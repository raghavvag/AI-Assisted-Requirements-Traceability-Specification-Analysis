"""
backend/app/services/github/client.py — GitHub REST + GraphQL client (one installation)
=====================================================================================
Thin async wrapper over httpx with the calls the PR review bot needs:

  check runs          create_check_run / update_check_run
  pull requests       get_pull_request / list_pull_request_files (paginated) / merge_base
  repository files    get_content (base64 decoded; raw fallback for files > 1 MB) / get_text
  issues + comments   get_issue / list_issue_comments / create / update / upsert_comment
  GraphQL             graphql / closing_issues (closingIssuesReferences)

Resilience:
  401         installation token refreshed once, request retried
  403 / 429   rate limits (primary: X-RateLimit-Remaining 0 + X-RateLimit-Reset;
              secondary: Retry-After or the "secondary rate limit" message) wait
              and retry when the wait is <= max_wait, else GitHubRateLimitError.
              A 403 that is not a rate limit (missing permission) is not retried.
  5xx         retried with exponential backoff.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote

import httpx

from app.services.github.auth import GitHubAppAuth, github_headers
from app.services.github.errors import GitHubAPIError, GitHubRateLimitError

logger = logging.getLogger(__name__)

RAW_ACCEPT = "application/vnd.github.raw+json"
MAX_PAGES = 30                         # 30 x 100 = GitHub's 3000-file cap on PR file listings
CHECK_TEXT_LIMIT = 65535               # check-run output.summary / output.text max length
CHECK_TITLE_LIMIT = 255
ANNOTATIONS_PER_REQUEST = 50

Sleep = Callable[[float], Awaitable[None]]

CLOSING_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 10) {
        nodes { number title body url repository { nameWithOwner } }
      }
    }
  }
}
"""


