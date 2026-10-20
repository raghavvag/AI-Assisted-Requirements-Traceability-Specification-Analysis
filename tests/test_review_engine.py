"""
test_review_engine.py — Shared review engine (src/review) and the simulated PR bot
==================================================================================
Fast tests use a deterministic fake embedder whose cosine scores are chosen
exactly, so the diff, the tiered rule, severity and verdict logic are tested
without loading a model. Tests marked `slow` run the three built-in PR
scenarios on the real all-mpnet-base-v2 model.
"""

from pathlib import Path
from typing import Dict

import numpy as np
import pytest

from src.config import FailOn, Thresholds
from src.parser import extract_endpoints_from_spec, extract_requirements
from src.pr_reviewer import SIMULATED_PR_SCENARIOS, PRReviewer, build_scenario_specs, resolve_scenario_key
from src.review import (
    ADDED,
    MODIFIED,
    REMOVED,
    PullRequestInfo,
    Verdict,
    diff_specs,
    is_regression,
    render_markdown,
    review_spec_change,
    violation_severity,
)

ROOT = Path(__file__).parent.parent


from tests.fakes import FakeEmbedder, FakeExplainer  # noqa: E402

REQS = [
    {"id": "REQ-A", "title": "Refunds", "text": "Refunds carry a client transaction id."},
    {"id": "REQ-B", "title": "Checkout", "text": "High-value checkout requires 2FA."},
    {"id": "REQ-C", "title": "Catalogue", "text": "Products can be listed."},
]


def _op(summary: str) -> Dict:
    return {"summary": summary, "responses": {"200": {"description": "ok"}}}


def _spec(paths: Dict[str, Dict[str, Dict]]) -> Dict:
    return {"openapi": "3.0.3", "paths": paths}


def _text(spec: Dict, endpoint_id: str) -> str:
    return next(e["text"] for e in extract_endpoints_from_spec(spec) if e["id"] == endpoint_id)


# ---------------------------------------------------------------------------
# 1. Diff
# ---------------------------------------------------------------------------

class TestDiff:
    BASE = _spec({
        "/api/v1/refund": {"post": _op("Refund with transaction id")},
        "/api/v1/products": {"get": _op("List products")},
        "/api/v1/legacy": {"delete": _op("Legacy cleanup")},
    })
    HEAD = _spec({
        "/api/v2/refund": {"post": _op("Refund with server reference")},     # modified
        "/api/v2/products": {"get": _op("List products")},                   # version bump only
        "/api/v2/wishlist": {"post": _op("Add to wishlist")},                # added
    })

    def test_categories(self):
        d = diff_specs(self.BASE, self.HEAD)
        assert [c.endpoint_id for c in d.modified] == ["POST /api/v2/refund"]
        assert [c.endpoint_id for c in d.removed] == ["DELETE /api/v1/legacy"]
        assert [c.endpoint_id for c in d.added] == ["POST /api/v2/wishlist"]
        assert d.unchanged == ("GET /api/v2/products",)

    def test_modified_pairs_base_and_head(self):
        m = diff_specs(self.BASE, self.HEAD).modified[0]
        assert (m.status, m.base_id, m.head_id) == (MODIFIED, "POST /api/v1/refund", "POST /api/v2/refund")
        assert "transaction id" in m.base_text and "server reference" in m.head_text

    def test_statuses_and_default_descriptions(self):
        d = diff_specs(self.BASE, self.HEAD)
        assert (d.removed[0].status, d.removed[0].change_description) == (REMOVED, "Endpoint removed")
        assert (d.added[0].status, d.added[0].change_description) == (ADDED, "Endpoint added")

    def test_change_descriptions_by_head_id(self):
        d = diff_specs(self.BASE, self.HEAD, {"POST /api/v2/refund": "dropped transaction_id"})
        assert d.modified[0].change_description == "dropped transaction_id"

    def test_identical_specs_are_empty(self):
        d = diff_specs(self.BASE, self.BASE)
        assert d.is_empty and len(d.unchanged) == 3

    def test_new_or_deleted_spec(self):
        assert len(diff_specs(None, self.HEAD).added) == 3
        assert len(diff_specs(self.BASE, None).removed) == 3

    def test_same_path_twice_in_one_spec(self):
        head = _spec({"/api/v1/refund": {"post": _op("old")}, "/api/v2/refund": {"post": _op("new")}})
        d = diff_specs(_spec({"/api/v1/refund": {"post": _op("old")}}), head)
        assert d.unchanged == ("POST /api/v1/refund",) and [c.endpoint_id for c in d.added] == ["POST /api/v2/refund"]


