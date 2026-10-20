"""
backend/app/services/github/review_flow.py — the PR review behind the webhook's 202
==================================================================================
docs/plan.md §4.4. One ReviewJob (from backend/app/routers/github.py) runs:

  1. check run      POST check-runs, status in_progress, on the head SHA
  2. config         .traceability.yml at the head SHA; missing -> neutral "skipping"
  3. PR files       paginated; no configured spec / SRS / config change -> neutral
  4. artefacts      every configured spec at the merge base and at head ("added"
                    = no base); the SRS at head, written to a temp file and parsed
                    by src/srs (.docx / .pdf / .md / .txt)
  5. linked issue   GraphQL closingIssuesReferences + the closing-keyword regex on
                    the PR body; issue bodies -> acceptance criteria
  6. engine         src.review.review_endpoint_changes (the same rules as the CLI,
                    the GitHub Action and the simulated PR page)
  7. complete       success (PASS) / neutral (WARN, or BLOCK under fail_on: never)
                    / failure (fails under fail_on); summary table + explanations
  8. comment        one PR comment, found by the <!-- traceability-bot --> marker
                    and edited on every push
  9. persist        a PRReviewRun row (the React "Live reviews" tab)

Errors: a broken config / SRS / spec completes the check as failure with the
reason (the author can fix it and push). An unexpected crash also completes it
as failure, then re-raises so the webhook router forgets the delivery and a
manual redelivery retries. A delivery that already has a completed PRReviewRun
row is not reviewed again (idempotent across restarts).

CPU-bound work (SRS parsing, embeddings, LLM explanations) runs in a worker
thread so the event loop keeps answering webhooks.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml
from sqlalchemy import select

from app.core.config import settings
from app.database import AsyncSessionLocal, PRReviewRun
from app.routers.github import ReviewJob
from app.services.github.auth import GitHubAppAuth
from app.services.github.client import GitHubClient
from app.services.github.errors import GitHubError

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import CONFIG_ALT_FILENAMES, CONFIG_FILENAME, ConfigError, TraceabilityConfig, load_config_text  # noqa: E402
from src.parser import extract_endpoints_from_spec  # noqa: E402
from src.review import PullRequestInfo, ReviewResult, Verdict, diff_endpoints, review_endpoint_changes  # noqa: E402
from src.review.intent import find_linked_issues, parse_acceptance_criteria  # noqa: E402
from src.srs import SRSExtractionError, extract_requirements_with_report  # noqa: E402
from src.srs.readers.docx_reader import guard_docx_archive  # noqa: E402

logger = logging.getLogger(__name__)

COMMENT_MARKER = "<!-- traceability-bot -->"
MAX_LINKED_ISSUES = 5

SKIPPED, ERROR = "SKIPPED", "ERROR"


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

class SkipReview(Exception):
    """Nothing to review: the check completes as neutral."""

    def __init__(self, title: str, summary: str):
        super().__init__(title)
        self.title, self.summary = title, summary


class ReviewInputError(Exception):
    """The repo's config, SRS or spec cannot be used: the check fails with the reason."""

    def __init__(self, title: str, summary: str):
        super().__init__(f"{title}: {summary}")
        self.title, self.summary = title, summary


@dataclass
class ReviewOutcome:
    verdict: str                           # PASS | WARN | BLOCK | SKIPPED | ERROR
    conclusion: str                        # check-run conclusion: success | neutral | failure
    title: str
    summary: str
    text: Optional[str] = None
    result: Optional[ReviewResult] = None
    linked_issues: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


def conclusion_for(result: ReviewResult, cfg: TraceabilityConfig) -> str:
    if result.fails(cfg.fail_on):
        return "failure"
    return "success" if result.verdict is Verdict.PASS else "neutral"


def check_title(result: ReviewResult) -> str:
    if result.verdict is Verdict.BLOCK:
        n = len(result.regressed_requirements)
        return f"BLOCK: {n} requirement{'s' if n != 1 else ''} regressed"
    if result.verdict is Verdict.WARN:
        coverage = f" ({result.intent.coverage:.0%} covered)" if result.intent is not None else ""
        return f"WARN: the linked issue is only partly delivered{coverage}"
    n = len(result.diff.changes)
    return f"PASS: no requirement regressed ({n} endpoint change{'s' if n != 1 else ''})"


