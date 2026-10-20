"""
src/review/engine.py — Shared specification review engine
=========================================================
One deterministic rule set for every caller: the simulated PR bot
(src/pr_reviewer.py), the CLI and GitHub Action (M2/M4), the GitHub App (M3)
and the API.

    review_spec_change(requirements, spec_base, spec_head, ...)
      1. diff        extract_endpoints_from_spec on both specs; pair endpoints by
                     version-normalised path -> added / removed / modified
                     (modified = text changed beyond the /api/vN segment)
      2. score       embed the base and head text of every removed / modified
                     endpoint, cosine against every requirement
      3. regression  tiered relative-drop rule (is_regression), thresholds from
                     src.config.Thresholds (.traceability.yml), applied only to
                     trace links: pairs where the endpoint is among the
                     requirement's top `link_top_k` matches in the base spec.
                     Without this, a whole-spec change flags loosely related
                     pairs (store_v1 -> v2: REQ-05/06/07 vs the 2FA endpoints,
                     none of which are relevant in the ground truth).
      4. severity    drift_detector.classify_severity (a confirmed regression is
                     at least "Minor Drift")
      5. intent      optional: linked-issue acceptance criteria vs the added and
                     modified head endpoints (EmbeddingAlignmentScorer +
                     ThresholdAlignmentGate)
      6. verdict     BLOCK on any regression; WARN when the intent check is not a
                     clean pass; PASS otherwise
      7. report      ReviewResult (+ GitHub-style Markdown via src.review.report)

The LLM never decides anything here: it is only asked to explain violations
that the deterministic rule already found.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.config import FailOn, Thresholds
from src.drift_detector import classify_severity, normalise_endpoint_path
from src.parser import extract_endpoints_from_spec
from src.similarity import compute_similarity_matrix

logger = logging.getLogger(__name__)

ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"

_SEVERITY_ORDER = {"No Drift": 0, "Stable": 1, "Minor Drift": 2, "Major Drift": 3, "Critical Drift": 4}


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PullRequestInfo:
    """Optional PR metadata for the report header."""

    number: Optional[int] = None
    title: str = ""
    author: str = ""
    branch: str = ""
    url: str = ""


@dataclass(frozen=True)
class EndpointChange:
    """One endpoint that differs between the base and head spec."""

    endpoint_id: str                 # display id: head id if present, else base id
    status: str                      # added | removed | modified
    base_id: Optional[str] = None
    head_id: Optional[str] = None
    base_text: str = ""
    head_text: str = ""
    change_description: str = ""
    kind: str = "endpoint"           # endpoint | function | class (code units, src/review/code.py)
    location: str = ""               # "app/refunds.py:12" for code units

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "endpoint_id": self.endpoint_id, "status": self.status,
            "base_id": self.base_id, "head_id": self.head_id,
            "change_description": self.change_description,
        }
        if self.kind != "endpoint":
            out.update(kind=self.kind, location=self.location)
        return out


@dataclass(frozen=True)
class SpecDiff:
    added: Tuple[EndpointChange, ...]
    removed: Tuple[EndpointChange, ...]
    modified: Tuple[EndpointChange, ...]
    unchanged: Tuple[str, ...]
    # every base-spec endpoint as (id, text): needed to rank trace links
    base_endpoints: Tuple[Tuple[str, str], ...] = ()
    kind: str = "endpoint"           # "code" when the units are Python symbols

    @property
    def changes(self) -> Tuple[EndpointChange, ...]:
        return self.removed + self.modified + self.added

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)


@dataclass
class Violation:
    requirement_id: str
    requirement_text: str
    endpoint_id: str
    change_status: str
    score_v1: float
    score_v2: float
    delta: float
    severity: str
    change_description: str = ""
    explanation: str = ""
    kind: str = "endpoint"           # endpoint | function | class
    location: str = ""               # "app/refunds.py:12" for a code symbol

    @property
    def is_code(self) -> bool:
        return self.kind != "endpoint"

    def to_dict(self) -> Dict[str, Any]:
        """Wire format used by the API / UI (kept from the v1 PR reviewer)."""
        return {
            "artifact_kind": self.kind,
            "location": self.location,
            "requirement_id": self.requirement_id,
            "requirement_text": self.requirement_text,
            "endpoint_id": self.endpoint_id,
            "change_status": self.change_status,
            "score_v1": self.score_v1,
            "score_v2": self.score_v2,
            "delta": self.delta,
            "severity": self.severity,
            "endpoint_change_description": self.change_description,
            "explanation": self.explanation,
        }


@dataclass
class ReviewResult:
    verdict: Verdict
    violations: List[Violation]
    diff: SpecDiff
    clean_changes: List[EndpointChange]
    thresholds: Thresholds
    intent: Optional[Any] = None                 # src.alignment.GateDecision when an intent check ran
    pr: Optional[PullRequestInfo] = None
    provider_info: str = ""
    markdown: str = ""
    notes: List[str] = field(default_factory=list)
    code_diff: Optional[SpecDiff] = None         # changed Python symbols (M5), reviewed with the code embedder

    @property
    def approved(self) -> bool:
        """Merge allowed (PASS or WARN)."""
        return self.verdict is not Verdict.BLOCK

    @property
    def regressed_requirements(self) -> List[str]:
        return sorted({v.requirement_id for v in self.violations})

    @property
    def worst_severity(self) -> Optional[str]:
        if not self.violations:
            return None
        return max((v.severity for v in self.violations), key=lambda s: _SEVERITY_ORDER.get(s, 0))

    @property
    def summary(self) -> str:
        d = self.diff
        parts = [f"{self.verdict.value}: {len(d.modified)} modified, {len(d.removed)} removed, "
                 f"{len(d.added)} added endpoint(s)"]
        if self.code_diff is not None:
            c = self.code_diff
            parts.append(f"code: {len(c.modified)} modified, {len(c.removed)} removed, {len(c.added)} added symbol(s)")
        if self.violations:
            parts.append(f"{len(self.violations)} regression(s) on {', '.join(self.regressed_requirements)}")
        if self.intent is not None:
            parts.append(f"intent: {self.intent.summary}")
        return "; ".join(parts)

    def fails(self, fail_on: FailOn = FailOn.BLOCK) -> bool:
        """Should a CI job / check run fail under the `fail_on` policy?"""
        fail_on = FailOn(fail_on)
        if fail_on is FailOn.NEVER:
            return False
        if fail_on is FailOn.CRITICAL:
            return any(v.severity == "Critical Drift" for v in self.violations)
        return self.verdict is Verdict.BLOCK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "approved": self.approved,
            "summary": self.summary,
            "violations": [v.to_dict() for v in self.violations],
            "changes": [c.to_dict() for c in self.diff.changes],
            "clean_changes": [c.to_dict() for c in self.clean_changes],
            "unchanged_endpoints": len(self.diff.unchanged),
            "code_changes": None if self.code_diff is None else [c.to_dict() for c in self.code_diff.changes],
            "intent": None if self.intent is None else {
                "verdict": self.intent.verdict.value,
                "coverage": self.intent.coverage,
                "summary": self.intent.summary,
                "unmet": list(self.intent.unmet_context_ids),
            },
            "thresholds": self.thresholds.model_dump(),
            "notes": list(self.notes),
            "markdown": self.markdown,
        }


# ---------------------------------------------------------------------------
# Rules (pure functions)
# ---------------------------------------------------------------------------

def is_regression(score_base: float, score_head: float, thresholds: Optional[Thresholds] = None) -> bool:
    """Tiered relative-drop rule.

    Sentence-transformer cosine scores between business prose and API contract
    text sit in a narrow band (~0.3-0.8), so "crossed an absolute threshold"
    is unreliable. A pair regresses when the head score is lower AND either
      - score_base >= strong_relevance  and  delta <= regression_strong, or
      - score_base >= relevance_floor   and  delta <= regression_weak.
    Pairs below relevance_floor were never related and cannot regress.
    """
    t = thresholds or Thresholds()
    if score_head >= score_base:
        return False
    delta = score_head - score_base
    if score_base >= t.strong_relevance:
        return delta <= t.regression_strong
    if score_base >= t.relevance_floor:
        return delta <= t.regression_weak
    return False


def violation_severity(delta: float) -> str:
    """classify_severity, floored at "Minor Drift": a pair the gate blocks is
    never reported as "Stable" / "No Drift" (the tiered rule can flag drops
    smaller than the -0.10 noise band of the matrix drift report)."""
    label = classify_severity(delta)
    return label if _SEVERITY_ORDER.get(label, 0) >= _SEVERITY_ORDER["Minor Drift"] else "Minor Drift"


_VERSION_SEGMENT = re.compile(r"/api/v\d+(?:\.\d+)?")


def _comparable(text: str) -> str:
    """Endpoint text without the /api/vN segment: a pure version bump is not a change."""
    return _VERSION_SEGMENT.sub("", text)


def _index_by_path(endpoints: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for ep in endpoints:
        key = normalise_endpoint_path(ep["id"])
        if key in out:                      # two versions of one path in the same spec
            key = ep["id"]
        out[key] = ep
    return out


def diff_endpoints(
    base_endpoints: Sequence[Dict[str, Any]],
    head_endpoints: Sequence[Dict[str, Any]],
    change_descriptions: Optional[Dict[str, str]] = None,
) -> SpecDiff:
    """Pair endpoints across versions by method + version-normalised path."""
    descs = change_descriptions or {}
    base, head = _index_by_path(base_endpoints), _index_by_path(head_endpoints)

    def desc(*ids: Optional[str]) -> str:
        return next((descs[i] for i in ids if i and i in descs), "")

    added, removed, modified, unchanged = [], [], [], []
    for key in sorted(set(base) | set(head)):
        b, h = base.get(key), head.get(key)
        if b and h:
            if _comparable(b["text"]) == _comparable(h["text"]):
                unchanged.append(h["id"])
                continue
            modified.append(EndpointChange(h["id"], MODIFIED, b["id"], h["id"], b["text"], h["text"],
                                           desc(h["id"], b["id"], key)))
        elif b:
            removed.append(EndpointChange(b["id"], REMOVED, b["id"], None, b["text"], "",
                                          desc(b["id"], key) or "Endpoint removed"))
        else:
            added.append(EndpointChange(h["id"], ADDED, None, h["id"], "", h["text"],
                                        desc(h["id"], key) or "Endpoint added"))
    return SpecDiff(tuple(added), tuple(removed), tuple(modified), tuple(unchanged),
                    tuple((e["id"], e["text"]) for e in base_endpoints))


def diff_specs(
    spec_base: Optional[Dict[str, Any]],
    spec_head: Optional[Dict[str, Any]],
    change_descriptions: Optional[Dict[str, str]] = None,
    *,
    source_base: str = "base",
    source_head: str = "head",
) -> SpecDiff:
    """Diff two OpenAPI specs. A missing side (None) means the spec was added or deleted."""
    base_eps = extract_endpoints_from_spec(spec_base, source_base) if spec_base is not None else []
    head_eps = extract_endpoints_from_spec(spec_head, source_head) if spec_head is not None else []
    return diff_endpoints(base_eps, head_eps, change_descriptions)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _thresholds_of(thresholds: Any) -> Thresholds:
    if thresholds is None:
        return Thresholds()
    if isinstance(thresholds, Thresholds):
        return thresholds
    if hasattr(thresholds, "thresholds"):              # TraceabilityConfig
        return thresholds.thresholds
    return Thresholds.model_validate(thresholds)


def review_endpoint_changes(
    requirements: Sequence[Dict[str, Any]],
    diff: SpecDiff,
    *,
    thresholds: Any = None,
    embedder: Any = None,
    explainer: Any = None,
    req_embeddings: Optional[np.ndarray] = None,
    intent: Optional[Sequence[Any]] = None,
    pr: Optional[PullRequestInfo] = None,
    block_on_unmet_intent: bool = False,
) -> ReviewResult:
    """Score an already-computed diff. `review_spec_change` is the usual entry point."""
    from src.review.report import render_markdown

    t = _thresholds_of(thresholds)
    notes: List[str] = []
    if not requirements:
        raise ValueError("No requirements to review against.")

    scored = [c for c in diff.changes if c.status in (MODIFIED, REMOVED)]
    violations: List[Violation] = []
    regressed_ids = set()

    if scored:
        if embedder is None:
            from src.embedder import SemanticEmbedder
            embedder = SemanticEmbedder()
        if req_embeddings is None:
            req_embeddings = embedder.encode_requirements(list(requirements))
        if req_embeddings.shape[0] != len(requirements):
            raise ValueError(f"req_embeddings has {req_embeddings.shape[0]} rows for "
                             f"{len(requirements)} requirements")

        base_vecs = embedder.encode([c.base_text for c in scored])
        base_scores = compute_similarity_matrix(req_embeddings, base_vecs)          # (N, K)
        rank = _link_ranks(req_embeddings, base_scores, scored, diff, embedder, t.link_top_k)
        head_texts = [c.head_text for c in scored if c.status == MODIFIED]
        head_scores_mod = (compute_similarity_matrix(req_embeddings, embedder.encode(head_texts))
                           if head_texts else np.zeros((len(requirements), 0), dtype=np.float32))

        mod_col = 0
        for k, change in enumerate(scored):
            if change.status == MODIFIED:
                head_col = head_scores_mod[:, mod_col]
                mod_col += 1
            else:                                   # removed: nothing covers the requirement any more
                head_col = np.zeros(len(requirements), dtype=np.float32)
            for i, req in enumerate(requirements):
                s_base, s_head = float(base_scores[i, k]), float(head_col[i])
                if rank is not None and rank[i, k] > t.link_top_k:
                    continue                       # not a trace link of this requirement
                if not is_regression(s_base, s_head, t):
                    continue
                delta = s_head - s_base
                violations.append(Violation(
                    requirement_id=str(req["id"]),
                    requirement_text=str(req.get("text", "")),
                    endpoint_id=change.endpoint_id,
                    change_status=change.status,
                    score_v1=round(s_base, 4),
                    score_v2=round(s_head, 4),
                    delta=round(delta, 4),
                    severity=violation_severity(delta),
                    change_description=change.change_description,
                    kind=change.kind,
                    location=change.location,
                ))
                regressed_ids.add(change.endpoint_id)

    violations.sort(key=lambda v: (v.delta, v.requirement_id))

    if explainer is not None:
        by_id = {c.endpoint_id: c for c in scored}
        for v in violations:
            c = by_id[v.endpoint_id]
            v.explanation = explainer.explain_drift(v.to_dict(), c.base_text, c.head_text)

    clean = [c for c in diff.changes if c.endpoint_id not in regressed_ids]

    # Intent hook: do the added / modified endpoints deliver the linked issue's criteria?
    intent_decision = None
    if intent:
        intent_decision = _check_intent(intent, diff, t, embedder)
        if intent_decision is None:
            notes.append("Intent check skipped: the change adds or modifies no endpoint.")

    verdict = Verdict.PASS
    if violations:
        verdict = Verdict.BLOCK
    elif intent_decision is not None and intent_decision.verdict.value != "ALLOW":
        verdict = Verdict.BLOCK if (block_on_unmet_intent and intent_decision.verdict.value == "BLOCK") \
            else Verdict.WARN
    if diff.is_empty:
        notes.append("No endpoint changed between the two specs." if diff.kind == "endpoint"
                     else "No Python symbol changed in the configured code paths.")

    result = ReviewResult(
        verdict=verdict, violations=violations, diff=diff, clean_changes=clean, thresholds=t,
        intent=intent_decision, pr=pr, notes=notes,
        provider_info=getattr(explainer, "provider_info", "") if explainer is not None else "explanations disabled",
    )
    result.markdown = render_markdown(result)
    logger.info("Spec review: %s", result.summary)
    return result


def _link_ranks(req_embeddings, base_scores, scored, diff: SpecDiff, embedder, top_k: int):
    """(N, K) rank (1 = best) of each scored endpoint among ALL base-spec
    endpoints, per requirement. None when scoping is off or the base list is unknown."""
    if top_k <= 0 or not diff.base_endpoints:
        return None
    changed = {c.base_id for c in scored}
    others = [text for eid, text in diff.base_endpoints if eid not in changed]
    if others:
        other_scores = compute_similarity_matrix(req_embeddings, embedder.encode(others))
        all_scores = np.concatenate([base_scores, other_scores], axis=1)
    else:
        all_scores = base_scores
    # rank = 1 + number of base endpoints that score strictly higher
    return 1 + (all_scores[:, None, :] > base_scores[:, :, None]).sum(axis=2)


def _check_intent(intent: Sequence[Any], diff: SpecDiff, t: Thresholds, embedder: Any):
    from src.alignment import ArtifactUnit, ContextUnit, EmbeddingAlignmentScorer, GatePolicy, ThresholdAlignmentGate

    artifacts = [ArtifactUnit(id=c.endpoint_id, text=c.head_text, kind=c.kind, location=c.location)
                 for c in diff.changes if c.status in (ADDED, MODIFIED)]
    if not artifacts:
        return None
    contexts = [u if isinstance(u, ContextUnit) else ContextUnit(id=str(u["id"]), text=str(u["text"]),
                                                                 title=str(u.get("title", "")))
                for u in intent]
    policy = GatePolicy(compliant_at=t.compliant, partial_at=t.partial)
    if embedder is None:
        from src.embedder import SemanticEmbedder
        embedder = SemanticEmbedder()
    results = EmbeddingAlignmentScorer(embedder=embedder, policy=policy).score(contexts, artifacts)
    return ThresholdAlignmentGate(policy).evaluate(contexts, results)


def review_spec_change(
    requirements: Sequence[Dict[str, Any]],
    spec_base: Optional[Dict[str, Any]],
    spec_head: Optional[Dict[str, Any]],
    *,
    intent: Optional[Sequence[Any]] = None,
    thresholds: Any = None,
    embedder: Any = None,
    explainer: Any = None,
    req_embeddings: Optional[np.ndarray] = None,
    pr: Optional[PullRequestInfo] = None,
    change_descriptions: Optional[Dict[str, str]] = None,
    source_base: str = "base",
    source_head: str = "head",
    block_on_unmet_intent: bool = False,
) -> ReviewResult:
    """Review an OpenAPI change against the requirement baseline.

    Args:
        requirements:  baseline requirement dicts ({id, title, text}) from the SRS.
        spec_base / spec_head: parsed OpenAPI dicts; None = spec absent on that side.
        intent:        optional acceptance criteria (ContextUnit or {id, text, title}).
        thresholds:    src.config.Thresholds, a TraceabilityConfig, a dict, or None (defaults).
        embedder:      src.embedder.SemanticEmbedder (created on demand).
        explainer:     optional ComplianceExplainer; when None no LLM is called.
        req_embeddings: precomputed requirement vectors (same order as `requirements`).
        change_descriptions: endpoint id (or normalised path) -> human description.
        block_on_unmet_intent: escalate an intent BLOCK to a review BLOCK (default: WARN).
    """
    diff = diff_specs(spec_base, spec_head, change_descriptions,
                      source_base=source_base, source_head=source_head)
    return review_endpoint_changes(
        requirements, diff, thresholds=thresholds, embedder=embedder, explainer=explainer,
        req_embeddings=req_embeddings, intent=intent, pr=pr, block_on_unmet_intent=block_on_unmet_intent,
    )