# ---------------------------------------------------------------------------
# 2. Tiered regression rule
# ---------------------------------------------------------------------------

class TestTieredRule:
    @pytest.mark.parametrize("base,head,expected,why", [
        (0.80, 0.74, True, "strong tier: -0.06 <= -0.05"),
        (0.80, 0.76, False, "strong tier: -0.04 is noise"),
        (0.6125, 0.55, True, "strong tier starts at 0.60"),
        (0.50, 0.41, True, "weak tier: -0.09 <= -0.08"),
        (0.50, 0.43, False, "weak tier: -0.07 is noise"),
        (0.59, 0.53, False, "weak tier applies just below 0.60: -0.06 is not enough"),
        (0.35, 0.20, True, "relevance floor is inclusive"),
        (0.34, 0.05, False, "below the relevance floor: never related"),
        (0.70, 0.75, False, "improvement"),
        (0.60, 0.60, False, "unchanged"),
        (0.90, 0.00, True, "endpoint removed"),
    ])
    def test_default_table(self, base, head, expected, why):
        assert is_regression(base, head) is expected, why

    def test_uses_configured_thresholds(self):
        loose = Thresholds(regression_weak=-0.20, regression_strong=-0.10)
        assert is_regression(0.50, 0.41) and not is_regression(0.50, 0.41, loose)
        assert is_regression(0.80, 0.74) and not is_regression(0.80, 0.74, loose)
        strict_floor = Thresholds(relevance_floor=0.45)
        assert not is_regression(0.40, 0.20, strict_floor)

    @pytest.mark.parametrize("delta,label", [
        (-0.06, "Minor Drift"),          # flagged by the tiered rule, below the -0.10 noise band
        (-0.15, "Minor Drift"),
        (-0.30, "Major Drift"),
        (-0.60, "Critical Drift"),
    ])
    def test_violation_severity(self, delta, label):
        assert violation_severity(delta) == label


# ---------------------------------------------------------------------------
# 3. Engine (fake embedder)
# ---------------------------------------------------------------------------

def _review(base_spec, head_spec, emb, **kw):
    return review_spec_change(REQS, base_spec, head_spec, embedder=emb,
                              req_embeddings=emb.encode_requirements(REQS), **kw)