def check_summary(result: ReviewResult, cfg: TraceabilityConfig, *, srs_path: str, n_requirements: int,
                  spec_paths: Sequence[str], base_sha: str, head_sha: str,
                  issues: Sequence[Dict[str, Any]], conclusion: str) -> str:
    lines = [f"**{result.summary}**", ""]
    if result.violations:
        lines += ["| Requirement | Endpoint | Change | Score base → head | Δ | Severity |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| `{v.requirement_id}` | `{v.endpoint_id}` | {v.change_status} | "
                  f"{v.score_v1:.3f} → {v.score_v2:.3f} | {v.delta:+.3f} | {v.severity} |"
                  for v in result.violations]
    elif result.diff.changes:
        lines += ["No requirement lost alignment. Changed endpoints:", ""]
        lines += [f"- `{c.endpoint_id}` ({c.status})" for c in result.diff.changes]
    else:
        lines.append("No endpoint changed between the base and head specification.")
    lines.append("")

    if result.intent is not None:
        lines.append(f"**Linked-issue intent:** {result.intent.summary}")
        lines += [f"- `{cid}` is not delivered by any added or modified endpoint"
                  for cid in result.intent.unmet_context_ids]
        lines.append("")
    if issues:
        lines.append("**Linked issues:** " + ", ".join(f"[{_issue_label(i)}]({i.get('url') or '#'}) "
                                                        f"{i.get('title', '')}".strip() for i in issues))
        lines.append("")
    if result.verdict is Verdict.BLOCK and conclusion != "failure":
        lines += [f"> `fail_on: {cfg.fail_on.value}` in .traceability.yml: reported, not blocking.", ""]

    lines.append(f"<sub>SRS `{srs_path}` ({n_requirements} requirements) · spec "
                 f"{', '.join(f'`{p}`' for p in spec_paths)} · base `{base_sha[:7]}` → head `{head_sha[:7]}` · "
                 f"fail_on `{cfg.fail_on.value}`</sub>")
    return "\n".join(lines)


def check_text(result: ReviewResult) -> Optional[str]:
    """Per-violation explanations (LLM or template)."""
    parts = []
    for v in result.violations:
        if v.explanation:
            parts.append(f"### `{v.requirement_id}` · `{v.endpoint_id}` ({v.severity})\n\n"
                         f"> {v.requirement_text}\n\n{v.explanation}")
    return "\n\n---\n\n".join(parts) or None


def comment_body(job: ReviewJob, outcome: ReviewOutcome, check_url: Optional[str]) -> str:
    link = f"[{settings.GITHUB_CHECK_NAME}]({check_url})" if check_url else settings.GITHUB_CHECK_NAME
    footer = f"\n\n<sub>Check run: {link} · head `{job.head_sha[:7]}` · re-reviewed on every push</sub>"
    if outcome.result is not None:
        return f"{COMMENT_MARKER}\n{outcome.result.markdown}{footer}"
    return (f"{COMMENT_MARKER}\n## \U0001f916 AI Compliance Bot — Requirements Traceability Review\n\n"
            f"**{outcome.title}**\n\n{outcome.summary}{footer}")


def _issue_label(issue: Dict[str, Any]) -> str:
    return f"{issue['repository']}#{issue['number']}" if issue.get("cross_repo") else f"#{issue['number']}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_path(path: str) -> str:
    """Config path -> repository path ("./docs\\SRS.docx" -> "docs/SRS.docx")."""
    return PurePosixPath(str(path).replace("\\", "/").lstrip("/")).as_posix()


def parse_spec(text: str, path: str) -> Dict[str, Any]:
    try:
        spec = json.loads(text) if path.lower().endswith(".json") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ReviewInputError("Invalid OpenAPI spec", f"`{path}` is not valid JSON/YAML: {exc}") from exc
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise ReviewInputError("Invalid OpenAPI spec", f"`{path}` has no `paths` section.")
    return spec


