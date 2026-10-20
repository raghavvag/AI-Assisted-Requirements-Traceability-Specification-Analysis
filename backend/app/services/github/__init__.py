"""
backend/app/services/github — GitHub App integration (Milestone 3)
=================================================================
    auth.py     app JWT (RS256) + cached installation tokens
    client.py   REST / GraphQL calls for one installation, rate-limit aware
    errors.py   exception types
    review_flow.py  ReviewFlow: the review job behind the webhook (check run, specs,
                SRS, linked issue, engine, PR comment, PRReviewRun row)

The webhook endpoint is backend/app/routers/github.py.
"""

from app.services.github.auth import GitHubAppAuth, create_app_jwt, load_private_key
from app.services.github.client import GitHubClient, normalise_check_output
from app.services.github.errors import (
    GitHubAPIError,
    GitHubAppConfigError,
    GitHubAuthError,
    GitHubError,
    GitHubRateLimitError,
)

__all__ = [
    "GitHubAPIError", "GitHubAppAuth", "GitHubAppConfigError", "GitHubAuthError", "GitHubClient",
    "GitHubError", "GitHubRateLimitError", "create_app_jwt", "load_private_key", "normalise_check_output",
]