class TestEngine:
    BASE = _spec({"/api/v1/refund": {"post": _op("refund v1")}, "/api/v1/checkout": {"post": _op("checkout v1")}})

    def _emb(self, head, refund=(0.80, 0.70), checkout=(0.50, 0.48)):
        emb = FakeEmbedder(len(REQS))
        emb.set(_text(self.BASE, "POST /api/v1/refund"), r0=refund[0])
        emb.set(_text(self.BASE, "POST /api/v1/checkout"), r1=checkout[0])
        if "/api/v2/refund" in head["paths"]:
            emb.set(_text(head, "POST /api/v2/refund"), r0=refund[1])
        if "/api/v2/checkout" in head["paths"]:
            emb.set(_text(head, "POST /api/v2/checkout"), r1=checkout[1])
        return emb

    def test_block_on_regression(self):
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}, "/api/v2/checkout": {"post": _op("checkout v2")}})
        explainer = FakeExplainer()
        r = _review(self.BASE, head, self._emb(head), explainer=explainer)
        assert r.verdict is Verdict.BLOCK and not r.approved
        assert [(v.requirement_id, v.endpoint_id, v.delta) for v in r.violations] == [
            ("REQ-A", "POST /api/v2/refund", -0.1)]
        assert r.violations[0].severity == "Minor Drift"
        assert explainer.calls == ["REQ-A"] and r.violations[0].explanation == "explained REQ-A"
        assert [c.endpoint_id for c in r.clean_changes] == ["POST /api/v2/checkout"]

    def test_pass_when_drops_are_noise(self):
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}, "/api/v2/checkout": {"post": _op("checkout v2")}})
        r = _review(self.BASE, head, self._emb(head, refund=(0.80, 0.78)))
        assert r.verdict is Verdict.PASS and r.approved and r.violations == []

    def test_removed_endpoint_regresses_its_requirement(self):
        head = _spec({"/api/v2/checkout": {"post": _op("checkout v2")}})
        r = _review(self.BASE, head, self._emb(head))
        assert r.verdict is Verdict.BLOCK
        v = r.violations[0]
        assert (v.requirement_id, v.change_status, v.score_v2, v.delta) == ("REQ-A", REMOVED, 0.0, -0.8)
        assert v.severity == "Critical Drift"

    def test_added_endpoint_never_regresses_and_is_not_embedded(self):
        head = _spec({**self.BASE["paths"], "/api/v1/wishlist": {"post": _op("wishlist")}})
        emb = self._emb(head)
        r = _review(self.BASE, head, emb)
        assert r.verdict is Verdict.PASS and emb.calls == []
        assert [c.status for c in r.clean_changes] == [ADDED]

    def test_no_change_passes_with_note(self):
        r = _review(self.BASE, self.BASE, self._emb(self.BASE))
        assert r.verdict is Verdict.PASS and r.diff.is_empty
        assert any("No endpoint changed" in n for n in r.notes)

    def test_thresholds_from_config_change_the_verdict(self):
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}, "/api/v2/checkout": {"post": _op("checkout v2")}})
        r = _review(self.BASE, head, self._emb(head), thresholds=Thresholds(regression_strong=-0.15, regression_weak=-0.20))
        assert r.verdict is Verdict.PASS

    def test_fails_policy(self):
        head = _spec({"/api/v2/checkout": {"post": _op("checkout v2")}})
        blocked = _review(self.BASE, head, self._emb(head))                       # removal: Critical
        assert blocked.fails(FailOn.BLOCK) and blocked.fails(FailOn.CRITICAL) and not blocked.fails(FailOn.NEVER)
        head2 = _spec({"/api/v2/refund": {"post": _op("refund v2")}, "/api/v2/checkout": {"post": _op("checkout v2")}})
        minor = _review(self.BASE, head2, self._emb(head2))                        # Minor only
        assert minor.fails("block") and not minor.fails("critical")

    def test_requirement_embedding_shape_checked(self):
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}})
        emb = self._emb(head)
        with pytest.raises(ValueError, match="rows"):
            review_spec_change(REQS, self.BASE, head, embedder=emb, req_embeddings=np.zeros((1, 67)))

    def test_no_requirements(self):
        with pytest.raises(ValueError):
            review_spec_change([], self.BASE, self.BASE, embedder=FakeEmbedder(0))

    def test_result_serialises(self):
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}, "/api/v2/checkout": {"post": _op("checkout v2")}})
        d = _review(self.BASE, head, self._emb(head)).to_dict()
        assert d["verdict"] == "BLOCK" and d["violations"][0]["endpoint_change_description"] == ""
        assert {c["status"] for c in d["changes"]} == {MODIFIED} and d["thresholds"]["regression_strong"] == -0.05


