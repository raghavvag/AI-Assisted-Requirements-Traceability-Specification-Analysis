"""
backend/app/routers/github.py — GitHub App webhook
==================================================
POST /api/github/webhook

  1. HMAC-SHA256 of the RAW body with GITHUB_WEBHOOK_SECRET must equal
     X-Hub-Signature-256 (constant-time compare) -> otherwise 401.
     No secret configured -> 503 (the app is not set up; never accept unsigned).
  2. X-GitHub-Delivery is de-duplicated (GitHub retries, manual redeliveries).
  3. Routing
       ping                                   200 pong
       installation, installation_repositories 200 logged
       pull_request opened / synchronize / reopened / edited (body or base changed)
                                              202 accepted -> review job
       check_run rerequested (our own check)   202 accepted -> review job
       anything else                           200 ignored
  4. 202 is returned at once; the review runs in BackgroundTasks, because
     GitHub gives a webhook 10 seconds before it marks the delivery failed.

The review itself (check run, specs, SRS, engine, comment) is the job handler,
app.services.github.review_flow.ReviewFlow, installed with `set_review_handler`
at startup when the app is configured. Without it the default handler only logs.

Authentication is the HMAC signature, not a user JWT: GitHub calls this endpoint.

GET /api/github/reviews           PRReviewRun rows, newest first (JWT-protected;
                                  ?repo=owner/name&pr_number=N&limit=50)
GET /api/github/reviews/{id}      one run incl. the PR comment Markdown
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_current_user
from app.database import PRReviewRun, User, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/github", tags=["github"])

REVIEWED_PR_ACTIONS = {"opened", "synchronize", "reopened", "edited"}


# ---------------------------------------------------------------------------
# Review jobs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewJob:
    """Everything the review flow needs to fetch and review one PR head."""

    delivery_id: str
    trigger: str                     # e.g. "pull_request.opened", "check_run.rerequested"
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    head_ref: str = ""
    base_ref: str = ""
    pr_title: str = ""
    pr_body: str = ""
    pr_author: str = ""
    pr_url: str = ""
    check_run_id: Optional[int] = None   # the re-requested check (a re-run creates a fresh check run)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("pr_body", None)
        return d


ReviewHandler = Callable[[ReviewJob], Awaitable[None]]


async def _review_flow_not_wired(job: ReviewJob) -> None:
    logger.warning("Review job for %s#%s @ %s received, but the review flow (T3.5) is not wired yet.",
                   job.full_name, job.pr_number, job.head_sha[:7])


_review_handler: ReviewHandler = _review_flow_not_wired


def set_review_handler(handler: ReviewHandler) -> None:
    """Install the coroutine that performs a review (called from app startup)."""
    global _review_handler
    _review_handler = handler


def get_review_handler() -> ReviewHandler:
    """FastAPI dependency (overridden in tests)."""
    return _review_handler


# ---------------------------------------------------------------------------
# Delivery de-duplication
# ---------------------------------------------------------------------------

class DeliveryCache:
    """Bounded, TTL'd set of delivery ids seen by this process.

    Per-process: with several workers a duplicate can slip through to a second
    worker. That is harmless here because a review is idempotent per head SHA
    (one check run, one upserted comment). Persisted de-duplication comes with
    the PRReviewRun table (T3.7)."""

    def __init__(self, maxsize: int = 10_000, ttl_seconds: float = 24 * 3600,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.maxsize, self.ttl, self._clock = maxsize, ttl_seconds, clock
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, delivery_id: str) -> bool:
        """True if this id was already seen (duplicate); records it otherwise."""
        now = self._clock()
        with self._lock:
            while self._seen:
                oldest, ts = next(iter(self._seen.items()))
                if now - ts <= self.ttl and len(self._seen) < self.maxsize:
                    break
                self._seen.popitem(last=False)
            if delivery_id in self._seen:
                return True
            self._seen[delivery_id] = now
            return False

    def forget(self, delivery_id: str) -> None:
        """Allow a redelivery after the background job failed."""
        with self._lock:
            self._seen.pop(delivery_id, None)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


deliveries = DeliveryCache()


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

def compute_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: Optional[str]) -> bool:
    """Constant-time check of X-Hub-Signature-256 over the raw request body."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(compute_signature(secret, body), header.strip())


