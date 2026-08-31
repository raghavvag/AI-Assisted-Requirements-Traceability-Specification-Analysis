"""
src/alignment/base.py — Semantic Alignment Engine: contracts
============================================================
Pure interfaces + dataclasses. No heavy imports here so this module stays cheap
to import and easy to test.

Glossary
--------
ContextUnit    One atomic thing the business asked for (an SRS requirement, a Jira
               user story, a single acceptance-criterion bullet).
ArtifactUnit   One atomic thing engineering produced (an OpenAPI endpoint, a Python
               function, a changed diff hunk, a docstring).
AlignmentScorer  Given contexts + artefacts, produce AlignmentResult rows
               (score in [0, 1] + evidence). The model lives behind this.
AlignmentGate  Turn a set of AlignmentResults + a GatePolicy into an ALLOW / WARN /
               BLOCK decision plus a human-readable Markdown comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextUnit:
    """One atomic business requirement / story / acceptance criterion."""

    id: str
    text: str
    title: str = ""
    kind: str = "requirement"            # requirement | user_story | acceptance_criterion | adr
    metadata: Dict[str, str] = field(default_factory=dict)

    def embedding_text(self) -> str:
        """Text handed to the scorer. Override upstream by adjusting title/text."""
        return f"{self.title}. {self.text}".strip(". ").strip() or self.text


@dataclass(frozen=True)
class ArtifactUnit:
    """One atomic implementation unit."""

    id: str
    text: str
    kind: str = "endpoint"               # endpoint | function | class | diff_hunk | docstring
    location: str = ""                   # "store_v1.json#/paths/~1refund" or "app/api.py:42"
    metadata: Dict[str, str] = field(default_factory=dict)

    def embedding_text(self) -> str:
        return self.text


class AlignmentBand(str, Enum):
    COMPLIANT = "Compliant"
    PARTIAL = "Partial"
    NON_COMPLIANT = "Non-Compliant"


@dataclass(frozen=True)
class AlignmentResult:
    """The alignment of one (context, artefact) pair."""

    context_id: str
    artifact_id: str
    score: float                         # cosine / cross-encoder probability, in [0, 1]
    method: str = "embedding-cosine"
    band: AlignmentBand = AlignmentBand.NON_COMPLIANT
    evidence: str = ""                   # short phrase(s) supporting the score
    rank: Optional[int] = None           # rank of this artefact for its context (1 = best)


class GateVerdict(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass
class GatePolicy:
    """Thresholds / policy the gate applies. Serialisable to `.alignment.yml`."""

    compliant_at: float = 0.60           # score >= => Compliant
    partial_at: float = 0.40             # score >= => Partial
    min_coverage: float = 0.80           # fraction of contexts that must be >= partial_at
    block_below_coverage: bool = True    # coverage < min_coverage => BLOCK (else WARN)
    warn_on_partial: bool = True         # any Partial context => at least WARN

    def band(self, score: float) -> AlignmentBand:
        if score >= self.compliant_at:
            return AlignmentBand.COMPLIANT
        if score >= self.partial_at:
            return AlignmentBand.PARTIAL
        return AlignmentBand.NON_COMPLIANT


@dataclass
class GateDecision:
    verdict: GateVerdict
    coverage: float
    summary: str
    markdown_comment: str
    unmet_context_ids: List[str] = field(default_factory=list)
    results: List[AlignmentResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict is GateVerdict.BLOCK


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class ContextSource(Protocol):
    """Yields the business side of the comparison."""

    def load(self) -> List[ContextUnit]: ...


@runtime_checkable
class ArtifactSource(Protocol):
    """Yields the implementation side of the comparison."""

    def load(self) -> List[ArtifactUnit]: ...


@runtime_checkable
class AlignmentScorer(Protocol):
    """
    Scores every (context, artefact) pair. Implementations may be embedding
    bi-encoders, code-aware Siamese-BERT models, or cross-encoder re-rankers —
    the rest of the system does not care which.

    Contract:
      - returns one AlignmentResult per (context, artefact) pair
      - score in [0.0, 1.0]
      - deterministic for identical inputs
      - `rank` populated per context (1 = best-matching artefact)
    """

    def score(
        self,
        contexts: List[ContextUnit],
        artifacts: List[ArtifactUnit],
    ) -> List[AlignmentResult]: ...


@runtime_checkable
class AlignmentGate(Protocol):
    """Turns scored pairs + policy into an ALLOW / WARN / BLOCK decision."""

    def evaluate(
        self,
        contexts: List[ContextUnit],
        results: List[AlignmentResult],
        policy: Optional[GatePolicy] = None,
    ) -> GateDecision: ...