class TestTraceLinkScoping:
    """A drop only counts on a trace link: the endpoint must be among the
    requirement's top `link_top_k` base-spec matches."""

    BASE = _spec({
        "/api/v1/refund": {"post": _op("refund v1")},
        "/api/v1/a": {"get": _op("strong a")},
        "/api/v1/b": {"get": _op("strong b")},
        "/api/v1/c": {"get": _op("strong c")},
    })
    HEAD = _spec({
        "/api/v1/refund": {"post": _op("refund v2")},
        "/api/v1/a": {"get": _op("strong a")},
        "/api/v1/b": {"get": _op("strong b")},
        "/api/v1/c": {"get": _op("strong c")},
    })

    def _emb(self):
        emb = FakeEmbedder(len(REQS))
        # REQ-B: refund is only its 4th-best endpoint (0.50 vs a/b/c at 0.9/0.8/0.7)
        emb.set(_text(self.BASE, "POST /api/v1/refund"), r1=0.50)
        emb.set(_text(self.HEAD, "POST /api/v1/refund"), r1=0.40)
        for path, s in (("/api/v1/a", 0.9), ("/api/v1/b", 0.8), ("/api/v1/c", 0.7)):
            emb.set(_text(self.BASE, f"GET {path}"), r1=s)
        return emb

    def test_drop_outside_top_k_is_ignored(self):
        r = _review(self.BASE, self.HEAD, self._emb())
        assert r.verdict is Verdict.PASS

    def test_drop_inside_top_k_blocks(self):
        r = _review(self.BASE, self.HEAD, self._emb(), thresholds=Thresholds(link_top_k=4))
        assert r.verdict is Verdict.BLOCK and r.regressed_requirements == ["REQ-B"]

    def test_scoping_can_be_disabled(self):
        r = _review(self.BASE, self.HEAD, self._emb(), thresholds=Thresholds(link_top_k=0))
        assert r.verdict is Verdict.BLOCK


class TestIntentHook:
    BASE = _spec({"/api/v1/refund": {"post": _op("refund v1")}})
    HEAD = _spec({"/api/v1/refund": {"post": _op("refund v1")}, "/api/v1/wishlist": {"post": _op("wishlist")}})

    def _emb(self, ac_score):
        emb = FakeEmbedder(len(REQS) + 1)
        emb.set(_text(self.HEAD, "POST /api/v1/wishlist"), r3=ac_score)
        # the acceptance criterion text embeds onto e_3, like a 4th requirement
        emb.encode_orig = emb.encode

        def encode(texts):
            out = emb.encode_orig(texts)
            for k, t in enumerate(texts):
                if t.startswith("Wishlist"):
                    out[k] = 0
                    out[k, 3] = 1.0
            return out
        emb.encode = encode
        return emb

    def _run(self, ac_score, **kw):
        emb = self._emb(ac_score)
        intent = [{"id": "AC-1", "title": "Wishlist", "text": "users can save products to a wishlist"}]
        return review_spec_change(REQS, self.BASE, self.HEAD, embedder=emb, intent=intent,
                                  req_embeddings=emb.encode_requirements(REQS + [{}])[:3], **kw)

    def test_intent_met_passes(self):
        r = self._run(0.9)
        assert r.verdict is Verdict.PASS and r.intent.verdict.value == "ALLOW"

    def test_partial_intent_warns(self):
        r = self._run(0.45)
        assert r.verdict is Verdict.WARN and r.approved and "Linked-issue intent" in r.markdown

    def test_unmet_intent_warns_by_default_and_can_block(self):
        assert self._run(0.10).verdict is Verdict.WARN
        assert self._run(0.10, block_on_unmet_intent=True).verdict is Verdict.BLOCK


class TestMarkdown:
    def test_layout(self):
        base = _spec({"/api/v1/refund": {"post": _op("refund v1")}})
        head = _spec({"/api/v2/refund": {"post": _op("refund v2")}})
        emb = FakeEmbedder(len(REQS))
        emb.set(_text(base, "POST /api/v1/refund"), r0=0.8)
        emb.set(_text(head, "POST /api/v2/refund"), r0=0.6)
        r = review_spec_change(REQS, base, head, embedder=emb, req_embeddings=emb.encode_requirements(REQS),
                               pr=PullRequestInfo(number=7, title="t", author="a"))
        md = render_markdown(r)
        for needle in ("| PR Number | #7 |", "**FAIL ❌**", "VIOLATION #1", "`REQ-A`", "Merge is BLOCKED",
                       "1 modified, 0 removed, 0 added"):
            assert needle in md


