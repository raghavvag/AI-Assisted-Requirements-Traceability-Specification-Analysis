"""
test_github_app.py — GitHub App auth, API client, webhook and review flow (M3: T3.2-T3.9)
=======================================================================================
No network: GitHub is an httpx.MockTransport (an in-memory repo for the review
flow), the private key is generated here, the webhook runs through FastAPI's
TestClient, and the embedder is the deterministic fake from tests/fakes.py.
"""

import asyncio
import base64
import hashlib
import hmac
import json
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.main import app
from app.routers import github as gh_router
from app.services.github import (
    GitHubAPIError,
    GitHubAppAuth,
    GitHubAppConfigError,
    GitHubAuthError,
    GitHubClient,
    GitHubRateLimitError,
    create_app_jwt,
    load_private_key,
)
from app.services.github.client import graphql_url, normalise_check_output

APP_ID = "123456"
INSTALLATION = 987
NOW = 1_800_000_000.0                  # fixed clock


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pem, public


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """Routes requests to handlers; records every request and token exchange."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.requests: List[httpx.Request] = []
        self.exchanges = 0
        self.token_ttl = 3600
        self.exchange_status = 201
        self.routes: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def route(self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.routes[f"{method} {path}"] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/access_tokens"):
            self.exchanges += 1
            if self.exchange_status != 201:
                return httpx.Response(self.exchange_status, json={"message": "nope"})
            return httpx.Response(201, json={"token": f"ghs_token{self.exchanges}",
                                             "expires_at": _iso(self.clock() + self.token_ttl),
                                             "permissions": {"checks": "write"}})
        handler = self.routes.get(f"{request.method} {path}") or self.fallback
        if handler is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return handler(request)

    fallback: Optional[Callable[[httpx.Request], httpx.Response]] = None


class Clock:
    def __init__(self, t: float = NOW):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def world(rsa_key):
    clock = Clock()
    fake = FakeGitHub(clock)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="https://api.github.com")
    auth = GitHubAppAuth(APP_ID, rsa_key[0], http=http, clock=clock)
    sleeps: List[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock.t += s

    client = GitHubClient(auth, INSTALLATION, http=http, sleep=fake_sleep, clock=clock, max_wait=60)
    return {"fake": fake, "auth": auth, "client": client, "clock": clock, "sleeps": sleeps}


# ===========================================================================
# T3.2 configuration
# ===========================================================================

class TestSettings:
    def test_defaults(self):
        assert settings.GITHUB_CHECK_NAME == "Requirements Traceability"
        assert settings.GITHUB_API_URL == "https://api.github.com"

    def test_configured_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "GITHUB_APP_ID", "")
        assert settings.github_app_configured is False
        monkeypatch.setattr(settings, "GITHUB_APP_ID", APP_ID)
        monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----")
        monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "s")
        assert settings.github_app_configured is True

    def test_health_reports_github_config(self, monkeypatch):
        monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")
        cfg = TestClient(app).get("/api/health").json()["config"]
        assert cfg["github_webhook_secret_loaded"] is False and "github_app_configured" in cfg


# ===========================================================================
# T3.3 auth
# ===========================================================================

class TestAppJwt:
    def test_claims_and_signature(self, rsa_key):
        token = create_app_jwt(APP_ID, rsa_key[0], now=NOW)
        claims = jwt.decode(token, rsa_key[1], algorithms=["RS256"])
        assert claims == {"iat": int(NOW) - 60, "exp": int(NOW) + 540, "iss": int(APP_ID)}
        assert jwt.get_unverified_header(token)["alg"] == "RS256"

    def test_missing_app_id(self, rsa_key):
        with pytest.raises(GitHubAppConfigError):
            create_app_jwt("", rsa_key[0])

    def test_load_key_from_file_and_inline(self, rsa_key, tmp_path):
        pem_file = tmp_path / "app.pem"
        pem_file.write_text(rsa_key[0], encoding="utf-8")
        assert "PRIVATE KEY" in load_private_key(path=str(pem_file))
        escaped = rsa_key[0].strip().replace("\n", "\\n")              # how CI secrets usually look
        assert load_private_key(inline=escaped).strip() == rsa_key[0].strip()

    @pytest.mark.parametrize("kwargs,needle", [
        ({}, "GITHUB_APP_PRIVATE_KEY_PATH"),
        ({"path": "C:/definitely/missing.pem"}, "does not exist"),
        ({"inline": "not a key"}, "not a PEM"),
        ({"inline": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"}, "could not be loaded"),
    ])
    def test_bad_keys(self, kwargs, needle):
        with pytest.raises(GitHubAppConfigError, match=needle):
            load_private_key(**kwargs)

    def test_from_settings_requires_app_id(self, monkeypatch):
        monkeypatch.setattr(settings, "GITHUB_APP_ID", "")
        with pytest.raises(GitHubAppConfigError, match="GITHUB_APP_ID"):
            GitHubAppAuth.from_settings(settings)


class TestInstallationTokens:
    def test_exchange_uses_app_jwt(self, world, rsa_key):
        assert run(world["auth"].installation_token(INSTALLATION)) == "ghs_token1"
        req = world["fake"].requests[0]
        assert req.method == "POST" and req.url.path == f"/app/installations/{INSTALLATION}/access_tokens"
        bearer = req.headers["authorization"].removeprefix("Bearer ")
        assert jwt.decode(bearer, rsa_key[1], algorithms=["RS256"])["iss"] == int(APP_ID)
        assert req.headers["x-github-api-version"] == "2022-11-28"

    def test_cached_until_five_minutes_before_expiry(self, world):
        auth, fake, clock = world["auth"], world["fake"], world["clock"]
        run(auth.installation_token(INSTALLATION))
        clock.t += 3600 - 301                                         # 5 min + 1 s left
        assert run(auth.installation_token(INSTALLATION)) == "ghs_token1" and fake.exchanges == 1
        clock.t += 2                                                  # inside the 5-minute margin
        assert run(auth.installation_token(INSTALLATION)) == "ghs_token2" and fake.exchanges == 2

    def test_per_installation_and_invalidate(self, world):
        auth, fake = world["auth"], world["fake"]
        run(auth.installation_token(1))
        run(auth.installation_token(2))
        assert fake.exchanges == 2
        auth.invalidate(1)
        run(auth.installation_token(1))
        assert fake.exchanges == 3

    def test_concurrent_callers_share_one_exchange(self, world):
        async def many():
            return await asyncio.gather(*(world["auth"].installation_token(INSTALLATION) for _ in range(8)))
        assert set(run(many())) == {"ghs_token1"} and world["fake"].exchanges == 1

    @pytest.mark.parametrize("code,needle", [(401, "rejected the app JWT"), (404, "not found"), (500, "500")])
    def test_exchange_errors(self, world, code, needle):
        world["fake"].exchange_status = code
        with pytest.raises(GitHubAuthError, match=needle):
            run(world["auth"].installation_token(INSTALLATION))


# ===========================================================================
# T3.3 client
# ===========================================================================

def _json_response(data: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers or {})


class TestClientCalls:
    def test_auth_headers(self, world):
        world["fake"].route("GET", "/repos/o/r/pulls/5", lambda r: _json_response({"number": 5}))
        assert run(world["client"].get_pull_request("o", "r", 5))["number"] == 5
        req = world["fake"].requests[-1]
        assert req.headers["authorization"] == "Bearer ghs_token1"
        assert req.headers["accept"] == "application/vnd.github+json"

    def test_pr_files_paginated(self, world):
        fake = world["fake"]

        def files(req: httpx.Request) -> httpx.Response:
            if req.url.params.get("page") == "2":
                return _json_response([{"filename": "c.yaml"}])
            nxt = "https://api.github.com/repos/o/r/pulls/5/files?per_page=100&page=2"
            return _json_response([{"filename": "a.yaml"}, {"filename": "b.md"}],
                                  headers={"link": f'<{nxt}>; rel="next", <{nxt}>; rel="last"'})
        fake.route("GET", "/repos/o/r/pulls/5/files", files)
        names = [f["filename"] for f in run(world["client"].list_pull_request_files("o", "r", 5))]
        assert names == ["a.yaml", "b.md", "c.yaml"]
        assert fake.requests[1].url.params["per_page"] == "100"

    def test_get_content_base64(self, world):
        content = base64.b64encode(b"openapi: 3.0.0\npaths: {}\n").decode()
        world["fake"].route("GET", "/repos/o/r/contents/api/openapi.yaml", lambda r: _json_response(
            {"type": "file", "encoding": "base64", "size": 25, "content": content[:10] + "\n" + content[10:]}))
        assert run(world["client"].get_text("o", "r", "api/openapi.yaml", "abc123")) == "openapi: 3.0.0\npaths: {}\n"
        assert world["fake"].requests[-1].url.params["ref"] == "abc123"

    def test_get_content_missing_is_none(self, world):
        assert run(world["client"].get_content("o", "r", "nope.yaml", "main")) is None

    def test_get_content_directory_rejected(self, world):
        world["fake"].route("GET", "/repos/o/r/contents/docs", lambda r: _json_response([{"name": "a"}]))
        with pytest.raises(GitHubAPIError, match="directory"):
            run(world["client"].get_content("o", "r", "docs", "main"))

    def test_large_file_falls_back_to_raw(self, world):
        def big(req: httpx.Request) -> httpx.Response:
            if req.headers["accept"] == "application/vnd.github.raw+json":
                return httpx.Response(200, content=b"PK\x03\x04big docx bytes")
            return _json_response({"type": "file", "encoding": "none", "size": 2_000_000, "content": ""})
        world["fake"].route("GET", "/repos/o/r/contents/docs/SRS.docx", big)
        assert run(world["client"].get_content("o", "r", "docs/SRS.docx", "main")).startswith(b"PK")

    def test_check_runs(self, world):
        bodies = []

        def create(req):
            bodies.append(json.loads(req.content))
            return _json_response({"id": 77}, 201)

        def update(req):
            bodies.append(json.loads(req.content))
            return _json_response({"id": 77, "status": "completed"})
        world["fake"].route("POST", "/repos/o/r/check-runs", create)
        world["fake"].route("PATCH", "/repos/o/r/check-runs/77", update)
        cr = run(world["client"].create_check_run("o", "r", name="Requirements Traceability", head_sha="abc"))
        run(world["client"].update_check_run("o", "r", cr["id"], conclusion="failure",
                                             output={"title": "BLOCK", "summary": "x" * 70000}))
        assert bodies[0] == {"name": "Requirements Traceability", "head_sha": "abc", "status": "in_progress"}
        assert bodies[1]["status"] == "completed" and bodies[1]["conclusion"] == "failure"
        assert len(bodies[1]["output"]["summary"]) == 65535 and bodies[1]["output"]["summary"].endswith("(truncated)")

    def test_output_limits(self):
        out = normalise_check_output({"title": "t" * 300, "summary": "s", "annotations": [{}] * 60})
        assert len(out["title"]) == 255 and len(out["annotations"]) == 50

    def test_output_omits_null_text(self):
        # GitHub answers 422 to "text": null; PASS and skip outcomes have no text.
        out = normalise_check_output({"title": "PASS", "summary": "ok", "text": None})
        assert "text" not in out and out["summary"] == "ok"

    def test_upsert_comment(self, world):
        fake, calls = world["fake"], []
        existing = [{"id": 1, "body": "unrelated"}, {"id": 2, "body": "<!-- traceability-bot -->\nold"}]
        fake.route("GET", "/repos/o/r/issues/5/comments", lambda r: _json_response(existing))
        fake.route("PATCH", "/repos/o/r/issues/comments/2",
                   lambda r: calls.append(("patch", json.loads(r.content)["body"])) or _json_response({"id": 2}))
        fake.route("POST", "/repos/o/r/issues/5/comments",
                   lambda r: calls.append(("post", json.loads(r.content)["body"])) or _json_response({"id": 3}, 201))
        run(world["client"].upsert_comment("o", "r", 5, "<!-- traceability-bot -->", "new report"))
        assert calls == [("patch", "<!-- traceability-bot -->\nnew report")]
        existing.pop()
        run(world["client"].upsert_comment("o", "r", 5, "<!-- traceability-bot -->", "first report"))
        assert calls[-1][0] == "post"

    def test_closing_issues_graphql(self, world):
        seen = {}

        def gql(req):
            seen.update(json.loads(req.content))
            return _json_response({"data": {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": [
                {"number": 12, "title": "Support discount codes", "body": "- [ ] codes apply",
                 "url": "https://github.com/o/r/issues/12", "repository": {"nameWithOwner": "o/r"}}]}}}}})
        world["fake"].route("POST", "/graphql", gql)
        issues = run(world["client"].closing_issues("o", "r", 5))
        assert issues == [{"number": 12, "title": "Support discount codes", "body": "- [ ] codes apply",
                           "url": "https://github.com/o/r/issues/12", "repository": "o/r"}]
        assert seen["variables"] == {"owner": "o", "repo": "r", "number": 5}
        assert "closingIssuesReferences" in seen["query"]

    def test_graphql_errors_raise(self, world):
        world["fake"].route("POST", "/graphql", lambda r: _json_response({"errors": [{"message": "bad field"}]}))
        with pytest.raises(GitHubAPIError, match="bad field"):
            run(world["client"].graphql("{ viewer { login } }"))

    def test_graphql_url_for_enterprise(self):
        assert graphql_url("https://api.github.com") == "https://api.github.com/graphql"
        assert graphql_url("https://ghe.corp/api/v3") == "https://ghe.corp/api/graphql"


class TestResilience:
    def _sequence(self, world, path, responses):
        it = iter(responses)
        world["fake"].route("GET", path, lambda r: next(it))

    def test_primary_rate_limit_waits_until_reset(self, world):
        reset = str(int(NOW) + 5)
        self._sequence(world, "/repos/o/r/pulls/1", [
            _json_response({"message": "API rate limit exceeded"}, 403,
                           {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
            _json_response({"number": 1}),
        ])
        assert run(world["client"].get_pull_request("o", "r", 1))["number"] == 1
        assert world["sleeps"] == [6.0]

    def test_429_retry_after(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [
            _json_response({"message": "slow down"}, 429, {"retry-after": "2"}), _json_response({"number": 1})])
        run(world["client"].get_pull_request("o", "r", 1))
        assert world["sleeps"] == [2.0]

    def test_secondary_rate_limit_message(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [
            _json_response({"message": "You have exceeded a secondary rate limit."}, 403), _json_response({"number": 1})])
        run(world["client"].get_pull_request("o", "r", 1))
        assert world["sleeps"] == [60.0]

    def test_wait_longer_than_max_raises(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [
            _json_response({"message": "limit"}, 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 3600)})])
        with pytest.raises(GitHubRateLimitError) as exc:
            run(world["client"].get_pull_request("o", "r", 1))
        assert exc.value.retry_after > 3000 and world["sleeps"] == []

    def test_retries_exhausted(self, world):
        world["fake"].route("GET", "/repos/o/r/pulls/1",
                            lambda r: _json_response({"message": "slow"}, 429, {"retry-after": "1"}))
        with pytest.raises(GitHubRateLimitError):
            run(world["client"].get_pull_request("o", "r", 1))
        assert world["sleeps"] == [1.0, 1.0, 1.0]

    def test_permission_403_is_not_retried(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [
            _json_response({"message": "Resource not accessible by integration"}, 403)])
        with pytest.raises(GitHubAPIError) as exc:
            run(world["client"].get_pull_request("o", "r", 1))
        assert exc.value.status == 403 and not isinstance(exc.value, GitHubRateLimitError)
        assert world["sleeps"] == []

    def test_401_refreshes_token_once(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [_json_response({"message": "Bad credentials"}, 401),
                                                     _json_response({"number": 1})])
        run(world["client"].get_pull_request("o", "r", 1))
        assert world["fake"].exchanges == 2
        assert world["fake"].requests[-1].headers["authorization"] == "Bearer ghs_token2"

    def test_5xx_backoff(self, world):
        self._sequence(world, "/repos/o/r/pulls/1", [_json_response({}, 502), _json_response({}, 503),
                                                     _json_response({"number": 1})])
        run(world["client"].get_pull_request("o", "r", 1))
        assert world["sleeps"] == [1.0, 2.0]


# ===========================================================================
# T3.4 webhook
# ===========================================================================

SECRET = "test-webhook-secret"


def _pr_payload(action="opened", number=5, state="open", changes=None):
    p = {
        "action": action,
        "installation": {"id": INSTALLATION},
        "repository": {"name": "traceability-demo-store", "owner": {"login": "acme"}},
        "pull_request": {
            "number": number, "state": state, "title": "Drop transaction_id", "body": "Closes #12",
            "html_url": "https://github.com/acme/traceability-demo-store/pull/5",
            "user": {"login": "dev-alice"},
            "head": {"sha": "a" * 40, "ref": "feature/x"}, "base": {"sha": "b" * 40, "ref": "main"},
        },
    }
    if changes is not None:
        p["changes"] = changes
    return p


def _check_run_payload(app_id=APP_ID, action="rerequested", prs=True):
    return {
        "action": action, "installation": {"id": INSTALLATION},
        "repository": {"name": "traceability-demo-store", "owner": {"login": "acme"}},
        "check_run": {
            "id": 4242, "name": "Requirements Traceability", "head_sha": "c" * 40, "app": {"id": int(app_id)},
            "pull_requests": [{"number": 5, "head": {"sha": "c" * 40, "ref": "feature/x"},
                               "base": {"sha": "b" * 40, "ref": "main"}}] if prs else [],
        },
    }


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "GITHUB_APP_ID", APP_ID)
    gh_router.deliveries.clear()
    jobs: List[gh_router.ReviewJob] = []
    failing = {"on": False}

    async def handler(job):
        jobs.append(job)
        if failing["on"]:
            raise RuntimeError("review crashed")

    app.dependency_overrides[gh_router.get_review_handler] = lambda: handler
    client = TestClient(app)

    def post(event: str, payload: Any, delivery: str = "d-1", secret: str = SECRET, raw: Optional[bytes] = None,
             signature: Optional[str] = "auto"):
        body = raw if raw is not None else json.dumps(payload).encode()
        headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "Content-Type": "application/json"}
        if signature == "auto":
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return client.post("/api/github/webhook", content=body, headers=headers)

    yield {"post": post, "jobs": jobs, "failing": failing}
    app.dependency_overrides.pop(gh_router.get_review_handler, None)


class TestSignature:
    def test_valid(self, hook):
        assert hook["post"]("ping", {"zen": "hi", "hook_id": 1}).status_code == 200

    @pytest.mark.parametrize("kwargs", [
        {"secret": "wrong-secret"},
        {"signature": None},
        {"signature": "sha1=abcdef"},
        {"signature": "sha256=" + "0" * 64},
    ])
    def test_rejected(self, hook, kwargs):
        r = hook["post"]("pull_request", _pr_payload(), **kwargs)
        assert r.status_code == 401 and hook["jobs"] == []

    def test_signature_covers_raw_bytes(self, hook):
        body = json.dumps(_pr_payload()).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        reformatted = json.dumps(_pr_payload(), indent=2).encode()            # same JSON, different bytes
        assert hook["post"]("pull_request", None, raw=reformatted, signature=sig).status_code == 401

    def test_unconfigured_secret_is_503(self, hook, monkeypatch):
        monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")
        assert hook["post"]("ping", {}).status_code == 503

    def test_verify_signature_helper(self):
        body = b'{"a":1}'
        good = gh_router.compute_signature("k", body)
        assert gh_router.verify_signature("k", body, good)
        assert not gh_router.verify_signature("k", body + b" ", good)
        assert not gh_router.verify_signature("", body, good)


class TestRouting:
    def test_ping(self, hook):
        r = hook["post"]("ping", {"zen": "Keep it logically awesome.", "hook_id": 9})
        assert r.status_code == 200 and r.json()["status"] == "pong" and r.json()["hook_id"] == 9

    def test_installation_logged(self, hook):
        r = hook["post"]("installation", {"action": "created", "installation": {"id": INSTALLATION,
                         "account": {"login": "acme"}}, "repositories": [{"full_name": "acme/demo"}]})
        assert r.status_code == 200 and r.json()["status"] == "logged" and hook["jobs"] == []

    @pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
    def test_pull_request_accepted(self, hook, action):
        r = hook["post"]("pull_request", _pr_payload(action))
        assert r.status_code == 202 and r.json()["status"] == "accepted"
        job = hook["jobs"][0]
        assert (job.owner, job.repo, job.pr_number, job.installation_id) == ("acme", "traceability-demo-store", 5, INSTALLATION)
        assert job.head_sha == "a" * 40 and job.base_sha == "b" * 40 and job.trigger == f"pull_request.{action}"
        assert job.pr_body == "Closes #12" and job.check_run_id is None
        assert "pr_body" not in r.json()["jobs"][0]

    def test_edited_body_is_reviewed_title_only_is_not(self, hook):
        assert hook["post"]("pull_request", _pr_payload("edited", changes={"body": {"from": "x"}}), "d-a").status_code == 202
        r = hook["post"]("pull_request", _pr_payload("edited", changes={"title": {"from": "x"}}), "d-b")
        assert r.status_code == 200 and r.json()["status"] == "ignored" and len(hook["jobs"]) == 1

    @pytest.mark.parametrize("payload", [_pr_payload("closed"), _pr_payload("labeled"),
                                         _pr_payload("synchronize", state="closed")])
    def test_pull_request_ignored(self, hook, payload):
        r = hook["post"]("pull_request", payload)
        assert r.status_code == 200 and r.json()["status"] == "ignored" and hook["jobs"] == []

    def test_pull_request_without_installation(self, hook):
        p = _pr_payload()
        del p["installation"]
        assert hook["post"]("pull_request", p).json()["status"] == "ignored"

    def test_check_run_rerequested(self, hook):
        r = hook["post"]("check_run", _check_run_payload())
        assert r.status_code == 202
        job = hook["jobs"][0]
        assert job.check_run_id == 4242 and job.head_sha == "c" * 40 and job.trigger == "check_run.rerequested"

    @pytest.mark.parametrize("payload", [_check_run_payload(app_id="999"), _check_run_payload(action="created"),
                                         _check_run_payload(prs=False)])
    def test_check_run_ignored(self, hook, payload):
        r = hook["post"]("check_run", payload)
        assert r.status_code == 200 and r.json()["status"] == "ignored" and hook["jobs"] == []

    def test_unknown_event_ignored(self, hook):
        r = hook["post"]("star", {"action": "created"})
        assert r.status_code == 200 and r.json()["status"] == "ignored"

    def test_bad_requests(self, hook):
        assert hook["post"]("pull_request", None, raw=b"not json").status_code == 400
        assert hook["post"]("", {"a": 1}).status_code == 400
        assert hook["post"]("pull_request", None, raw=b"[1, 2]").status_code == 400


class TestIdempotency:
    def test_duplicate_delivery_runs_once(self, hook):
        assert hook["post"]("pull_request", _pr_payload(), "same-id").status_code == 202
        r = hook["post"]("pull_request", _pr_payload(), "same-id")
        assert r.status_code == 200 and r.json()["status"] == "duplicate" and len(hook["jobs"]) == 1

    def test_new_delivery_same_pr_runs_again(self, hook):
        hook["post"]("pull_request", _pr_payload("synchronize"), "d-1")
        hook["post"]("pull_request", _pr_payload("synchronize"), "d-2")
        assert len(hook["jobs"]) == 2

    def test_failed_job_allows_redelivery(self, hook):
        hook["failing"]["on"] = True
        assert hook["post"]("pull_request", _pr_payload(), "retry-me").status_code == 202
        hook["failing"]["on"] = False
        assert hook["post"]("pull_request", _pr_payload(), "retry-me").status_code == 202
        assert len(hook["jobs"]) == 2

    def test_invalid_signature_does_not_poison_cache(self, hook):
        hook["post"]("pull_request", _pr_payload(), "id-x", secret="wrong")
        assert hook["post"]("pull_request", _pr_payload(), "id-x").status_code == 202

    def test_cache_ttl_and_size(self):
        clock = Clock(0)
        cache = gh_router.DeliveryCache(maxsize=2, ttl_seconds=10, clock=clock)
        assert not cache.check_and_add("a") and cache.check_and_add("a")
        clock.t = 11
        assert not cache.check_and_add("a")                       # expired
        cache.check_and_add("b")
        cache.check_and_add("c")                                  # evicts the oldest ("a")
        assert not cache.check_and_add("a")


# ===========================================================================
# T3.5-T3.9 review flow (check run lifecycle, comment upsert, persistence)
# ===========================================================================

import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
from urllib.parse import unquote  # noqa: E402

import yaml  # noqa: E402
from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.security import get_current_user  # noqa: E402
from app.database import Base, PRReviewRun, User  # noqa: E402
from app.services.github.review_flow import (  # noqa: E402
    COMMENT_MARKER,
    ReviewFlow,
    intent_units,
    repo_path,
)
from src.alignment import ContextUnit  # noqa: E402
from src.parser import extract_endpoints_from_spec  # noqa: E402
from src.pr_reviewer import SIMULATED_PR_SCENARIOS  # noqa: E402
from src.review.intent import IssueRef, find_linked_issues  # noqa: E402
from tests.fakes import FakeEmbedder, FakeExplainer  # noqa: E402

OWNER, REPO = "acme", "traceability-demo-store"
BASE_SHA, HEAD_SHA, MERGE_BASE = "b" * 40, "a" * 40, "m" * 40
SRS_DOCX = (_Path(__file__).parent / "fixtures" / "srs" / "ecommerce_srs_ieee.docx").read_bytes()
CONFIG = {"version": 1, "srs": "docs/SRS.docx", "openapi": "openapi.yaml", "fail_on": "block"}

# requirement index in ecommerce_srs_ieee.docx (REQ-01 .. REQ-10): r2 = REQ-03 cart, r3 = REQ-04, r8 = REQ-09
PATHS = {"add_endpoint": "/api/v1/cart/items", "remove_idempotency": "/api/v1/refund",
         "drop_2fa_requirement": "/api/v1/checkout"}
ISSUES = {
    "add_endpoint": (11, "Support discount codes",
                     "Shoppers want promo codes.\n\n- [ ] Cart accepts an optional discount code"),
    "remove_idempotency": (12, "Simplify refunds", "## Acceptance criteria\n- Refunds use a server reference"),
    "drop_2fa_requirement": (13, "Reduce checkout friction", "Make checkout faster."),
}


def _spec(head_scenario: Optional[str] = None) -> Dict[str, Any]:
    """store_v1-like spec with the three scenario endpoints; one swapped to its head version."""
    paths = {}
    for key, path in PATHS.items():
        change = SIMULATED_PR_SCENARIOS[key]["changes"][0]
        paths[path] = {"post": change["head"] if key == head_scenario else change["base"]}
    return {"openapi": "3.0.3", "info": {"title": "Nexus Store", "version": "1"}, "paths": paths}


def _ep_text(spec: Dict[str, Any], path: str) -> str:
    return next(e["text"] for e in extract_endpoints_from_spec(spec) if e["id"] == f"POST {path}")


def _embedder() -> FakeEmbedder:
    """Cosine scores that make the 3 demo PRs PASS / BLOCK REQ-04 / BLOCK REQ-09."""
    emb = FakeEmbedder(10)
    base = _spec()
    emb.set(_ep_text(base, PATHS["add_endpoint"]), r2=0.70)
    emb.set(_ep_text(_spec("add_endpoint"), PATHS["add_endpoint"]), r2=0.72)                 # +0.02: fine
    emb.set(_ep_text(base, PATHS["remove_idempotency"]), r3=0.82)
    emb.set(_ep_text(_spec("remove_idempotency"), PATHS["remove_idempotency"]), r3=0.75)     # -0.07 strong tier
    emb.set(_ep_text(base, PATHS["drop_2fa_requirement"]), r8=0.56)
    emb.set(_ep_text(_spec("drop_2fa_requirement"), PATHS["drop_2fa_requirement"]), r8=0.40)  # -0.16 weak tier
    # the discount-code acceptance criterion is delivered by the new cart endpoint (0.9 * 0.72 = 0.648)
    number, title, body = ISSUES["add_endpoint"]
    for u in intent_units([{"number": number, "title": title, "body": body}]):
        emb.set(ContextUnit(id=u["id"], text=u["text"], title=u["title"]).embedding_text(), r2=0.90)
    return emb


def _b64(data: bytes) -> Dict[str, Any]:
    return {"type": "file", "encoding": "base64", "size": len(data),
            "content": base64.b64encode(data).decode()}


class FakeRepo:
    """In-memory repository behind FakeGitHub: files per ref, PR, issues, comments, check runs."""

    def __init__(self, fake: FakeGitHub):
        fake.fallback = self.handle
        self.files: Dict[str, Dict[str, bytes]] = {BASE_SHA: {}, HEAD_SHA: {}, MERGE_BASE: {}}
        self.pr_files: List[Dict[str, Any]] = []
        self.pr: Dict[str, Any] = {}
        self.issues: Dict[int, Dict[str, Any]] = {}
        self.closing: List[Dict[str, Any]] = []
        self.graphql_error = False
        self.comments: List[Dict[str, Any]] = []
        self.checks: Dict[int, Dict[str, Any]] = {}
        self.check_log: List[tuple] = []           # ("create" | "update", id, body)
        self.comment_log: List[tuple] = []         # ("create" | "update", id)

    def put(self, sha: str, path: str, data: Any) -> None:
        if isinstance(data, dict):
            data = (json.dumps(data) if path.endswith(".json") else yaml.safe_dump(data, sort_keys=False)).encode()
        self.files[sha][path] = data if isinstance(data, bytes) else data.encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, unquote(request.url.path)
        if path == "/graphql":
            if self.graphql_error:
                return httpx.Response(200, json={"errors": [{"message": "Resource not accessible"}]})
            return httpx.Response(200, json={"data": {"repository": {"pullRequest": {
                "closingIssuesReferences": {"nodes": self.closing}}}}})
        prefix = f"/repos/{OWNER}/{REPO}/"
        if not path.startswith(prefix):
            return httpx.Response(404, json={"message": "Not Found"})
        rest = path[len(prefix):]
        body = json.loads(request.content) if request.content else {}

        if method == "POST" and rest == "check-runs":
            cid = len(self.checks) + 1
            self.checks[cid] = {"id": cid, "html_url": f"https://github.com/{OWNER}/{REPO}/runs/{cid}", **body}
            self.check_log.append(("create", cid, body))
            return httpx.Response(201, json=self.checks[cid])
        if m := _re.fullmatch(r"check-runs/(\d+)", rest):
            cid = int(m.group(1))
            # Like the real API: output fields must be strings, never null.
            if any(v is None for v in (body.get("output") or {}).values()):
                return httpx.Response(422, json={"message": "Invalid request.\n\nnil is not a string."})
            self.checks[cid].update(body)
            self.check_log.append(("update", cid, body))
            return httpx.Response(200, json=self.checks[cid])
        if _re.fullmatch(r"compare/\w+\.\.\.\w+", rest):
            return httpx.Response(200, json={"merge_base_commit": {"sha": MERGE_BASE}})
        if _re.fullmatch(r"pulls/\d+/files", rest):
            return httpx.Response(200, json=self.pr_files)
        if _re.fullmatch(r"pulls/\d+", rest):
            return httpx.Response(200, json=self.pr)
        if rest.startswith("contents/"):
            data = self.files.get(request.url.params.get("ref"), {}).get(rest[len("contents/"):])
            if data is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=_b64(data))
        if m := _re.fullmatch(r"issues/(\d+)", rest):
            issue = self.issues.get(int(m.group(1)))
            return httpx.Response(200, json=issue) if issue else httpx.Response(404, json={"message": "Not Found"})
        if _re.fullmatch(r"issues/\d+/comments", rest):
            if method == "GET":
                return httpx.Response(200, json=self.comments)
            cid = 100 + len(self.comments)
            self.comments.append({"id": cid, "body": body["body"], "html_url": f"https://github.com/c/{cid}"})
            self.comment_log.append(("create", cid))
            return httpx.Response(201, json=self.comments[-1])
        if m := _re.fullmatch(r"issues/comments/(\d+)", rest):
            comment = next(c for c in self.comments if c["id"] == int(m.group(1)))
            comment["body"] = body["body"]
            self.comment_log.append(("update", comment["id"]))
            return httpx.Response(200, json=comment)
        return httpx.Response(404, json={"message": f"unrouted {method} {rest}"})

    def scenario(self, key: str, *, via_graphql: bool = True) -> None:
        """Base = v1 spec; head = the scenario's change; the PR links the scenario's issue."""
        for sha in (BASE_SHA, MERGE_BASE, HEAD_SHA):
            self.put(sha, ".traceability.yml", CONFIG)
            self.put(sha, "docs/SRS.docx", SRS_DOCX)
        for sha in (BASE_SHA, MERGE_BASE):
            self.put(sha, "openapi.yaml", _spec())
        self.put(HEAD_SHA, "openapi.yaml", _spec(key))
        self.pr_files = [{"filename": "openapi.yaml", "status": "modified"}]
        number, title, body = ISSUES[key]
        issue = {"number": number, "title": title, "body": body,
                 "html_url": f"https://github.com/{OWNER}/{REPO}/issues/{number}"}
        self.issues[number] = issue
        self.closing = [{"number": number, "title": title, "body": body, "url": issue["html_url"],
                         "repository": {"nameWithOwner": f"{OWNER}/{REPO}"}}] if via_graphql else []


def _job(key: str = "add_endpoint", delivery: str = "d-1", **over) -> gh_router.ReviewJob:
    sc = SIMULATED_PR_SCENARIOS[key]
    fields = dict(delivery_id=delivery, trigger="pull_request.opened", installation_id=INSTALLATION,
                  owner=OWNER, repo=REPO, pr_number=sc["pr_number"], head_sha=HEAD_SHA, base_sha=BASE_SHA,
                  head_ref=sc["pr_branch"], base_ref="main", pr_title=sc["pr_title"],
                  pr_body=f"Closes #{ISSUES[key][0]}", pr_author=sc["pr_author"],
                  pr_url=f"https://github.com/{OWNER}/{REPO}/pull/{sc['pr_number']}")
    fields.update(over)
    return gh_router.ReviewJob(**fields)


@pytest.fixture(scope="module")
def review_db():
    assert "trace_tests_" in settings.DATABASE_URL, "tests must never use the real database"
    sync = create_engine(settings.DATABASE_URL.replace("+aiosqlite", ""))
    Base.metadata.create_all(sync)
    yield sync
    sync.dispose()


@pytest.fixture
def flow_world(rsa_key, review_db):
    with Session(review_db) as s:
        s.execute(delete(PRReviewRun))
        s.commit()
    clock = Clock()
    fake = FakeGitHub(clock)
    repo = FakeRepo(fake)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    auth = GitHubAppAuth(APP_ID, rsa_key[0], http=http, clock=clock)
    sessions = async_sessionmaker(create_async_engine(settings.DATABASE_URL, poolclass=NullPool),
                                  expire_on_commit=False)
    explainer = FakeExplainer()
    flow = ReviewFlow(auth, http=http, embedder_factory=_embedder, explainer_factory=lambda: explainer,
                      session_factory=sessions)

    def rows() -> List[PRReviewRun]:
        with Session(review_db) as s:
            return list(s.execute(select(PRReviewRun).order_by(PRReviewRun.id)).scalars())

    return {"repo": repo, "fake": fake, "flow": flow, "rows": rows, "explainer": explainer}


class TestReviewFlowScenarios:
    """The three demo PRs (docs/plan.md §4.7) end to end."""

    @pytest.mark.parametrize("key,verdict,conclusion,regressed", [
        ("add_endpoint", "PASS", "success", []),
        ("remove_idempotency", "BLOCK", "failure", ["REQ-04"]),
        ("drop_2fa_requirement", "BLOCK", "failure", ["REQ-09"]),
    ])
    def test_scenario(self, flow_world, key, verdict, conclusion, regressed):
        repo = flow_world["repo"]
        repo.scenario(key)
        outcome = run(flow_world["flow"](_job(key)))

        assert outcome.verdict == verdict and outcome.conclusion == conclusion
        assert outcome.result.regressed_requirements == regressed

        # check run lifecycle: created in_progress on the head SHA, then completed once
        (k1, cid, created), (k2, cid2, updated) = repo.check_log
        assert (k1, k2, cid) == ("create", "update", cid2)
        assert created["status"] == "in_progress" and created["head_sha"] == HEAD_SHA
        assert created["name"] == settings.GITHUB_CHECK_NAME and created["external_id"] == "d-1"
        assert updated["status"] == "completed" and updated["conclusion"] == conclusion
        assert updated["output"]["title"].startswith(verdict)
        for req in regressed:
            assert f"| `{req}` |" in updated["output"]["summary"]
            assert f"explained {req}" in updated["output"]["text"]
        if not regressed:
            assert updated["output"].get("text") is None

        # one PR comment with the marker
        assert repo.comment_log == [("create", 100)]
        assert repo.comments[0]["body"].startswith(COMMENT_MARKER)
        assert "Requirements Traceability Review" in repo.comments[0]["body"]
        assert "/runs/1" in repo.comments[0]["body"]

        # persisted
        (row,) = flow_world["rows"]()
        assert (row.verdict, row.conclusion, row.repo, row.pr_number) == (
            verdict, conclusion, f"{OWNER}/{REPO}", SIMULATED_PR_SCENARIOS[key]["pr_number"])
        assert sorted({v["requirement_id"] for v in row.violations_json}) == regressed
        assert row.check_run_url.endswith("/runs/1") and row.comment_url == "https://github.com/c/100"
        assert row.linked_issues[0]["number"] == ISSUES[key][0]
        assert row.markdown.startswith("## ") and "markdown" not in row.report_json

    def test_pass_uses_linked_issue_intent(self, flow_world):
        flow_world["repo"].scenario("add_endpoint")
        outcome = run(flow_world["flow"](_job("add_endpoint")))
        assert outcome.result.intent is not None and outcome.result.intent.verdict.value == "ALLOW"
        assert "Linked-issue intent" in outcome.summary and "Support discount codes" in outcome.summary

    def test_diff_is_against_the_merge_base(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("remove_idempotency")
        repo.put(BASE_SHA, "openapi.yaml", _spec("remove_idempotency"))   # main moved on; must not matter
        outcome = run(flow_world["flow"](_job("remove_idempotency")))
        assert outcome.verdict == "BLOCK"
        assert f"base `{MERGE_BASE[:7]}`" in outcome.summary

    def test_comment_is_updated_on_the_next_push(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("remove_idempotency")
        run(flow_world["flow"](_job("remove_idempotency", "d-1")))
        repo.put(HEAD_SHA, "openapi.yaml", _spec())                       # the author restores the field
        repo.comments.append({"id": 7, "body": "LGTM from a human", "html_url": "x"})
        outcome = run(flow_world["flow"](_job("remove_idempotency", "d-2")))
        assert outcome.verdict == "PASS"
        assert repo.comment_log == [("create", 100), ("update", 100)]
        assert len([c for c in repo.comments if COMMENT_MARKER in c["body"]]) == 1
        assert "Merge is APPROVED" in repo.comments[0]["body"]
        assert [r.verdict for r in flow_world["rows"]()] == ["BLOCK", "PASS"]

    def test_fail_on_never_reports_block_as_neutral(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("drop_2fa_requirement")
        repo.put(HEAD_SHA, ".traceability.yml", {**CONFIG, "fail_on": "never"})
        outcome = run(flow_world["flow"](_job("drop_2fa_requirement")))
        assert (outcome.verdict, outcome.conclusion) == ("BLOCK", "neutral")
        assert "fail_on: never" in outcome.summary

    def test_regex_fallback_when_graphql_fails(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint", via_graphql=False)
        repo.graphql_error = True
        outcome = run(flow_world["flow"](_job("add_endpoint")))
        assert outcome.verdict == "PASS" and outcome.result.intent is not None
        assert [i["number"] for i in outcome.linked_issues] == [11]

    def test_no_linked_issue_means_no_intent_check(self, flow_world):
        flow_world["repo"].scenario("add_endpoint", via_graphql=False)
        outcome = run(flow_world["flow"](_job("add_endpoint", pr_body="Just a tweak")))
        assert outcome.result.intent is None and outcome.linked_issues == []

    def test_new_spec_has_no_base(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        del repo.files[MERGE_BASE]["openapi.yaml"]
        repo.pr_files = [{"filename": "openapi.yaml", "status": "added"}]
        outcome = run(flow_world["flow"](_job("add_endpoint")))
        assert outcome.verdict == "PASS" and len(outcome.result.diff.added) == 3

    def test_rerequested_check_loads_the_pr(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("remove_idempotency")
        repo.pr = {"title": "refactor refunds", "body": "Fixes #12", "user": {"login": "dev-alice"},
                   "html_url": "https://github.com/acme/demo/pull/42",
                   "base": {"sha": BASE_SHA, "ref": "main"}, "head": {"sha": HEAD_SHA, "ref": "x"}}
        job = _job("remove_idempotency", trigger="check_run.rerequested", pr_title="", pr_body="",
                   check_run_id=4242)
        outcome = run(flow_world["flow"](job))
        assert outcome.verdict == "BLOCK"
        assert repo.check_log[0][0] == "create"                       # a re-run gets a fresh check run
        assert flow_world["rows"]()[0].pr_title == "refactor refunds"


class TestReviewFlowSkipsAndErrors:
    def test_no_config_is_neutral_and_does_not_comment(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        del repo.files[HEAD_SHA][".traceability.yml"]
        outcome = run(flow_world["flow"](_job()))
        assert (outcome.verdict, outcome.conclusion) == ("SKIPPED", "neutral")
        assert repo.check_log[-1][2]["output"]["title"] == "No .traceability.yml, skipping"
        assert repo.comment_log == [] and flow_world["rows"]()[0].verdict == "SKIPPED"

    def test_no_spec_change_is_neutral_and_refreshes_an_old_comment(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        repo.pr_files = [{"filename": "README.md", "status": "modified"}]
        repo.comments.append({"id": 55, "body": f"{COMMENT_MARKER}\nold BLOCK", "html_url": "y"})
        outcome = run(flow_world["flow"](_job()))
        assert outcome.title == "No specification change" and outcome.conclusion == "neutral"
        assert repo.comment_log == [("update", 55)] and "No specification change" in repo.comments[0]["body"]

    def test_srs_change_alone_is_reviewed(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        repo.put(HEAD_SHA, "openapi.yaml", _spec())
        repo.pr_files = [{"filename": "docs/SRS.docx", "status": "modified"}]
        outcome = run(flow_world["flow"](_job()))
        assert outcome.verdict == "PASS" and outcome.result.diff.is_empty

    @pytest.mark.parametrize("breakage,title", [
        (lambda r: r.put(HEAD_SHA, ".traceability.yml", "srs: docs/SRS.docx\nthreshold: {}\n"),
         "Invalid .traceability.yml"),
        (lambda r: r.files[HEAD_SHA].pop("docs/SRS.docx"), "SRS not found"),
        (lambda r: r.put(HEAD_SHA, "docs/SRS.docx", b"PK\x03\x04 not really a docx"), "Could not read the SRS"),
        (lambda r: r.put(HEAD_SHA, "openapi.yaml", "paths: [1, 2"), "Invalid OpenAPI spec"),
    ])
    def test_bad_inputs_fail_the_check_with_the_reason(self, flow_world, breakage, title):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        breakage(repo)
        outcome = run(flow_world["flow"](_job()))
        assert (outcome.verdict, outcome.conclusion, outcome.title) == ("ERROR", "failure", title)
        assert repo.check_log[-1][2]["conclusion"] == "failure"
        assert flow_world["rows"]()[0].error

    def test_crash_fails_the_check_then_reraises_and_a_retry_replaces_the_row(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("add_endpoint")
        flow = flow_world["flow"]

        def broken():
            raise RuntimeError("model download failed")
        flow.embedder_factory = broken
        with pytest.raises(RuntimeError):
            run(flow(_job(delivery="d-x")))
        assert repo.check_log[-1][2]["conclusion"] == "failure"
        assert "model download failed" in repo.check_log[-1][2]["output"]["summary"]
        assert flow_world["rows"]()[0].verdict == "ERROR"

        flow.embedder_factory = _embedder                              # redelivery of the same id retries
        assert run(flow(_job(delivery="d-x"))).verdict == "PASS"
        assert [r.verdict for r in flow_world["rows"]()] == ["PASS"]

    def test_redelivery_of_a_completed_review_is_a_no_op(self, flow_world):
        repo = flow_world["repo"]
        repo.scenario("remove_idempotency")
        assert run(flow_world["flow"](_job("remove_idempotency", "same"))) is not None
        assert run(flow_world["flow"](_job("remove_idempotency", "same"))) is None
        assert len(repo.checks) == 1 and len(flow_world["rows"]()) == 1


class TestIssueParsing:
    @pytest.mark.parametrize("body,expected", [
        ("Closes #12", [IssueRef(12)]),
        ("fixes: #3, resolved #4 and Fixed acme/api#7", [IssueRef(3), IssueRef(4), IssueRef(7, "acme/api")]),
        ("Resolves https://github.com/acme/store/issues/9", [IssueRef(9, "acme/store")]),
        ("closes #5 closes #5", [IssueRef(5)]),
        ("See #12, prefix#3, unfixes #4, closes#8", []),
        ("<!-- Closes #1 -->\n```\nfixes #2\n```\n`closes #3`", []),
        ("", []),
    ])
    def test_find_linked_issues(self, body, expected):
        assert find_linked_issues(body) == expected

    def test_intent_units(self):
        units = intent_units([
            {"number": 1, "title": "T1", "body": "- [x] A works\n- [ ] B works"},
            {"number": 2, "title": "Only a title", "body": ""},
        ])
        assert [(u["id"], u["text"]) for u in units] == [
            ("ISSUE1-AC1", "A works"), ("ISSUE1-AC2", "B works"), ("ISSUE2-AC1", "Only a title")]

    @pytest.mark.parametrize("raw,clean", [("./docs\\SRS.docx", "docs/SRS.docx"), ("/openapi.yaml", "openapi.yaml")])
    def test_repo_path(self, raw, clean):
        assert repo_path(raw) == clean


class TestReviewsEndpoint:
    @pytest.fixture
    def api(self, review_db):
        with Session(review_db, expire_on_commit=False) as s:
            s.execute(delete(PRReviewRun))
            user = s.execute(select(User).where(User.email == "gh@example.com")).scalar_one_or_none()
            if user is None:
                user = User(google_id="g-gh", email="gh@example.com", name="GH")
                s.add(user)
            for i, (repo, verdict) in enumerate([("acme/a", "PASS"), ("acme/b", "BLOCK"), ("acme/a", "SKIPPED")]):
                s.add(PRReviewRun(installation_id=1, repo=repo, pr_number=40 + i, head_sha="f" * 40,
                                  delivery_id=f"d{i}", verdict=verdict, conclusion="success", title=verdict,
                                  summary="s", markdown=f"# md {i}",
                                  violations_json=[{"requirement_id": "REQ-04"}] if verdict == "BLOCK" else []))
            s.commit()
        app.dependency_overrides[get_current_user] = lambda: user
        yield TestClient(app)
        app.dependency_overrides.pop(get_current_user, None)

    def test_list_newest_first(self, api):
        data = api.get("/api/github/reviews").json()
        assert [r["pr_number"] for r in data["reviews"]] == [42, 41, 40]
        assert data["reviews"][1]["violations"] == [{"requirement_id": "REQ-04"}]
        assert data["reviews"][0]["pr_url"] == "https://github.com/acme/a/pull/42"
        assert "markdown" not in data["reviews"][0] and "app_configured" in data

    def test_filters(self, api):
        assert [r["pr_number"] for r in api.get("/api/github/reviews?repo=acme/a").json()["reviews"]] == [42, 40]
        assert len(api.get("/api/github/reviews?repo=acme/a&pr_number=40").json()["reviews"]) == 1
        assert len(api.get("/api/github/reviews?limit=1").json()["reviews"]) == 1

    def test_detail_and_404(self, api):
        rid = api.get("/api/github/reviews?pr_number=41").json()["reviews"][0]["id"]
        detail = api.get(f"/api/github/reviews/{rid}").json()
        assert detail["markdown"] == "# md 1" and detail["verdict"] == "BLOCK"
        assert api.get("/api/github/reviews/999999").status_code == 404

    def test_requires_login(self, review_db):
        assert TestClient(app).get("/api/github/reviews").status_code in (401, 403)