# ---------------------------------------------------------------------------
# Payload -> job
# ---------------------------------------------------------------------------

def _repo_parts(payload: Dict[str, Any]) -> Optional[tuple]:
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    name = repo.get("name")
    return (owner, name) if owner and name else None


def _installation_id(payload: Dict[str, Any]) -> Optional[int]:
    inst = payload.get("installation") or {}
    try:
        return int(inst["id"])
    except (KeyError, TypeError, ValueError):
        return None


def job_from_pull_request(payload: Dict[str, Any], delivery_id: str) -> Optional[ReviewJob]:
    pr = payload.get("pull_request") or {}
    parts, iid = _repo_parts(payload), _installation_id(payload)
    if not pr or not parts or iid is None:
        return None
    return ReviewJob(
        delivery_id=delivery_id, trigger=f"pull_request.{payload.get('action')}",
        installation_id=iid, owner=parts[0], repo=parts[1], pr_number=int(pr["number"]),
        head_sha=pr["head"]["sha"], base_sha=pr["base"]["sha"],
        head_ref=pr["head"].get("ref", ""), base_ref=pr["base"].get("ref", ""),
        pr_title=pr.get("title") or "", pr_body=pr.get("body") or "",
        pr_author=(pr.get("user") or {}).get("login", ""), pr_url=pr.get("html_url", ""),
    )


def jobs_from_check_run(payload: Dict[str, Any], delivery_id: str) -> List[ReviewJob]:
    cr = payload.get("check_run") or {}
    parts, iid = _repo_parts(payload), _installation_id(payload)
    if not cr or not parts or iid is None:
        return []
    jobs = []
    for pr in cr.get("pull_requests") or []:
        jobs.append(ReviewJob(
            delivery_id=delivery_id, trigger="check_run.rerequested",
            installation_id=iid, owner=parts[0], repo=parts[1], pr_number=int(pr["number"]),
            head_sha=cr.get("head_sha") or pr["head"]["sha"], base_sha=pr["base"]["sha"],
            head_ref=pr["head"].get("ref", ""), base_ref=pr["base"].get("ref", ""),
            check_run_id=int(cr["id"]) if cr.get("id") is not None else None,
        ))
    return jobs


def _is_our_check(cr: Dict[str, Any]) -> bool:
    app_id = str((cr.get("app") or {}).get("id", ""))
    if settings.GITHUB_APP_ID.strip():
        return app_id == settings.GITHUB_APP_ID.strip()
    return cr.get("name") == settings.GITHUB_CHECK_NAME


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _reply(code: int, **body: Any) -> JSONResponse:
    return JSONResponse(status_code=code, content=body)