def intent_units(issues: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Acceptance criteria of every linked issue (the title stands in for an empty body)."""
    units: List[Dict[str, str]] = []
    for issue in issues:
        prefix = f"ISSUE{issue['number']}"
        found = parse_acceptance_criteria(issue.get("body") or "", prefix=prefix)
        if not found and (issue.get("title") or "").strip():
            title = issue["title"].strip()
            found = [{"id": f"{prefix}-AC1", "title": title, "text": title}]
        units.extend(found)
    return units


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def _default_embedder():
    from app.services.pipeline import get_embedder
    return get_embedder()


def _default_explainer():
    from src.explainer import ComplianceExplainer
    return ComplianceExplainer()          # template text when no LLM key is set


class ReviewFlow:
    """Callable review handler: `set_review_handler(ReviewFlow(auth))`."""

    def __init__(
        self,
        auth: GitHubAppAuth,
        *,
        http: Any = None,                                   # shared httpx.AsyncClient (tests: MockTransport)
        check_name: Optional[str] = None,
        embedder_factory: Callable[[], Any] = _default_embedder,
        explainer_factory: Optional[Callable[[], Any]] = _default_explainer,
        session_factory: Callable[[], Any] = AsyncSessionLocal,
        client_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.auth = auth
        self.http = http
        self.check_name = check_name or settings.GITHUB_CHECK_NAME
        self.embedder_factory = embedder_factory
        self.explainer_factory = explainer_factory
        self.session_factory = session_factory
        self.client_kwargs = client_kwargs or {}

    async def __call__(self, job: ReviewJob) -> Optional[ReviewOutcome]:
        if await self._already_reviewed(job):
            logger.info("Delivery %s for %s#%s was already reviewed; skipping", job.delivery_id,
                        job.full_name, job.pr_number)
            return None

        crash: Optional[BaseException] = None
        comment_url: Optional[str] = None
        async with GitHubClient(self.auth, job.installation_id, http=self.http, **self.client_kwargs) as gh:
            if job.trigger.startswith("check_run") or not job.pr_title:
                job = await self._with_pr_details(gh, job)

            # 1. check run
            check = await gh.create_check_run(
                job.owner, job.repo, name=self.check_name, head_sha=job.head_sha, status="in_progress",
                external_id=job.delivery_id or None, started_at=_utcnow_iso(),
                output={"title": "Reviewing the specification change…",
                        "summary": "Scoring the changed endpoints against the SRS requirement baseline."},
            )
            check_id, check_url = int(check["id"]), check.get("html_url")

            # 2-6. review
            try:
                outcome = await self._review(gh, job)
            except SkipReview as skip:
                outcome = ReviewOutcome(SKIPPED, "neutral", skip.title, skip.summary)
            except ReviewInputError as bad:
                outcome = ReviewOutcome(ERROR, "failure", bad.title, bad.summary, error=str(bad))
            except Exception as exc:  # noqa: BLE001 — report on the PR, then re-raise below
                logger.exception("Review of %s#%s @ %s crashed", job.full_name, job.pr_number, job.head_sha[:7])
                crash = exc
                outcome = ReviewOutcome(
                    ERROR, "failure", f"Review error: {type(exc).__name__}",
                    f"The traceability review could not run:\n\n```\n{str(exc)[:2000]}\n```\n\n"
                    "Use **Re-run** on this check once the problem is fixed.",
                    error=f"{type(exc).__name__}: {exc}",
                )

            # 7. complete the check run
            await gh.update_check_run(
                job.owner, job.repo, check_id, conclusion=outcome.conclusion, completed_at=_utcnow_iso(),
                output={"title": outcome.title, "summary": outcome.summary, "text": outcome.text},
            )

            # 8. PR comment: always for a real review; a skip / error only refreshes an earlier comment
            try:
                comment = await gh.upsert_comment(
                    job.owner, job.repo, job.pr_number, COMMENT_MARKER, comment_body(job, outcome, check_url),
                    create=outcome.result is not None,
                )
                comment_url = (comment or {}).get("html_url")
            except GitHubError as exc:
                logger.warning("Could not comment on %s#%s: %s", job.full_name, job.pr_number, exc)

        # 9. persist
        await self._persist(job, outcome, check_id, check_url, comment_url)
        logger.info("Reviewed %s#%s @ %s: %s (%s)", job.full_name, job.pr_number, job.head_sha[:7],
                    outcome.verdict, outcome.conclusion)
        if crash is not None:
            raise crash                               # the router forgets the delivery -> redelivery retries
        return outcome

    # -- steps -----------------------------------------------------------------

    async def _with_pr_details(self, gh: GitHubClient, job: ReviewJob) -> ReviewJob:
        """check_run.rerequested payloads carry no PR title / body / author."""
        try:
            pr = await gh.get_pull_request(job.owner, job.repo, job.pr_number)
        except GitHubError as exc:
            logger.warning("Could not load %s#%s: %s", job.full_name, job.pr_number, exc)
            return job
        return dataclasses.replace(
            job, pr_title=pr.get("title") or "", pr_body=pr.get("body") or "",
            pr_author=(pr.get("user") or {}).get("login", ""), pr_url=pr.get("html_url", ""),
            base_sha=(pr.get("base") or {}).get("sha") or job.base_sha,
            base_ref=(pr.get("base") or {}).get("ref") or job.base_ref,
            head_ref=(pr.get("head") or {}).get("ref") or job.head_ref,
        )

    async def _review(self, gh: GitHubClient, job: ReviewJob) -> ReviewOutcome:
        cfg, cfg_path = await self._load_config(gh, job)

        files = await gh.list_pull_request_files(job.owner, job.repo, job.pr_number)
        by_name = {f["filename"]: f for f in files}
        touched_names = set(by_name) | {f["previous_filename"] for f in files if f.get("previous_filename")}
        spec_paths = [repo_path(p) for p in cfg.openapi]
        srs_path = repo_path(cfg.srs)
        watched = [*spec_paths, srs_path, cfg_path]
        if not touched_names & set(watched):
            raise SkipReview("No specification change",
                             "This pull request does not change "
                             + ", ".join(f"`{p}`" for p in dict.fromkeys(watched)) + ", so there is nothing to review.")

        base_sha = await self._merge_base(gh, job)
        base_eps, head_eps = await self._endpoints(gh, job, spec_paths, by_name, base_sha)
        reqs = await self._requirements(gh, job, srs_path, cfg)
        issues = await self._linked_issues(gh, job)
        intent = intent_units(issues)

        pr = PullRequestInfo(number=job.pr_number, title=job.pr_title, author=job.pr_author,
                             branch=job.head_ref, url=job.pr_url)
        result = await asyncio.to_thread(self._run_engine, reqs, base_eps, head_eps, cfg, intent, pr)
        conclusion = conclusion_for(result, cfg)
        return ReviewOutcome(
            verdict=result.verdict.value, conclusion=conclusion, title=check_title(result),
            summary=check_summary(result, cfg, srs_path=srs_path, n_requirements=len(reqs), spec_paths=spec_paths,
                                  base_sha=base_sha, head_sha=job.head_sha, issues=issues, conclusion=conclusion),
            text=check_text(result), result=result, linked_issues=issues,
        )

    async def _load_config(self, gh: GitHubClient, job: ReviewJob) -> Tuple[TraceabilityConfig, str]:
        for name in (CONFIG_FILENAME, *CONFIG_ALT_FILENAMES):
            text = await gh.get_text(job.owner, job.repo, name, job.head_sha)
            if text is not None:
                try:
                    return load_config_text(text, source=name, env=None), name
                except ConfigError as exc:
                    raise ReviewInputError(f"Invalid {name}", f"```\n{exc}\n```") from exc
        raise SkipReview(f"No {CONFIG_FILENAME}, skipping",
                         f"Add a `{CONFIG_FILENAME}` at the repository root naming the SRS and the OpenAPI spec(s) "
                         "to enable the requirements traceability check:\n\n"
                         "```yaml\nversion: 1\nsrs: docs/SRS.docx\nopenapi: openapi.yaml\nfail_on: block\n```")

    async def _merge_base(self, gh: GitHubClient, job: ReviewJob) -> str:
        try:
            return await gh.merge_base(job.owner, job.repo, job.base_sha, job.head_sha) or job.base_sha
        except GitHubError as exc:
            logger.warning("compare %s...%s failed (%s); diffing against the base tip", job.base_sha[:7],
                           job.head_sha[:7], exc)
            return job.base_sha

    async def _endpoints(self, gh: GitHubClient, job: ReviewJob, spec_paths: Sequence[str],
                         by_name: Dict[str, Dict[str, Any]], base_sha: str
                         ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        base_eps: List[Dict[str, Any]] = []
        head_eps: List[Dict[str, Any]] = []
        found_any = False
        for path in spec_paths:
            change = by_name.get(path)
            head_text = await gh.get_text(job.owner, job.repo, path, job.head_sha)
            if change is None:
                base_text = head_text                      # untouched by this PR: same file on both sides
            elif change.get("status") == "added":
                base_text = None
            else:
                base_path = change.get("previous_filename") or path
                base_text = await gh.get_text(job.owner, job.repo, base_path, base_sha)
            for text, sha, out in ((base_text, base_sha, base_eps), (head_text, job.head_sha, head_eps)):
                if text is None:
                    continue
                found_any = True
                spec = parse_spec(text, path)
                try:
                    out.extend(extract_endpoints_from_spec(spec, f"{path}@{sha[:7]}"))
                except ValueError as exc:
                    raise ReviewInputError("Invalid OpenAPI spec", f"`{path}` @ `{sha[:7]}`: {exc}") from exc
        if not found_any:
            raise ReviewInputError("OpenAPI spec not found",
                                   "None of " + ", ".join(f"`{p}`" for p in spec_paths)
                                   + f" exists at `{base_sha[:7]}` or `{job.head_sha[:7]}`. Check `openapi:` in "
                                   f"{CONFIG_FILENAME}.")
        return base_eps, head_eps

    async def _requirements(self, gh: GitHubClient, job: ReviewJob, srs_path: str,
                            cfg: TraceabilityConfig) -> List[Dict[str, Any]]:
        data = await gh.get_content(job.owner, job.repo, srs_path, job.head_sha)
        if data is None:
            raise ReviewInputError("SRS not found", f"`{srs_path}` does not exist at `{job.head_sha[:7]}`. "
                                                    f"Check `srs:` in {CONFIG_FILENAME}.")
        limit = settings.MAX_UPLOAD_MB * 1024 * 1024
        if len(data) > limit:
            raise ReviewInputError("SRS too large", f"`{srs_path}` is {len(data) / 1e6:.1f} MB; "
                                                    f"the limit is {settings.MAX_UPLOAD_MB} MB.")
        try:
            reqs = await asyncio.to_thread(_extract_srs_bytes, data, srs_path, cfg)
        except SRSExtractionError as exc:
            raise ReviewInputError("Could not read the SRS", f"`{srs_path}`: {exc.message}") from exc
        if not reqs:
            raise ReviewInputError("No requirements in the SRS", f"`{srs_path}` yielded no requirements.")
        return reqs

    async def _linked_issues(self, gh: GitHubClient, job: ReviewJob) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        here = job.full_name.lower()
        try:
            for n in await gh.closing_issues(job.owner, job.repo, job.pr_number):
                repo_name = n.get("repository") or job.full_name
                issues.append({**n, "repository": repo_name, "cross_repo": repo_name.lower() != here})
        except GitHubError as exc:
            logger.warning("closingIssuesReferences failed for %s#%s: %s", job.full_name, job.pr_number, exc)

        seen = {(i["repository"].lower(), int(i["number"])) for i in issues}
        for ref in find_linked_issues(job.pr_body):
            repo_name = ref.repo or job.full_name
            if (repo_name.lower(), ref.number) in seen or len(issues) >= MAX_LINKED_ISSUES:
                continue
            seen.add((repo_name.lower(), ref.number))
            owner, name = repo_name.split("/", 1)
            try:
                data = await gh.get_issue(owner, name, ref.number)
            except GitHubError as exc:
                logger.info("Linked issue %s not readable: %s", ref.label(), exc)
                continue
            if not data or data.get("pull_request"):     # the issues API also returns PRs
                continue
            issues.append({"number": ref.number, "title": data.get("title", ""), "body": data.get("body") or "",
                           "url": data.get("html_url", ""), "repository": repo_name,
                           "cross_repo": repo_name.lower() != here})
        return issues[:MAX_LINKED_ISSUES]

    def _run_engine(self, reqs, base_eps, head_eps, cfg: TraceabilityConfig, intent, pr) -> ReviewResult:
        diff = diff_endpoints(base_eps, head_eps)
        explainer = self.explainer_factory() if self.explainer_factory is not None else None
        return review_endpoint_changes(
            reqs, diff, thresholds=cfg.thresholds, embedder=self.embedder_factory(),
            explainer=explainer, intent=intent or None, pr=pr,
        )

    # -- persistence -------------------------------------------------------------

    async def _already_reviewed(self, job: ReviewJob) -> bool:
        if not job.delivery_id:
            return False
        try:
            async with self.session_factory() as db:
                row = (await db.execute(select(PRReviewRun.id).where(
                    PRReviewRun.delivery_id == job.delivery_id, PRReviewRun.repo == job.full_name,
                    PRReviewRun.pr_number == job.pr_number, PRReviewRun.verdict != ERROR,
                ))).first()
            return row is not None
        except Exception:  # noqa: BLE001 — never skip a review because the DB is unhappy
            logger.exception("PRReviewRun lookup failed; reviewing anyway")
            return False

    async def _persist(self, job: ReviewJob, outcome: ReviewOutcome, check_id: int,
                       check_url: Optional[str], comment_url: Optional[str]) -> None:
        report = None
        if outcome.result is not None:
            report = outcome.result.to_dict()
            report.pop("markdown", None)
        values = dict(
            installation_id=job.installation_id, repo=job.full_name, pr_number=job.pr_number,
            pr_title=job.pr_title[:512] or None, pr_url=job.pr_url or None, pr_author=job.pr_author or None,
            head_sha=job.head_sha, base_sha=job.base_sha, delivery_id=job.delivery_id or None,
            trigger=job.trigger, verdict=outcome.verdict, conclusion=outcome.conclusion,
            title=outcome.title[:255], summary=outcome.summary,
            violations_json=[v.to_dict() for v in outcome.result.violations] if outcome.result else [],
            report_json=report,
            markdown=outcome.result.markdown if outcome.result else None,
            linked_issues=[{k: i.get(k) for k in ("number", "title", "url", "repository")}
                           for i in outcome.linked_issues],
            check_run_id=check_id, check_run_url=check_url, comment_url=comment_url, error=outcome.error,
        )
        try:
            async with self.session_factory() as db:
                row = None
                if job.delivery_id:                     # a retried delivery replaces its ERROR row
                    row = (await db.execute(select(PRReviewRun).where(
                        PRReviewRun.delivery_id == job.delivery_id, PRReviewRun.repo == job.full_name,
                        PRReviewRun.pr_number == job.pr_number,
                    ))).scalar_one_or_none()
                if row is None:
                    db.add(PRReviewRun(**values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.created_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception:  # noqa: BLE001 — GitHub already has the result; losing the row is not fatal
            logger.exception("Could not store the PRReviewRun for %s#%s", job.full_name, job.pr_number)


def _extract_srs_bytes(data: bytes, srs_path: str, cfg: TraceabilityConfig) -> List[Dict[str, Any]]:
    """The SRS readers work on files: write the blob to a temp file with the same name."""
    with tempfile.TemporaryDirectory(prefix="trace_srs_") as tmp:
        path = Path(tmp) / (PurePosixPath(srs_path).name or "srs.txt")
        path.write_bytes(data)
        if path.suffix.lower() == ".docx":
            guard_docx_archive(path, settings.MAX_DOCX_UNCOMPRESSED_MB * 1024 * 1024)
        reqs, report = extract_requirements_with_report(path, cfg.extraction_options(), display_name=srs_path)
    logger.info("SRS %s: %s", srs_path, report.summary())
    return reqs
