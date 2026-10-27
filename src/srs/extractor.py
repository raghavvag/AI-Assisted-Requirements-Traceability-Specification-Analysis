"""
src/srs/extractor.py — Orchestration
====================================
    file -> reader (docx | pdf | md/txt) -> blocks -> captions -> noise filter
         -> figure interpretation (src/srs/diagrams.py)
         -> Table / Tagged / Shall / Diagram strategies -> normaliser
         -> requirements + report
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from src.srs.blocks import Block
from src.srs.diagrams import DiagramStrategy, attach_captions, interpret_figures
from src.srs.errors import NoRequirementsFoundError, UnsupportedFormatError
from src.srs.noise import filter_blocks
from src.srs.normalise import canonical_id, classify_type, finalize
from src.srs.options import ExtractionOptions
from src.srs.readers.docx_reader import read_docx
from src.srs.readers.markdown_reader import LEGACY_PATTERN, legacy_extract, read_markdown, read_text_file
from src.srs.readers.pdf_reader import read_pdf
from src.srs.report import ExtractionReport
from src.srs.strategies import (
    SectionContext,
    ShallStatementStrategy,
    TableStrategy,
    TaggedStrategy,
    TagMatcher,
)

logger = logging.getLogger(__name__)

Requirement = Dict[str, object]

SUPPORTED_EXTENSIONS: Dict[str, str] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}
_REJECTED: Dict[str, str] = {
    ".doc": "Legacy Word 97-2003 (.doc) files are not supported. Open the file in Word and save it as .docx.",
    ".docm": "Macro-enabled documents (.docm) are not accepted. Save the SRS as a plain .docx.",
    ".dotx": "That is a Word template. Create a document from it and upload the .docx.",
    ".rtf": "RTF is not supported. Save the SRS as .docx.",
    ".odt": "OpenDocument (.odt) is not supported. Save the SRS as .docx.",
}
LEGACY_STRATEGY = "legacy_markdown"


def detect_format(path: Union[str, Path]) -> str:
    ext = Path(path).suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[ext]
    if ext in _REJECTED:
        raise UnsupportedFormatError(_REJECTED[ext])
    raise UnsupportedFormatError(
        f"Unsupported SRS file type '{ext or '(none)'}'. Use .docx, .pdf, .md or .txt."
    )


def extract_from_blocks(
    blocks: Sequence[Block],
    options: ExtractionOptions,
    report: ExtractionReport,
    *,
    source_file: str = "",
    source_format: str = "",
) -> List[Requirement]:
    """Run noise filtering + figure interpretation + strategies + normalisation
    on a block stream."""
    report.n_blocks = report.n_blocks or len(blocks)
    clean = filter_blocks(attach_captions(blocks), options, report)
    clean = interpret_figures(clean, options, report)
    ctx = SectionContext(clean)
    tags = TagMatcher(options.effective_id_pattern)

    tables = TableStrategy(tags)
    candidates, consumed = tables.extract(clean, ctx, report)
    candidates += TaggedStrategy(tags, tables).extract(clean, ctx, report, consumed)
    if not candidates:
        candidates = ShallStatementStrategy().extract(clean, ctx, report)
    from_figures, figure_refs = DiagramStrategy(tags).extract(
        clean, ctx, report, {canonical_id(c.raw_id) for c in candidates})
    candidates += from_figures

    reqs = finalize(candidates, source_file=source_file, source_format=source_format,
                    options=options, report=report, figure_refs=figure_refs)
    # Strategy counts after de-duplication, so they add up to n_requirements.
    report.by_strategy.clear()
    for r in reqs:
        report.found(str(r["strategy"]))
    report.n_requirements = len(reqs)
    return reqs


def _legacy(raw: str, source_file: str, source_format: str, report: ExtractionReport) -> List[Requirement]:
    reqs = legacy_extract(raw)
    line_of = {m.group(1).strip(): raw.count("\n", 0, m.start()) + 1 for m in LEGACY_PATTERN.finditer(raw)}
    out: List[Requirement] = []
    for r in reqs:
        out.append({
            **r,
            "type": classify_type(r["id"], ()),
            "priority": None,
            "section": "",
            "raw_id": r["id"],
            "id_source": "document",
            "acceptance_criteria": None,
            "rationale": None,
            "origin": None,
            "strategy": LEGACY_STRATEGY,
            "source": {"file": source_file, "format": source_format,
                       "locator": f"line {line_of.get(r['id'], 0)}"},
        })
    report.found(LEGACY_STRATEGY, len(out))
    report.n_requirements = len(out)
    return out


def extract_requirements_with_report(
    path: Union[str, Path],
    options: Optional[ExtractionOptions] = None,
    *,
    display_name: Optional[str] = None,
) -> Tuple[List[Requirement], ExtractionReport]:
    """Extract requirements from an SRS file of any supported format.

    Raises:
        FileNotFoundError        the file does not exist
        UnsupportedFormatError   .doc / .docm / unknown extension
        InvalidDocumentError     corrupt, encrypted, zip bomb
        ScannedPdfError          PDF without a text layer
        NoRequirementsFoundError nothing matched (the report says what was seen)
    All four extraction errors subclass ValueError.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"SRS file not found: {path}")
    options = options or ExtractionOptions()
    fmt = detect_format(path)
    name = display_name or path.name
    report = ExtractionReport(source_file=name, format=fmt, options_used=options.to_dict())
    tags = TagMatcher(options.effective_id_pattern)

    if fmt in ("markdown", "text"):
        raw = read_text_file(path)
        legacy = _legacy(raw, name, fmt, report)
        if legacy:
            return legacy, report
        blocks = read_markdown(raw, report, is_text=True, tag_line=tags.line_start)
    elif fmt == "docx":
        blocks = read_docx(path, report)
    else:
        blocks = read_pdf(path, report, tag_line=tags.line_start)

    report.n_blocks = len(blocks)
    reqs = extract_from_blocks(blocks, options, report, source_file=name, source_format=fmt)
    if not reqs:
        raise NoRequirementsFoundError(_nothing_found_message(name, report), report)
    logger.info("SRS %s: %s", name, report.summary())
    return reqs, report


def _nothing_found_message(name: str, report: ExtractionReport) -> str:
    msg = (
        f"No requirements were found in '{name}'. The parser looks for (1) requirement tables with an "
        "ID column and a Description/Requirement column, (2) paragraphs or headings that start with an "
        "ID such as FR-1, REQ-04 or [NFR-2], and (3) 'shall'/'must' statements inside a Requirements section."
    )
    if report.n_blocks == 0:
        msg += " The document contains no readable text."
    skipped = sum(report.skipped.values())
    if skipped:
        msg += f" {skipped} block(s) were skipped as boilerplate (see report.skipped)."
    return msg


def extract_requirements(path: Union[str, Path], options: Optional[ExtractionOptions] = None) -> List[Requirement]:
    return extract_requirements_with_report(path, options)[0]


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "detect_format",
    "extract_from_blocks",
    "extract_requirements",
    "extract_requirements_with_report",
]
