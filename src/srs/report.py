"""
src/srs/report.py — ExtractionReport
====================================
A record of what the extractor found, which strategy found it, what it skipped
and why. It is shown in the UI before analysis runs and returned in the 422
response when nothing could be extracted, so users can fix their document
instead of guessing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ExtractionReport:
    source_file: str = ""
    format: str = ""
    n_requirements: int = 0
    n_blocks: int = 0
    by_strategy: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    duplicates: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    options_used: Dict[str, Any] = field(default_factory=dict)

    def skip(self, reason: str, n: int = 1) -> None:
        if n > 0:
            self.skipped[reason] += n

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def found(self, strategy: str, n: int = 1) -> None:
        if n > 0:
            self.by_strategy[strategy] += n

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file": self.source_file,
            "format": self.format,
            "n_requirements": self.n_requirements,
            "n_blocks": self.n_blocks,
            "by_strategy": dict(self.by_strategy),
            "skipped": dict(self.skipped),
            "duplicates": list(self.duplicates),
            "warnings": list(self.warnings),
            "options_used": dict(self.options_used),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractionReport":
        return cls(
            source_file=data.get("source_file", ""),
            format=data.get("format", ""),
            n_requirements=int(data.get("n_requirements", 0)),
            n_blocks=int(data.get("n_blocks", 0)),
            by_strategy=Counter(data.get("by_strategy", {})),
            skipped=Counter(data.get("skipped", {})),
            duplicates=list(data.get("duplicates", [])),
            warnings=list(data.get("warnings", [])),
            options_used=dict(data.get("options_used", {})),
        )

    def summary(self) -> str:
        """One line for logs / CLI output."""
        strategies = ", ".join(f"{n} from {k}" for k, n in self.by_strategy.most_common())
        return (
            f"{self.n_requirements} requirement(s) from {self.format or '?'}"
            + (f" ({strategies})" if strategies else "")
            + (f"; {len(self.warnings)} warning(s)" if self.warnings else "")
        )
