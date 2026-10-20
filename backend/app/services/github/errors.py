"""
backend/app/services/github/errors.py — GitHub integration errors
"""

from __future__ import annotations

from typing import Optional


class GitHubError(Exception):
    """Base class for everything raised by the GitHub integration."""


class GitHubAppConfigError(GitHubError):
    """App ID / private key / webhook secret missing or unusable."""


class GitHubAuthError(GitHubError):
    """The app JWT or the installation token was rejected."""


class GitHubAPIError(GitHubError):
    """A REST or GraphQL call failed with a non-retryable status."""

    def __init__(self, status: int, message: str, url: str = "", documentation_url: str = ""):
        self.status = status
        self.message = message
        self.url = url
        self.documentation_url = documentation_url
        super().__init__(f"GitHub API {status} for {url}: {message}")


class GitHubRateLimitError(GitHubAPIError):
    """Rate limited and the wait is longer than we are willing to block for."""

    def __init__(self, status: int, message: str, url: str = "", retry_after: Optional[float] = None):
        super().__init__(status, message, url)
        self.retry_after = retry_after
