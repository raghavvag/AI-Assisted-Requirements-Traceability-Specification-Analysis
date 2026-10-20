"""
backend/app/services/github/auth.py — GitHub App authentication
===============================================================
Two credentials, both short-lived:

  App JWT              RS256, signed with the app's private key; proves "I am
                       app <id>". iat = now - 60 s (clock-skew allowance),
                       exp = now + 9 min (GitHub allows at most 10).
  Installation token   exchanged for the JWT at
                       POST /app/installations/{id}/access_tokens; acts on one
                       installation's repositories for 1 hour. Cached per
                       installation and refreshed 5 minutes before it expires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx
from jose import jwt

from app.services.github.errors import GitHubAppConfigError, GitHubAuthError

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "traceability-bot"
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


def github_headers(token: str, accept: str = "application/vnd.github+json") -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }


def load_private_key(path: str = "", inline: str = "") -> str:
    """The PEM text from `inline` (a secret with literal "\\n" escapes is
    accepted) or from the file at `path`. Validated by actually loading it."""
    pem = ""
    if inline.strip():
        pem = inline.strip().replace("\\n", "\n")
    elif path.strip():
        p = Path(path.strip().strip('"'))
        if not p.is_file():
            raise GitHubAppConfigError(f"GITHUB_APP_PRIVATE_KEY_PATH does not exist: {p}")
        pem = p.read_text(encoding="utf-8").strip()
    else:
        raise GitHubAppConfigError("Set GITHUB_APP_PRIVATE_KEY_PATH (or GITHUB_APP_PRIVATE_KEY).")
    if "-----BEGIN" not in pem or "PRIVATE KEY-----" not in pem:
        raise GitHubAppConfigError("The GitHub App private key is not a PEM private key.")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise GitHubAppConfigError(f"The GitHub App private key could not be loaded: {exc}") from exc
    return pem + "\n"


def create_app_jwt(app_id: str, private_key_pem: str, now: Optional[float] = None) -> str:
    """RS256 JWT identifying the app (valid ~10 minutes)."""
    if not str(app_id).strip():
        raise GitHubAppConfigError("GITHUB_APP_ID is not set.")
    ts = int(now if now is not None else time.time())
    issuer = int(app_id) if str(app_id).strip().isdigit() else str(app_id).strip()
    claims = {"iat": ts - JWT_BACKDATE_SECONDS, "exp": ts + JWT_LIFETIME_SECONDS, "iss": issuer}
    return jwt.encode(claims, private_key_pem, algorithm="RS256")


@dataclass
class InstallationToken:
    token: str
    expires_at: float                     # unix seconds
    permissions: Dict[str, str] = field(default_factory=dict)

    def fresh(self, now: float, margin: float = TOKEN_REFRESH_MARGIN_SECONDS) -> bool:
        return now < self.expires_at - margin


def _parse_expiry(value: str) -> float:
    # GitHub returns e.g. "2026-10-16T12:34:56Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


class GitHubAppAuth:
    """Signs app JWTs and hands out cached installation tokens."""

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        *,
        api_url: str = "https://api.github.com",
        http: Optional[httpx.AsyncClient] = None,
        clock: Callable[[], float] = time.time,
        refresh_margin: float = TOKEN_REFRESH_MARGIN_SECONDS,
    ) -> None:
        self.app_id = str(app_id).strip()
        self._key = private_key_pem
        self.api_url = api_url.rstrip("/")
        self._http = http
        self._owns_http = http is None
        self._clock = clock
        self._margin = refresh_margin
        self._tokens: Dict[int, InstallationToken] = {}
        self._locks: Dict[int, asyncio.Lock] = {}

    @classmethod
    def from_settings(cls, settings, *, http: Optional[httpx.AsyncClient] = None) -> "GitHubAppAuth":
        if not settings.GITHUB_APP_ID.strip():
            raise GitHubAppConfigError("GITHUB_APP_ID is not set (see manual.md section D).")
        key = load_private_key(settings.GITHUB_APP_PRIVATE_KEY_PATH, settings.GITHUB_APP_PRIVATE_KEY)
        return cls(settings.GITHUB_APP_ID, key, api_url=settings.GITHUB_API_URL, http=http)

    # -- JWT ----------------------------------------------------------------------

    def app_jwt(self) -> str:
        return create_app_jwt(self.app_id, self._key, now=self._clock())

    # -- installation tokens -------------------------------------------------------

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        return self._http

    def invalidate(self, installation_id: int) -> None:
        """Forget a cached token (e.g. after a 401)."""
        self._tokens.pop(int(installation_id), None)

    async def installation_token(self, installation_id: int, *, force_refresh: bool = False) -> str:
        iid = int(installation_id)
        cached = self._tokens.get(iid)
        if cached and not force_refresh and cached.fresh(self._clock(), self._margin):
            return cached.token
        lock = self._locks.setdefault(iid, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(iid)            # another task may have refreshed it meanwhile
            if cached and not force_refresh and cached.fresh(self._clock(), self._margin):
                return cached.token
            token = await self._exchange(iid)
            self._tokens[iid] = token
            return token.token

    async def _exchange(self, installation_id: int) -> InstallationToken:
        url = f"{self.api_url}/app/installations/{installation_id}/access_tokens"
        resp = await self.http.post(url, headers=github_headers(self.app_jwt()))
        if resp.status_code == 201:
            data = resp.json()
            token = InstallationToken(data["token"], _parse_expiry(data["expires_at"]), data.get("permissions") or {})
            logger.info("GitHub installation %s: token refreshed (expires %s)", installation_id, data["expires_at"])
            return token
        message = _message(resp)
        if resp.status_code == 401:
            raise GitHubAuthError(
                f"GitHub rejected the app JWT ({message}). Check GITHUB_APP_ID, that the private key belongs "
                "to this app, and that the system clock is correct."
            )
        if resp.status_code == 404:
            raise GitHubAuthError(f"Installation {installation_id} not found: is the app still installed? ({message})")
        raise GitHubAuthError(f"Installation token request failed with {resp.status_code}: {message}")

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", resp.text))[:300]
    except ValueError:
        return resp.text[:300]