def _truncate(text: Optional[str], limit: int, suffix: str = "\n\n… (truncated)") -> Optional[str]:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def normalise_check_output(output: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Clamp a check-run `output` to GitHub's limits (title 255, summary/text
    65535 chars, 50 annotations per request). GitHub rejects `"text": null`
    with a 422, so a missing text is left out, not sent as null."""
    if output is None:
        return None
    out = dict(output)
    out["title"] = _truncate(str(out.get("title", "")), CHECK_TITLE_LIMIT, "…") or ""
    out["summary"] = _truncate(str(out.get("summary", "")), CHECK_TEXT_LIMIT) or ""
    if out.get("text") is None:
        out.pop("text", None)
    else:
        out["text"] = _truncate(str(out["text"]), CHECK_TEXT_LIMIT)
    if out.get("annotations"):
        out["annotations"] = list(out["annotations"])[:ANNOTATIONS_PER_REQUEST]
    return out


def graphql_url(api_url: str) -> str:
    api_url = api_url.rstrip("/")
    if api_url.endswith("/api/v3"):                       # GitHub Enterprise Server
        return api_url[: -len("/api/v3")] + "/api/graphql"
    return api_url + "/graphql"


class GitHubClient:
    """API calls on behalf of one installation of the app."""

    def __init__(
        self,
        auth: GitHubAppAuth,
        installation_id: int,
        *,
        http: Optional[httpx.AsyncClient] = None,
        max_retries: int = 3,
        max_wait: float = 60.0,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth = auth
        self.installation_id = int(installation_id)
        self.api_url = auth.api_url
        self._http = http
        self._owns_http = http is None
        self.max_retries = max_retries
        self.max_wait = max_wait
        self._sleep = sleep
        self._clock = clock

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core

    def _url(self, path_or_url: str) -> str:
        return path_or_url if path_or_url.startswith("http") else f"{self.api_url}/{path_or_url.lstrip('/')}"

    def _rate_limit_wait(self, resp: httpx.Response) -> Optional[float]:
        """Seconds to wait if `resp` is a rate-limit response, else None."""
        if resp.status_code not in (403, 429):
            return None
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                return 60.0
        if resp.headers.get("x-ratelimit-remaining") == "0" and resp.headers.get("x-ratelimit-reset"):
            try:
                return max(0.0, float(resp.headers["x-ratelimit-reset"]) - self._clock()) + 1.0
            except ValueError:
                return 60.0
        try:
            message = str(resp.json().get("message", "")).lower()
        except ValueError:
            message = ""
        if "rate limit" in message or resp.status_code == 429:
            return 60.0                                    # secondary limit without headers: GitHub suggests >= 1 min
        return None                                        # a plain 403: missing permission, not a rate limit

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
        accept: str = "application/vnd.github+json",
        expected: Sequence[int] = (200, 201, 204),
        allow_404: bool = False,
    ) -> Optional[httpx.Response]:
        url = self._url(path_or_url)
        refreshed = False
        attempt = 0
        while True:
            token = await self.auth.installation_token(self.installation_id)
            resp = await self.http.request(method, url, params=params, json=json,
                                           headers=github_headers(token, accept))

            if resp.status_code == 401 and not refreshed:
                logger.info("GitHub 401 on %s %s: refreshing installation token", method, url)
                self.auth.invalidate(self.installation_id)
                refreshed = True
                continue

            wait = self._rate_limit_wait(resp)
            if wait is not None:
                if attempt < self.max_retries and wait <= self.max_wait:
                    logger.warning("GitHub rate limit on %s %s: waiting %.0fs (attempt %d)",
                                   method, url, wait, attempt + 1)
                    attempt += 1
                    await self._sleep(wait)
                    continue
                raise GitHubRateLimitError(resp.status_code, _message(resp), url, retry_after=wait)

            if resp.status_code >= 500 and attempt < self.max_retries:
                backoff = min(self.max_wait, 2.0 ** attempt)
                logger.warning("GitHub %s on %s %s: retrying in %.0fs", resp.status_code, method, url, backoff)
                attempt += 1
                await self._sleep(backoff)
                continue
            break

        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code not in expected:
            body = _json(resp)
            raise GitHubAPIError(resp.status_code, _message(resp), url,
                                 str(body.get("documentation_url", "")) if isinstance(body, dict) else "")
        return resp

    async def _paginate(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        items: List[Any] = []
        url: Optional[str] = self._url(path)
        query = {"per_page": 100, **(params or {})}
        for _ in range(MAX_PAGES):
            resp = await self.request("GET", url, params=query)
            items.extend(resp.json())
            nxt = resp.links.get("next", {}).get("url")
            if not nxt:
                break
            url, query = nxt, None                         # the next link already carries the query
        return items

    # ------------------------------------------------------------ check runs

    async def create_check_run(
        self, owner: str, repo: str, *, name: str, head_sha: str, status: str = "in_progress",
        external_id: Optional[str] = None, details_url: Optional[str] = None,
        output: Optional[Dict[str, Any]] = None, started_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name, "head_sha": head_sha, "status": status}
        for key, value in (("external_id", external_id), ("details_url", details_url), ("started_at", started_at),
                           ("output", normalise_check_output(output))):
            if value is not None:
                body[key] = value
        resp = await self.request("POST", f"repos/{owner}/{repo}/check-runs", json=body, expected=(201,))
        return resp.json()

    async def update_check_run(
        self, owner: str, repo: str, check_run_id: int, *, status: Optional[str] = None,
        conclusion: Optional[str] = None, output: Optional[Dict[str, Any]] = None,
        completed_at: Optional[str] = None, details_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        for key, value in (("status", status), ("conclusion", conclusion), ("completed_at", completed_at),
                           ("details_url", details_url), ("output", normalise_check_output(output))):
            if value is not None:
                body[key] = value
        if conclusion is not None and status is None:
            body["status"] = "completed"
        resp = await self.request("PATCH", f"repos/{owner}/{repo}/check-runs/{check_run_id}", json=body)
        return resp.json()

    # --------------------------------------------------------- pull requests

    async def get_pull_request(self, owner: str, repo: str, number: int) -> Dict[str, Any]:
        return (await self.request("GET", f"repos/{owner}/{repo}/pulls/{number}")).json()

    async def list_pull_request_files(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        """Changed files: filename, status (added/removed/modified/renamed), previous_filename, ..."""
        return await self._paginate(f"repos/{owner}/{repo}/pulls/{number}/files")

    async def merge_base(self, owner: str, repo: str, base: str, head: str) -> Optional[str]:
        """SHA of the merge base of `base` and `head` (what the PR's file list is
        diffed against), or None if GitHub cannot compare them."""
        resp = await self.request("GET", f"repos/{owner}/{repo}/compare/{base}...{head}",
                                  params={"per_page": 1}, allow_404=True)
        return None if resp is None else ((resp.json().get("merge_base_commit") or {}).get("sha"))

    # ---------------------------------------------------------------- files

    async def get_content(self, owner: str, repo: str, path: str, ref: str) -> Optional[bytes]:
        """File bytes at `ref`, or None if it does not exist there."""
        api_path = f"repos/{owner}/{repo}/contents/{quote(path.lstrip('/'))}"
        resp = await self.request("GET", api_path, params={"ref": ref}, allow_404=True)
        if resp is None:
            return None
        data = resp.json()
        if isinstance(data, list):
            raise GitHubAPIError(400, f"'{path}' is a directory, not a file", str(resp.url))
        if data.get("type") not in (None, "file"):
            raise GitHubAPIError(400, f"'{path}' is a {data.get('type')}, not a file", str(resp.url))
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"])
        if data.get("size", 0) == 0:
            return b""
        # Files over 1 MB come back with encoding "none" and no content: fetch them raw.
        raw = await self.request("GET", api_path, params={"ref": ref}, accept=RAW_ACCEPT, allow_404=True)
        return None if raw is None else raw.content

    async def get_text(self, owner: str, repo: str, path: str, ref: str) -> Optional[str]:
        data = await self.get_content(owner, repo, path, ref)
        return None if data is None else data.decode("utf-8-sig", errors="replace")

    # --------------------------------------------------------------- issues

    async def get_issue(self, owner: str, repo: str, number: int) -> Optional[Dict[str, Any]]:
        resp = await self.request("GET", f"repos/{owner}/{repo}/issues/{number}", allow_404=True)
        return None if resp is None else resp.json()

    async def list_issue_comments(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        return await self._paginate(f"repos/{owner}/{repo}/issues/{number}/comments")

    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> Dict[str, Any]:
        resp = await self.request("POST", f"repos/{owner}/{repo}/issues/{number}/comments",
                                  json={"body": body}, expected=(201,))
        return resp.json()

    async def update_issue_comment(self, owner: str, repo: str, comment_id: int, body: str) -> Dict[str, Any]:
        resp = await self.request("PATCH", f"repos/{owner}/{repo}/issues/comments/{comment_id}", json={"body": body})
        return resp.json()

    async def upsert_comment(self, owner: str, repo: str, number: int, marker: str, body: str,
                             *, create: bool = True) -> Optional[Dict[str, Any]]:
        """Edit the comment containing `marker` (e.g. "<!-- traceability-bot -->")
        instead of posting a new one on every push. With create=False an existing
        comment is updated but none is posted (returns None)."""
        if marker not in body:
            body = f"{marker}\n{body}"
        for comment in await self.list_issue_comments(owner, repo, number):
            if marker in (comment.get("body") or ""):
                return await self.update_issue_comment(owner, repo, int(comment["id"]), body)
        return await self.create_issue_comment(owner, repo, number, body) if create else None

    # -------------------------------------------------------------- GraphQL

    async def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = await self.request("POST", graphql_url(self.api_url), json={"query": query, "variables": variables or {}})
        payload = resp.json()
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", e)) for e in payload["errors"])
            raise GitHubAPIError(resp.status_code, f"GraphQL error: {messages}", str(resp.url))
        return payload.get("data") or {}

    async def closing_issues(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        """Issues this PR will close (GitHub's own parse of "Closes #N" plus manual links)."""
        data = await self.graphql(CLOSING_ISSUES_QUERY, {"owner": owner, "repo": repo, "number": int(number)})
        pr = ((data.get("repository") or {}).get("pullRequest") or {})
        nodes = ((pr.get("closingIssuesReferences") or {}).get("nodes")) or []
        return [
            {"number": n.get("number"), "title": n.get("title", ""), "body": n.get("body") or "",
             "url": n.get("url", ""), "repository": (n.get("repository") or {}).get("nameWithOwner", "")}
            for n in nodes if n
        ]


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _message(resp: httpx.Response) -> str:
    body = _json(resp)
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])[:300]
    return resp.text[:300]
