"""
test_alignment.py — Semantic Alignment Engine contract + integration tests
=========================================================================
Verifies the v1.0 pipeline, re-expressed against src/alignment/, produces the
same conclusions (REQ-04 -> refund endpoint, high coverage on the compliant spec).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.alignment import (
    EmbeddingAlignmentScorer,
    OpenAPIArtifactSource,
    SRSContextSource,
    ThresholdAlignmentGate,
)
from src.alignment.base import AlignmentResult, GatePolicy, GateVerdict

SRS_PATH = ROOT / "data" / "requirements" / "ecommerce_srs.md"
V1_PATH = ROOT / "data" / "api_specs" / "store_v1.json"
V2_PATH = ROOT / "data" / "api_specs" / "store_v2.json"


@pytest.fixture(scope="module")
def contexts():
    return SRSContextSource(str(SRS_PATH)).load()


@pytest.fixture(scope="module")
def artifacts_v1():
    return OpenAPIArtifactSource(str(V1_PATH)).load()


@pytest.fixture(scope="module")
def scorer():
    return EmbeddingAlignmentScorer()


@pytest.fixture(scope="module")
def results_v1(scorer, contexts, artifacts_v1):
    return scorer.score(contexts, artifacts_v1)


class TestSources:
    def test_srs_source_yields_10_contexts(self, contexts):
        assert len(contexts) == 10
        assert all(c.id.startswith("REQ-") for c in contexts)

    def test_openapi_source_yields_20_artifacts(self, artifacts_v1):
        assert len(artifacts_v1) == 20
        assert all(a.kind == "endpoint" for a in artifacts_v1)
        assert all(a.location for a in artifacts_v1)


class TestScorerContract:
    def test_one_result_per_pair(self, results_v1, contexts, artifacts_v1):
        assert len(results_v1) == len(contexts) * len(artifacts_v1)

    def test_scores_in_bounds(self, results_v1):
        assert all(0.0 <= r.score <= 1.0 for r in results_v1)

    def test_ranks_populated_per_context(self, results_v1, artifacts_v1):
        by_ctx = {}
        for r in results_v1:
            by_ctx.setdefault(r.context_id, []).append(r)
        for rows in by_ctx.values():
            ranks = sorted(r.rank for r in rows)
            assert ranks == list(range(1, len(artifacts_v1) + 1))

    def test_determinism(self, scorer, contexts, artifacts_v1):
        a = scorer.score(contexts, artifacts_v1)
        b = scorer.score(contexts, artifacts_v1)
        assert [r.score for r in a] == [r.score for r in b]

    def test_empty_inputs(self, scorer):
        assert scorer.score([], []) == []


class TestSemantics:
    def test_req04_best_match_is_refund(self, results_v1):
        req04 = [r for r in results_v1 if r.context_id == "REQ-04"]
        best = max(req04, key=lambda r: r.score)
        assert "refund" in best.artifact_id.lower()

    def test_req01_best_match_is_auth(self, results_v1):
        req01 = [r for r in results_v1 if r.context_id == "REQ-01"]
        best = max(req01, key=lambda r: r.score)
        assert "auth" in best.artifact_id.lower() or "login" in best.artifact_id.lower()


class TestGate:
    def test_compliant_spec_allows_or_warns(self, contexts, results_v1):
        decision = ThresholdAlignmentGate().evaluate(contexts, results_v1)
        assert decision.verdict in (GateVerdict.ALLOW, GateVerdict.WARN)
        assert decision.coverage >= 0.8
        assert "Semantic Alignment Review" in decision.markdown_comment

    def test_strict_policy_can_block(self, contexts, results_v1):
        strict = GatePolicy(compliant_at=0.95, partial_at=0.9, min_coverage=0.99)
        decision = ThresholdAlignmentGate().evaluate(contexts, results_v1, strict)
        assert decision.verdict is GateVerdict.BLOCK
        assert decision.blocked
        assert decision.unmet_context_ids
