"""
src/srs/options.py — Tunable extraction options
==============================================
Defaults work for IEEE 830 / ISO 29148 style documents. Projects with an unusual
ID scheme or extra boilerplate sections override them (later via the
`srs_extraction:` block of `.traceability.yml`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

# Requirement tag, e.g. REQ-04, FR1, FR-1.2, NFR 3, BNK-REQ-01, SRS-FR-007, US-12.
# An optional domain prefix ("BNK-", "TF-") is allowed in front of the kind
# keyword; the number may be dotted ("FR-5.1").
DEFAULT_ID_PATTERN = (
    r"(?:[A-Z][A-Z0-9]{0,9}[-_.])?"
    r"(?:REQ|FR|NFR|SRS|SR|UR|BR|SYS|FUNC|US)"
    r"[-_. ]?\d{1,4}(?:\.\d{1,3})*"
)


@dataclass(frozen=True)
class ExtractionOptions:
    """Knobs for the SRS extraction pipeline."""

    # Regex (no anchors, no groups required) matching one requirement ID.
    id_pattern: Optional[str] = None
    # Keep non-functional requirements (NFR-*, "Performance", "Security", ...).
    include_nfr: bool = True
    # Extra section headings to skip entirely, matched case-insensitively as a
    # substring of the heading text (numbering stripped).
    extra_blocklist: Tuple[str, ...] = field(default_factory=tuple)
    # Length warnings. all-mpnet-base-v2 truncates input at 384 word pieces.
    max_words: int = 350
    min_words: int = 5

    @property
    def effective_id_pattern(self) -> str:
        return self.id_pattern or DEFAULT_ID_PATTERN

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "ExtractionOptions":
        """Build from a plain dict (e.g. the `srs_extraction` YAML block).
        Unknown keys are ignored so config files stay forward compatible."""
        if not data:
            return cls()
        kwargs: Dict[str, Any] = {}
        if data.get("id_pattern"):
            kwargs["id_pattern"] = str(data["id_pattern"])
        if "include_nfr" in data:
            kwargs["include_nfr"] = bool(data["include_nfr"])
        if data.get("extra_blocklist"):
            kwargs["extra_blocklist"] = tuple(str(s) for s in data["extra_blocklist"])
        for key in ("max_words", "min_words"):
            if key in data:
                kwargs[key] = int(data[key])
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["extra_blocklist"] = list(self.extra_blocklist)
        return d
