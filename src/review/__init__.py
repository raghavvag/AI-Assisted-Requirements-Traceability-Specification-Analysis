"""
src/review — Shared review engine (PASS / WARN / BLOCK for an API change)
=======================================================================
    from src.review import review_spec_change
    result = review_spec_change(requirements, spec_base, spec_head, thresholds=config)
    result.verdict, result.violations, result.markdown, result.fails(config.fail_on)

See docs/plan.md §3.2.
"""

from src.review.engine import (
    ADDED,
    MODIFIED,
    REMOVED,
    EndpointChange,
    PullRequestInfo,
    ReviewResult,
    SpecDiff,
    Verdict,
    Violation,
    diff_endpoints,
    diff_specs,
    is_regression,
    review_endpoint_changes,
    review_spec_change,
    violation_severity,
)
from src.review.report import render_markdown

__all__ = [
    "ADDED", "MODIFIED", "REMOVED",
    "EndpointChange", "PullRequestInfo", "ReviewResult", "SpecDiff", "Verdict", "Violation",
    "diff_endpoints", "diff_specs", "is_regression", "render_markdown",
    "review_endpoint_changes", "review_spec_change", "violation_severity",
]