# ---------------------------------------------------------------------------
# 4. Simulated scenarios
# ---------------------------------------------------------------------------

class TestScenarioSpecs:
    @pytest.mark.parametrize("key", list(SIMULATED_PR_SCENARIOS))
    def test_each_scenario_is_one_modified_endpoint(self, key):
        base, head, pr, descs = build_scenario_specs(key)
        d = diff_specs(base, head, descs)
        assert len(d.modified) == 1 and not d.added and not d.removed
        assert d.modified[0].change_description == SIMULATED_PR_SCENARIOS[key]["changes"][0]["change_description"]
        assert pr.number == SIMULATED_PR_SCENARIOS[key]["pr_number"]

    def test_key_resolution(self):
        assert resolve_scenario_key("add_endpoint") == "add_endpoint"
        assert resolve_scenario_key("PR #43: Add optional discount_code field to cart — no violations") == "add_endpoint"
        assert resolve_scenario_key("nope") == "remove_idempotency"

    def test_legacy_threshold_kwargs(self):
        r = PRReviewer(embedder=None, drift_threshold=-0.12, relevance_floor=0.30)
        assert (r.thresholds.regression_weak, r.thresholds.relevance_floor) == (-0.12, 0.30)


@pytest.mark.slow
class TestScenariosOnRealModel:
    """The three built-in PRs on all-mpnet-base-v2. Margins on this model:
    add_endpoint worst pair REQ-07 Δ-0.052 (weak tier needs -0.08);
    remove_idempotency REQ-04 0.819 Δ-0.069 (strong tier, needs -0.05);
    drop_2fa REQ-09 0.565 Δ-0.172; closest non-violation REQ-01 at 0.340, just
    under the 0.35 relevance floor."""

    @pytest.fixture(scope="class")
    def reviewer_and_reqs(self, semantic_embedder):
        reqs = extract_requirements(str(ROOT / "data" / "requirements" / "ecommerce_srs.md"))
        return PRReviewer(embedder=semantic_embedder, explainer=FakeExplainer()), reqs, \
            semantic_embedder.encode_requirements(reqs)

    @pytest.mark.parametrize("key,verdict,regressed", [
        ("add_endpoint", Verdict.PASS, []),
        ("remove_idempotency", Verdict.BLOCK, ["REQ-04"]),
        ("drop_2fa_requirement", Verdict.BLOCK, ["REQ-09"]),
    ])
    def test_verdicts(self, reviewer_and_reqs, key, verdict, regressed):
        reviewer, reqs, req_emb = reviewer_and_reqs
        result = reviewer.review(key, reqs, req_emb)
        assert result.verdict is verdict
        assert result.regressed_requirements == regressed
        assert result.verdict.value == SIMULATED_PR_SCENARIOS[key]["expected_verdict"]

    def test_backward_compatible_wrapper(self, reviewer_and_reqs):
        reviewer, reqs, req_emb = reviewer_and_reqs
        comment, violations = reviewer.run_pr_review("remove_idempotency", reqs, req_emb)
        assert "Merge is BLOCKED" in comment
        v = violations[0]
        assert set(v) >= {"requirement_id", "requirement_text", "endpoint_id", "score_v1", "score_v2",
                          "delta", "severity", "endpoint_change_description", "explanation"}
        assert v["requirement_id"] == "REQ-04" and v["explanation"] == "explained REQ-04"

    def test_req_embeddings_optional(self, reviewer_and_reqs):
        reviewer, reqs, _ = reviewer_and_reqs
        assert reviewer.review("add_endpoint", reqs).verdict is Verdict.PASS
