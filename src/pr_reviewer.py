"""
pr_reviewer.py — Simulated PR scenarios on top of the shared review engine
==========================================================================
Three built-in pull requests let the UI demonstrate the PR gate without a
GitHub repository. Each scenario is a real (small) OpenAPI change: a base spec
and a head spec with one endpoint each. They go through exactly the same code
path as a live PR, `src.review.review_spec_change`, so the simulation can never
disagree with the GitHub App, the CLI or CI.

    add_endpoint          #41  optional discount_code on POST cart/items   -> PASS
    remove_idempotency    #42  refund drops the client transaction_id       -> BLOCK (REQ-04)
    drop_2fa_requirement  #43  checkout drops the 2FA step-up gate          -> BLOCK (REQ-09)

Regression rule, severity and verdict all live in src/review/engine.py
(`is_regression`, `violation_severity`); thresholds come from
src.config.Thresholds (.traceability.yml).

Scenario keys are short and stable so the React frontend and the FastAPI route
agree: "add_endpoint", "remove_idempotency", "drop_2fa_requirement".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.config import Thresholds
from src.review import PullRequestInfo, ReviewResult, review_spec_change

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenario operations (OpenAPI 3 operation objects)
# ---------------------------------------------------------------------------

def _operation(summary: str, description: str, fields: Sequence[Tuple[str, str]], response: str) -> Dict[str, Any]:
    """A POST operation with a JSON request body; `fields` = (name, description)."""
    properties = {name: {"type": "string", **({"description": desc} if desc else {})} for name, desc in fields}
    return {
        "summary": summary,
        "description": description,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object", "properties": properties}}},
        },
        "responses": {"200": {"description": response}},
    }


_REFUND_V1 = _operation(
    "Process a refund transaction with idempotent transaction_id",
    "Initiates a refund for a completed order. The client MUST provide a unique transaction_id in the request "
    "body to ensure idempotent processing. The transaction_id is included in the refund receipt response for "
    "end-to-end financial audit trail compliance.",
    [("order_id", "The order to refund"),
     ("transaction_id", "Unique client-provided identifier for idempotent processing and financial audit "
                        "compliance. Must be unique per refund attempt."),
     ("refund_amount", "Amount to refund"),
     ("reason", "Reason for the refund")],
    "Refund processed successfully. Returns the refund receipt including the client-provided transaction_id.",
)
_REFUND_V2 = _operation(
    "Process a refund and obtain a server-generated refund confirmation",
    "Initiates a refund for a completed order. The server auto-generates a unique refund_reference for internal "
    "tracking. No caller-supplied idempotency key is accepted or required.",
    [("order_id", "The order to refund"),
     ("refund_amount", "Amount to refund"),
     ("reason", "Reason for the refund")],
    "Refund processed. Returns a server-generated refund_reference.",
)

_CHECKOUT_V1 = _operation(
    "Submit checkout and place order",
    "Accepts a complete checkout payload including cart contents, shipping_address, delivery_method, and "
    "payment_method_token. High-value orders (total > $500) require prior 2FA verification. Returns order_id "
    "and estimated_delivery_date.",
    [("shipping_address", "Validated shipping address object"),
     ("delivery_method", "Selected delivery method"),
     ("payment_method_token", "Token representing the selected payment method"),
     ("two_fa_token", "2FA verification token required for high-value orders (total > $500)")],
    "Order placed successfully. Returns order_id and estimated_delivery_date.",
)
_CHECKOUT_V2 = _operation(
    "Submit checkout and place a new order",
    "Accepts a checkout payload with shipping_address, delivery_method, payment_method_token and optional "
    "currency_code. All orders are processed directly without any step-up authentication or secondary "
    "verification challenge, regardless of order value.",
    [("shipping_address", "Validated shipping address object"),
     ("delivery_method", "Selected delivery method"),
     ("payment_method_token", "Token representing the selected payment method"),
     ("currency_code", "Optional ISO 4217 currency code")],
    "Order placed successfully. Returns order_id and estimated_delivery_date.",
)

_CART_ADD_V1 = _operation(
    "Add item to shopping cart",
    "Adds a product to the authenticated user's persistent shopping cart. Returns the updated cart state with "
    "item_id, product_id, quantity, unit_price and updated subtotal.",
    [("product_id", ""), ("quantity", "")],
    "Item added. Returns updated cart state.",
)
_CART_ADD_V2 = _operation(
    "Add item to shopping cart",
    "Adds a product to the authenticated user's persistent shopping cart. Returns the updated cart state with "
    "item_id, product_id, quantity, unit_price and updated subtotal. Supports an optional discount_code field "
    "for promotional pricing (non-breaking addition).",
    [("product_id", ""), ("quantity", ""), ("discount_code", "Optional promotional discount code")],
    "Item added. Returns updated cart state with item_id, product_id, quantity, unit_price and updated subtotal.",
)


# ---------------------------------------------------------------------------
# Simulated PR scenarios — SHORT stable keys shared across all UIs
# ---------------------------------------------------------------------------

SIMULATED_PR_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "add_endpoint": {
        "pr_number": 41,
        "pr_title": "feat: Add optional discount_code field to cart (non-breaking)",
        "pr_author": "dev-bob",
        "pr_branch": "feature/discount-code-support",
        "expected_verdict": "PASS",
        "changes": [{
            "method": "post",
            "base_path": "/api/v1/cart/items", "base": _CART_ADD_V1,
            "head_path": "/api/v2/cart/items", "head": _CART_ADD_V2,
            "change_description": "Added optional discount_code field — non-breaking additive change",
        }],
    },
    "remove_idempotency": {
        "pr_number": 42,
        "pr_title": "refactor: Replace client transaction_id with server refund_reference",
        "pr_author": "dev-alice",
        "pr_branch": "feature/v2-refund-refactor",
        "expected_verdict": "BLOCK",
        "changes": [{
            "method": "post",
            "base_path": "/api/v1/refund", "base": _REFUND_V1,
            "head_path": "/api/v2/refund", "head": _REFUND_V2,
            "change_description": "Removed client-provided transaction_id; replaced with server-generated "
                                  "refund_reference (REQ-04 risk)",
        }],
    },
    "drop_2fa_requirement": {
        "pr_number": 43,
        "pr_title": "refactor: Remove 2FA step-up gate from checkout",
        "pr_author": "dev-carol",
        "pr_branch": "feature/v2-frictionless-checkout",
        "expected_verdict": "BLOCK",
        "changes": [{
            "method": "post",
            "base_path": "/api/v1/checkout", "base": _CHECKOUT_V1,
            "head_path": "/api/v2/checkout", "head": _CHECKOUT_V2,
            "change_description": "Removed the two_fa_token requirement and 2FA enforcement for high-value "
                                  "orders (REQ-09 risk)",
        }],
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


def build_scenario_specs(
    scenario_key: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], PullRequestInfo, Dict[str, str]]:
    """(spec_base, spec_head, pr_info, change_descriptions) for a scenario."""
    key = resolve_scenario_key(scenario_key)
    sc = SIMULATED_PR_SCENARIOS[key]
    base_paths: Dict[str, Dict[str, Any]] = {}
    head_paths: Dict[str, Dict[str, Any]] = {}
    descriptions: Dict[str, str] = {}
    for ch in sc["changes"]:
        method = ch["method"].lower()
        if ch.get("base") is not None:
            base_paths.setdefault(ch["base_path"], {})[method] = ch["base"]
        if ch.get("head") is not None:
            head_paths.setdefault(ch["head_path"], {})[method] = ch["head"]
        head_or_base = ch.get("head_path") or ch["base_path"]
        descriptions[f"{method.upper()} {head_or_base}"] = ch["change_description"]

    def spec(paths: Dict[str, Dict[str, Any]], version: str) -> Dict[str, Any]:
        return {"openapi": "3.0.3", "info": {"title": "Nexus Store API", "version": version}, "paths": paths}

    pr = PullRequestInfo(number=sc["pr_number"], title=sc["pr_title"], author=sc["pr_author"],
                         branch=sc["pr_branch"])
    return spec(base_paths, "1.0.0"), spec(head_paths, "2.0.0"), pr, descriptions


# ---------------------------------------------------------------------------
# PR Reviewer
# ---------------------------------------------------------------------------

class PRReviewer:
    """Runs a simulated PR scenario through the shared review engine."""

    def __init__(
        self,
        embedder: Any,
        explainer: Any = None,
        compliance_threshold: Optional[float] = None,
        drift_threshold: Optional[float] = None,
        relevance_floor: Optional[float] = None,
        thresholds: Optional[Thresholds] = None,
    ):
        """
        Args:
            embedder:   src.embedder.SemanticEmbedder.
            explainer:  ComplianceExplainer (or None to skip explanations).
            thresholds: src.config.Thresholds; defaults to .traceability.yml defaults.
            compliance_threshold / drift_threshold / relevance_floor: legacy keyword
                overrides, mapped to thresholds.compliant / regression_weak / relevance_floor.
        """
        self.embedder = embedder
        self.explainer = explainer
        t = thresholds or Thresholds()
        overrides: Dict[str, float] = {}
        if compliance_threshold is not None:
            overrides["compliant"] = compliance_threshold
        if drift_threshold is not None:
            overrides["regression_weak"] = drift_threshold
        if relevance_floor is not None:
            overrides["relevance_floor"] = relevance_floor
        self.thresholds = Thresholds.model_validate({**t.model_dump(), **overrides}) if overrides else t

    def review(
        self,
        scenario_key: str,
        requirements: List[Dict],
        req_embeddings: Optional[np.ndarray] = None,
    ) -> ReviewResult:
        """Run a scenario through `review_spec_change` and return the full result."""
        key = resolve_scenario_key(scenario_key)
        spec_base, spec_head, pr, descriptions = build_scenario_specs(key)
        result = review_spec_change(
            requirements, spec_base, spec_head,
            thresholds=self.thresholds,
            embedder=self.embedder,
            explainer=self.explainer,
            req_embeddings=req_embeddings,
            pr=pr,
            change_descriptions=descriptions,
            source_base=f"scenario:{key}:base",
            source_head=f"scenario:{key}:head",
        )
        logger.info("PR #%s (%s): %s", pr.number, key, result.summary)
        return result

    def run_pr_review(
        self,
        scenario_key: str,
        requirements: List[Dict],
        req_embeddings: Optional[np.ndarray] = None,
    ) -> Tuple[str, List[Dict]]:
        """Backward-compatible wrapper: (markdown_comment, violation dicts)."""
        result = self.review(scenario_key, requirements, req_embeddings)
        return result.markdown, [v.to_dict() for v in result.violations]
