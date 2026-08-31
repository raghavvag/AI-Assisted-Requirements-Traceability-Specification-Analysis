"""
pr_reviewer.py — PR Diff Simulator & GitHub-Style Compliance Review Generator
==============================================================================
Responsibility: Simulate a GitHub Actions CI/CD bot that reviews API spec
                changes in a pull request for requirement compliance violations.

Input:  A simulated PR diff payload (from SIMULATED_PR_SCENARIOS) containing
        before/after endpoint schema text for each modified endpoint.
Output: (markdown_comment, violations)
        - markdown_comment: GitHub-style review comment (summary table, per-violation
          cards with AI root-cause analysis, PASS/FAIL badge)
        - violations: list of violation dicts for structured UI rendering

Regression rule (robust for cross-domain cosine ranges):
  Sentence-transformer cosine scores between business prose and API contract text
  compress into a narrow band (~0.3–0.8), so absolute "crossed the threshold"
  tests are unreliable. Instead we use a TIERED relative-drop rule. A
  requirement/endpoint pair is a violation when score_v2 < score_v1 AND either:
    - score_v1 >= STRONG_RELEVANCE (0.60)  and  delta <= -0.05   (strongly linked
      requirement lost meaningful alignment), OR
    - score_v1 >= relevance_floor (0.35)   and  delta <= drift_threshold (-0.08)
  `compliance_threshold` is used only for the Compliant/Non-Compliant labels.

Scenario keys are short and stable so the Streamlit UI, the React frontend and
the FastAPI route all agree: "add_endpoint", "remove_idempotency", "drop_2fa_requirement".
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import numpy as np

from src.embedder import SemanticEmbedder
from src.explainer import ComplianceExplainer
from src.similarity import compute_similarity_matrix

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint text fragments (shared between scenarios)
# ---------------------------------------------------------------------------

_REFUND_V1 = (
    "POST /api/v1/refund | Process a refund transaction with idempotent transaction_id | "
    "Initiates a refund for a completed order. The client MUST provide a unique transaction_id "
    "in the request body to ensure idempotent processing. The transaction_id is included in the "
    "refund receipt response for end-to-end financial audit trail compliance. | "
    "field order_id: The order to refund | "
    "field transaction_id: Unique client-provided identifier for idempotent processing and financial "
    "audit compliance. Must be unique per refund attempt. | "
    "field refund_amount: Amount to refund | field reason: Reason for the refund | "
    "response: Refund processed successfully. Returns the refund receipt including the client-provided transaction_id."
)
_REFUND_V2 = (
    "POST /api/v2/refund | Process a refund and obtain a server-generated refund confirmation | "
    "Initiates a refund for a completed order. The server auto-generates a unique refund_reference "
    "for internal tracking. No caller-supplied idempotency key is accepted or required. | "
    "field order_id: The order to refund | "
    "field refund_amount: Amount to refund | field reason: Reason for the refund | "
    "response: Refund processed. Returns a server-generated refund_reference."
)

_CHECKOUT_V1 = (
    "POST /api/v1/checkout | Submit checkout and place order | "
    "Accepts a complete checkout payload including cart contents, shipping_address, delivery_method, "
    "and payment_method_token. High-value orders (total > $500) require prior 2FA verification. "
    "Returns order_id and estimated_delivery_date. | "
    "field shipping_address: Validated shipping address object | "
    "field delivery_method: Selected delivery method | "
    "field payment_method_token: Token representing the selected payment method | "
    "field two_fa_token: 2FA verification token required for high-value orders (total > $500) | "
    "response: Order placed successfully. Returns order_id and estimated_delivery_date."
)
_CHECKOUT_V2 = (
    "POST /api/v2/checkout | Submit checkout and place a new order | "
    "Accepts a checkout payload with shipping_address, delivery_method, payment_method_token and "
    "optional currency_code. All orders are processed directly without any step-up authentication "
    "or secondary verification challenge, regardless of order value. | "
    "field shipping_address: Validated shipping address object | "
    "field delivery_method: Selected delivery method | "
    "field payment_method_token: Token representing the selected payment method | "
    "field currency_code: Optional ISO 4217 currency code | "
    "response: Order placed successfully. Returns order_id and estimated_delivery_date."
)

_CART_ADD_V1 = (
    "POST /api/v1/cart/items | Add item to shopping cart | "
    "Adds a product to the authenticated user's persistent shopping cart. Returns the updated cart "
    "state with item_id, product_id, quantity, unit_price and updated subtotal. | "
    "field product_id | field quantity | "
    "response: Item added. Returns updated cart state."
)
_CART_ADD_V2 = (
    "POST /api/v2/cart/items | Add item to shopping cart | "
    "Adds a product to the authenticated user's persistent shopping cart. Returns the updated cart "
    "state with item_id, product_id, quantity, unit_price and updated subtotal. Supports an optional "
    "discount_code field for promotional pricing (non-breaking addition). | "
    "field product_id | field quantity | field discount_code: Optional promotional discount code | "
    "response: Item added. Returns updated cart state with item_id, product_id, quantity, unit_price and updated subtotal."
)


# ---------------------------------------------------------------------------
# Simulated PR scenarios — SHORT stable keys shared across all UIs
# ---------------------------------------------------------------------------

SIMULATED_PR_SCENARIOS: Dict[str, Dict] = {
    "add_endpoint": {
        "pr_number": 41,
        "pr_title": "feat: Add optional discount_code field to cart (non-breaking)",
        "pr_author": "dev-bob",
        "pr_branch": "feature/discount-code-support",
        "changed_endpoints": [
            {
                "endpoint_id": "POST /api/v2/cart/items",
                "change_description": "Added optional discount_code field — non-breaking additive change",
                "v1_text": _CART_ADD_V1,
                "v2_text": _CART_ADD_V2,
            }
        ],
    },
    "remove_idempotency": {
        "pr_number": 42,
        "pr_title": "refactor: Replace client transaction_id with server refund_reference",
        "pr_author": "dev-alice",
        "pr_branch": "feature/v2-refund-refactor",
        "changed_endpoints": [
            {
                "endpoint_id": "POST /api/v2/refund",
                "change_description": "Removed client-provided transaction_id; replaced with server-generated refund_reference (REQ-04 risk)",
                "v1_text": _REFUND_V1,
                "v2_text": _REFUND_V2,
            }
        ],
    },
    "drop_2fa_requirement": {
        "pr_number": 43,
        "pr_title": "refactor: Remove 2FA step-up gate from checkout",
        "pr_author": "dev-carol",
        "pr_branch": "feature/v2-frictionless-checkout",
        "changed_endpoints": [
            {
                "endpoint_id": "POST /api/v2/checkout",
                "change_description": "Removed the two_fa_token requirement and 2FA enforcement for high-value orders (REQ-09 risk)",
                "v1_text": _CHECKOUT_V1,
                "v2_text": _CHECKOUT_V2,
            }
        ],
    },
}

# Backwards-compatibility: accept the older long-form keys if any caller still uses them.
_LEGACY_KEY_ALIASES = {
    "PR #42: Refactor payment processing — introduces 2 drift violations": "remove_idempotency",
    "PR #43: Add optional discount_code field to cart — no violations": "add_endpoint",
    "PR #44: Update product search pagination parameters — no violations": "add_endpoint",
}


def resolve_scenario_key(key: str) -> str:
    """Map any accepted key (short, legacy long-form, or unknown) to a valid short key."""
    if key in SIMULATED_PR_SCENARIOS:
        return key
    if key in _LEGACY_KEY_ALIASES:
        return _LEGACY_KEY_ALIASES[key]
    logger.warning("Unknown PR scenario key %r — defaulting to 'remove_idempotency'.", key)
    return "remove_idempotency"


def _classify_severity(delta: float) -> str:
    if delta >= 0:
        return "No Drift"
    elif delta >= -0.10:
        return "Stable"
    elif delta >= -0.25:
        return "Minor Drift"
    elif delta >= -0.50:
        return "Major Drift"
    return "Critical Drift"


def _violation_severity(delta: float) -> str:
    """Severity for a pair already confirmed as a regression — never 'Stable'/'No Drift'."""
    label = _classify_severity(delta)
    return "Minor Drift" if label in ("No Drift", "Stable") else label


# ---------------------------------------------------------------------------
# PR Reviewer
# ---------------------------------------------------------------------------

class PRReviewer:
    """Simulates a GitHub Actions compliance review bot."""

    STRONG_RELEVANCE = 0.60        # score_v1 above this => the pair is strongly linked
    STRONG_DRIFT_THRESHOLD = -0.05  # smaller drop is meaningful for strongly-linked pairs

    def __init__(
        self,
        embedder: SemanticEmbedder,
        explainer: ComplianceExplainer,
        compliance_threshold: float = 0.50,
        drift_threshold: float = -0.08,
        relevance_floor: float = 0.35,
    ):
        self.embedder = embedder
        self.explainer = explainer
        self.compliance_threshold = compliance_threshold
        self.drift_threshold = drift_threshold
        self.relevance_floor = relevance_floor

    def _is_regression(self, s_v1: float, s_v2: float) -> bool:
        delta = s_v2 - s_v1
        if s_v2 >= s_v1:
            return False
        if s_v1 >= self.STRONG_RELEVANCE:
            return delta <= self.STRONG_DRIFT_THRESHOLD
        if s_v1 >= self.relevance_floor:
            return delta <= self.drift_threshold
        return False

    def run_pr_review(
        self,
        scenario_key: str,
        requirements: List[Dict],
        req_embeddings: np.ndarray,
    ) -> Tuple[str, List[Dict]]:
        """Run a PR compliance review for a scenario. Returns (markdown_comment, violations)."""
        key = resolve_scenario_key(scenario_key)
        scenario = SIMULATED_PR_SCENARIOS[key]

        changed_endpoints = scenario["changed_endpoints"]
        violations: List[Dict] = []
        clean_changes: List[Dict] = []

        for ep_change in changed_endpoints:
            ep_id      = ep_change["endpoint_id"]
            v1_text    = ep_change["v1_text"]
            v2_text    = ep_change["v2_text"]
            change_desc = ep_change["change_description"]

            v1_vec = self.embedder.encode_single(v1_text).reshape(1, -1)
            v2_vec = self.embedder.encode_single(v2_text).reshape(1, -1)
            scores_v1 = compute_similarity_matrix(req_embeddings, v1_vec).flatten()
            scores_v2 = compute_similarity_matrix(req_embeddings, v2_vec).flatten()

            ep_violated = False
            for i, req in enumerate(requirements):
                s_v1  = float(scores_v1[i])
                s_v2  = float(scores_v2[i])
                delta = s_v2 - s_v1

                if self._is_regression(s_v1, s_v2):
                    ep_violated = True
                    drift_pair = {
                        "requirement_id":   req["id"],
                        "requirement_text": req["text"],
                        "endpoint_id":      ep_id,
                        "score_v1":         round(s_v1, 4),
                        "score_v2":         round(s_v2, 4),
                        "delta":            round(delta, 4),
                        "severity":         _violation_severity(delta),
                    }
                    explanation = self.explainer.explain_drift(drift_pair, v1_text, v2_text)
                    violations.append({
                        **drift_pair,
                        "endpoint_change_description": change_desc,
                        "explanation": explanation,
                    })

            if not ep_violated:
                clean_changes.append({"endpoint_id": ep_id, "change_description": change_desc})

        # Keep only the single worst violation per (endpoint) to avoid noise,
        # unless multiple distinct requirements regressed materially.
        violations.sort(key=lambda v: v["delta"])

        comment = _build_pr_comment(
            pr_number=scenario["pr_number"],
            pr_title=scenario["pr_title"],
            pr_author=scenario["pr_author"],
            violations=violations,
            clean_changes=clean_changes,
            provider_info=self.explainer.provider_info,
        )
        logger.info(
            "PR #%d (%s) review: %d endpoints checked, %d violation(s).",
            scenario["pr_number"], key, len(changed_endpoints), len(violations),
        )
        return comment, violations


# ---------------------------------------------------------------------------
# Markdown comment builder
# ---------------------------------------------------------------------------

def _build_pr_comment(
    pr_number: int,
    pr_title: str,
    pr_author: str,
    violations: List[Dict],
    clean_changes: List[Dict],
    provider_info: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "FAIL ❌" if violations else "PASS ✅"
    n_violations = len(violations)
    n_clean = len(clean_changes)
    n_endpoints_changed = len({v["endpoint_id"] for v in violations}) + n_clean

    lines = [
        "## \U0001f916 AI Compliance Bot — Requirements Traceability Review",
        f"> Automated semantic compliance analysis | {now}",
        f"> LLM Provider: {provider_info}",
        "",
        "---",
        "",
        "### Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| PR Number | #{pr_number} |",
        f"| PR Title | {pr_title} |",
        f"| Author | {pr_author} |",
        f"| Endpoints Changed | {n_endpoints_changed} |",
        f"| Compliance Violations | {n_violations} |",
        f"| Clean Changes (No Violations) | {n_clean} |",
        f"| Overall Status | **{status}** |",
        "",
        "---",
        "",
    ]

    if violations:
        lines += ["### ⚠️ Violation Details", ""]
        for i, v in enumerate(violations, start=1):
            badge = f"\U0001f534 {v['severity']}" if "Critical" in v["severity"] or "Major" in v["severity"] else f"\U0001f7e0 {v['severity']}"
            lines += [
                f"#### VIOLATION #{i} — {badge}",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| **Requirement** | `{v['requirement_id']}` |",
                f"| **Endpoint** | `{v['endpoint_id']}` |",
                f"| **v1.0 Score** | `{v['score_v1']:.4f}` |",
                f"| **v2.0 Score** | `{v['score_v2']:.4f}` |",
                f"| **Score Delta** | `{v['delta']:.4f}` |",
                f"| **Change** | {v.get('endpoint_change_description', 'N/A')} |",
                "",
                "**\U0001f9e0 AI Root Cause Analysis:**",
                "",
                v.get("explanation", "_No explanation available._"),
                "",
                "---",
                "",
            ]
    else:
        lines += [
            "### ✅ No Compliance Violations Detected",
            "",
            "All changed endpoints maintain or improve alignment with the requirement baseline.",
            "",
            "---",
            "",
        ]

    if clean_changes:
        lines += ["### ✅ Clean Changes (Non-Regressing)", ""]
        lines += [f"- `{c['endpoint_id']}`: {c['change_description']}" for c in clean_changes]
        lines += ["", "---", ""]

    if violations:
        lines += [
            "> \U0001f6ab **Merge is BLOCKED** until all compliance violations are resolved.",
            "> Address the remediation actions above and push a new commit.",
        ]
    else:
        lines += [
            "> ✅ **Merge is APPROVED** from a requirements-compliance perspective.",
            "> All API changes are consistent with the requirement baseline.",
        ]

    return "\n".join(lines)
