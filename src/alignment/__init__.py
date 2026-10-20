"""
src/alignment/ — Semantic Alignment Engine
==========================================
A model-agnostic abstraction for scoring how well an *implementation artefact*
(OpenAPI endpoint, code symbol, changed hunk) satisfies a *business context unit*
(SRS requirement, Jira story, acceptance criterion), and gating a workflow on it.

The v1.0 traceability pipeline is one instantiation:
    SRSContextSource + OpenAPIArtifactSource + EmbeddingAlignmentScorer + ThresholdAlignmentGate
Code-aware (M5):
    PythonASTArtifactSource / GitDiffArtifactSource + SiameseBertAlignmentScorer
    (or CompositeAlignmentScorer, which routes each unit by kind)

See docs/architecture/semantic-alignment-roadmap.md for the full design and roadmap.
"""

from src.alignment.base import (
    AlignmentGate,
    AlignmentResult,
    AlignmentScorer,
    ArtifactSource,
    ArtifactUnit,
    ContextSource,
    ContextUnit,
    GateDecision,
    GatePolicy,
)
from src.alignment.gate import ThresholdAlignmentGate
from src.alignment.scorer import CompositeAlignmentScorer, EmbeddingAlignmentScorer, SiameseBertAlignmentScorer
from src.alignment.sources import (
    GitDiffArtifactSource,
    OpenAPIArtifactSource,
    PythonASTArtifactSource,
    SRSContextSource,
)

__all__ = [
    "AlignmentGate",
    "AlignmentResult",
    "AlignmentScorer",
    "ArtifactSource",
    "ArtifactUnit",
    "ContextSource",
    "ContextUnit",
    "GateDecision",
    "GatePolicy",
    "ThresholdAlignmentGate",
    "EmbeddingAlignmentScorer",
    "SiameseBertAlignmentScorer",
    "CompositeAlignmentScorer",
    "OpenAPIArtifactSource",
    "SRSContextSource",
    "PythonASTArtifactSource",
    "GitDiffArtifactSource",
]