async def _run_job(handler: ReviewHandler, job: ReviewJob) -> None:
    try:
        await handler(job)
    except Exception:  # noqa: BLE001 — a background job must never take the worker down
        logger.exception("Review job failed for %s#%s (delivery %s)", job.full_name, job.pr_number, job.delivery_id)
        deliveries.forget(job.delivery_id)            # let a manual redelivery retry it


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED, summary="GitHub App webhook receiver")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
    handler: ReviewHandler = Depends(get_review_handler),
):
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "GitHub App is not configured (GITHUB_WEBHOOK_SECRET is empty).")

    body = await request.body()                        # raw bytes: the signature covers exactly these
    if not verify_signature(secret, body, x_hub_signature_256):
        logger.warning("Webhook rejected: bad or missing signature (delivery %s)", x_github_delivery)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature.")

    if not x_github_event:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing X-GitHub-Event header.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON (set the webhook content type to "
                                                         "application/json).")
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook payload must be a JSON object.")

    delivery = x_github_delivery or ""
    if delivery and deliveries.check_and_add(delivery):
        logger.info("Duplicate delivery %s (%s) ignored", delivery, x_github_event)
        return _reply(200, status="duplicate", delivery=delivery)

    event, action = x_github_event, payload.get("action", "")

    if event == "ping":
        logger.info("GitHub ping: hook %s, zen %r", payload.get("hook_id"), payload.get("zen"))
        return _reply(200, status="pong", hook_id=payload.get("hook_id"), zen=payload.get("zen"))

    if event in ("installation", "installation_repositories"):
        inst = payload.get("installation") or {}
        account = (inst.get("account") or {}).get("login", "?")
        repos = [r.get("full_name") for r in (payload.get("repositories") or payload.get("repositories_added") or [])]
        logger.info("GitHub %s.%s: installation %s on %s, repositories %s",
                    event, action, inst.get("id"), account, repos)
        return _reply(200, status="logged", event=event, action=action, installation_id=inst.get("id"))

    if event == "pull_request":
        if action not in REVIEWED_PR_ACTIONS:
            return _reply(200, status="ignored", reason=f"pull_request.{action} is not reviewed")
        if action == "edited" and not ({"body", "base"} & set((payload.get("changes") or {}).keys())):
            return _reply(200, status="ignored", reason="edit did not change the PR body or base branch")
        if (payload.get("pull_request") or {}).get("state") == "closed":
            return _reply(200, status="ignored", reason="pull request is closed")
        job = job_from_pull_request(payload, delivery)
        if job is None:
            return _reply(200, status="ignored", reason="payload lacks repository, installation or PR data")
        background.add_task(_run_job, handler, job)
        logger.info("Queued review %s#%s @ %s (%s)", job.full_name, job.pr_number, job.head_sha[:7], job.trigger)
        return _reply(202, status="accepted", jobs=[job.to_dict()])

    if event == "check_run":
        cr = payload.get("check_run") or {}
        if action != "rerequested":
            return _reply(200, status="ignored", reason=f"check_run.{action} is not handled")
        if not _is_our_check(cr):
            return _reply(200, status="ignored", reason="check run belongs to another app")
        jobs = jobs_from_check_run(payload, delivery)
        if not jobs:
            return _reply(200, status="ignored", reason="check run is not attached to a pull request")
        for job in jobs:
            background.add_task(_run_job, handler, job)
        logger.info("Queued %d re-run(s) for %s", len(jobs), jobs[0].full_name)
        return _reply(202, status="accepted", jobs=[j.to_dict() for j in jobs])

    return _reply(200, status="ignored", reason=f"event {event} is not handled")


# ---------------------------------------------------------------------------
# Live reviews (React "Live reviews" tab)
# ---------------------------------------------------------------------------

def _utc_iso(value) -> Optional[str]:
    """SQLite returns naive datetimes; they are UTC. Say so, or browsers read them as local time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _run_summary(run: PRReviewRun) -> Dict[str, Any]:
    violations = run.violations_json or []
    return {
        "id": run.id, "repo": run.repo, "pr_number": run.pr_number, "pr_title": run.pr_title,
        "pr_url": run.pr_url or f"https://github.com/{run.repo}/pull/{run.pr_number}",
        "pr_author": run.pr_author, "head_sha": run.head_sha, "base_sha": run.base_sha,
        "trigger": run.trigger, "verdict": run.verdict, "conclusion": run.conclusion,
        "title": run.title, "violations": violations,
        "linked_issues": run.linked_issues or [], "check_run_url": run.check_run_url,
        "comment_url": run.comment_url, "error": run.error,
        "code_scan": bool((run.report_json or {}).get("code_changes"))
                     or any(v.get("artifact_kind", "endpoint") != "endpoint" for v in violations),
        "created_at": _utc_iso(run.created_at),
    }


@router.get("/reviews", summary="Live GitHub App reviews, newest first")
async def list_reviews(
    repo: Optional[str] = Query(None, description="owner/name"),
    pr_number: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Prototype: every signed-in user sees every installation's runs (docs/plan.md §4.8).
    q = select(PRReviewRun).order_by(PRReviewRun.created_at.desc(), PRReviewRun.id.desc()).limit(limit)
    if repo:
        q = q.where(PRReviewRun.repo == repo)
    if pr_number is not None:
        q = q.where(PRReviewRun.pr_number == pr_number)
    runs = (await db.execute(q)).scalars().all()
    return {"reviews": [_run_summary(r) for r in runs], "app_configured": settings.github_app_configured}


@router.get("/reviews/{review_id}", summary="One live review, including the PR comment")
async def get_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(PRReviewRun, review_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not found.")
    return {**_run_summary(run), "summary": run.summary, "markdown": run.markdown, "report": run.report_json}
