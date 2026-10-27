"""
src/srs/noise.py — Boilerplate and formatting noise removal
===========================================================
Formal SRS documents wrap a few dozen requirements in a lot of text that must
never reach the embedding model: cover pages, revision history, table of
contents, glossaries, references, appendices, figure captions, page numbers.

Two layers:
  * clean_text()     character-level cleanup applied by every reader
  * filter_blocks()  block-level removal (blocklisted sections, TOC lines,
                     captions, page numbers, glyph-only lines)

Every removed block is counted in the ExtractionReport by reason.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence

from src.srs.blocks import Block, Figure, Heading, Paragraph, Table
from src.srs.options import ExtractionOptions
from src.srs.report import ExtractionReport

# ---------------------------------------------------------------------------
# Character-level cleanup
# ---------------------------------------------------------------------------

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
})
# Bullet glyphs Word / PDF exporters leave at the start of list items. ASCII "-"
# and "*" only count when followed by whitespace (so "-5%" survives).
_LEADING_BULLET = re.compile(r"^\s*(?:[•▪▫◦●○■□➢►▶"
                             r"✓✔·]+|[-*+](?=\s))\s*")
_WS = re.compile(r"\s+")


def clean_text(text: str, *, collapse: bool = True) -> str:
    """Normalise text for embedding.

    NFKC (folds NBSP, ligatures, full-width forms), smart quotes -> ASCII,
    zero-width characters / soft hyphens removed, leading bullet glyph removed,
    whitespace collapsed. Dashes are intentionally kept: the plain-text SRS uses
    an em dash too, and identical text keeps scores identical across formats.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).translate(_QUOTES)
    t = _LEADING_BULLET.sub("", t)
    if collapse:
        t = _WS.sub(" ", t).strip()
    else:
        t = "\n".join(_WS.sub(" ", line).strip() for line in t.splitlines()).strip()
    return t


# ---------------------------------------------------------------------------
# Section blocklist
# ---------------------------------------------------------------------------

_BLOCKLIST = (
    "table of contents", "contents", "list of figures", "list of tables",
    "revision history", "document history", "version history", "change log", "changelog",
    "change history", "record of changes", "document control",
    "document conventions", "conventions", "typographical conventions",
    "intended audience", "reading suggestions", "audience and reading suggestions",
    "definitions", "acronyms", "abbreviations", "glossary", "terminology",
    "references", "bibliography", "related documents",
    "appendix", "annex", "index",
    "approval", "approvals", "sign-off", "sign off", "signatures", "distribution list",
    "to be determined list", "tbd list", "issues list", "open issues",
)

_BLOCKLIST_SET = frozenset(_BLOCKLIST) | {"terms", "standards"}
# "<word> of ..." headings that are always boilerplate ("Glossary of Terms",
# "List of Abbreviations", "Index of Figures").
_BOILER_OF_PREFIXES = ("glossary", "list", "index", "table", "definition", "definitions")
_NUMBERING = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*\.?|[a-z]\.|[ivxlc]+\.)\s+", re.IGNORECASE)


def heading_key(text: str) -> str:
    """Heading text without numbering / punctuation, lower-cased."""
    t = _NUMBERING.sub("", clean_text(text).lower())
    t = re.sub(r"[^a-z0-9\- ]+", " ", t)
    return _WS.sub(" ", t).strip()


def _is_boilerplate_part(part: str) -> bool:
    return part in _BLOCKLIST_SET or any(part.startswith(f"{p} of ") for p in _BOILER_OF_PREFIXES)


def is_blocklisted_heading(text: str, extra: Sequence[str] = ()) -> bool:
    """True when a heading names a boilerplate section.

    The heading is split on "," "&" "/" ";" and "and"; it is boilerplate only if
    EVERY part is a boilerplate term. So "Definitions, Acronyms and
    Abbreviations" is skipped, while feature sections that merely contain such a
    word ("Cart Contents", "Order Approval", "Search Index") are kept.
    """
    raw = _NUMBERING.sub("", clean_text(text).lower())
    if re.match(r"^\s*(?:appendix|annex)\b", raw):
        return True
    parts = [heading_key(p) for p in re.split(r",|&|/|;|\band\b", raw)]
    parts = [p for p in parts if p]
    if parts and all(_is_boilerplate_part(p) for p in parts):
        return True
    key = heading_key(text)
    return any(e.strip().lower() in key for e in extra if e.strip())


# ---------------------------------------------------------------------------
# Line-level noise
# ---------------------------------------------------------------------------

_CAPTION = re.compile(r"^\s*(?:figure|fig\.|table|diagram|exhibit|chart)\s*[0-9]+(?:[.\-][0-9]+)*\b", re.IGNORECASE)
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?[0-9]{1,4}(?:\s*(?:of|/)\s*[0-9]{1,4})?\s*$", re.IGNORECASE)
_DOT_LEADER = re.compile(r"(?:\.{4,}|…{2,}|(?:\. ){4,})\s*[0-9ivxlc]{1,4}\s*$", re.IGNORECASE)
_GLYPHS_ONLY = re.compile(r"^[\W_]+$")


def is_caption(p: Paragraph) -> bool:
    return (p.style or "").lower() == "caption" or bool(_CAPTION.match(p.text))


def paragraph_noise_reason(p: Paragraph) -> Optional[str]:
    """Return a skip reason for a noise paragraph, else None."""
    style = (p.style or "").lower()
    text = p.text.strip()
    if not text:
        return "empty"
    if style.startswith("toc") or style in {"table of figures", "index 1", "index 2"}:
        return "toc"
    if is_caption(p):
        return "caption"
    if _DOT_LEADER.search(text):
        return "toc"
    if _PAGE_NUMBER.match(text):
        return "page_number"
    if _GLYPHS_ONLY.match(text):
        return "glyphs"
    return None


# ---------------------------------------------------------------------------
# Block filter
# ---------------------------------------------------------------------------

def filter_blocks(
    blocks: Iterable[Block],
    options: ExtractionOptions,
    report: ExtractionReport,
) -> List[Block]:
    """Drop blocklisted sections (heading + everything until a heading of the
    same or higher level) and noise paragraphs. Tables and figures inside a
    blocklisted section go with it (revision-history tables, glossary tables,
    appendix diagrams), so those figures are never sent to an interpreter."""
    out: List[Block] = []
    skip_level: Optional[int] = None
    skip_name = ""

    for block in blocks:
        if isinstance(block, Heading):
            if skip_level is not None and block.level <= skip_level:
                skip_level = None
            if skip_level is not None:
                report.skip(f"blocklisted_section:{skip_name}")
                continue
            if is_blocklisted_heading(block.text, options.extra_blocklist):
                skip_level, skip_name = block.level, clean_text(block.text)
                report.skip(f"blocklisted_section:{skip_name}")
                continue
            if not block.text.strip():
                report.skip("empty")
                continue
            out.append(block)
            continue

        if skip_level is not None:
            report.skip(f"blocklisted_section:{skip_name}")
            continue

        if isinstance(block, Paragraph):
            reason = paragraph_noise_reason(block)
            if reason:
                report.skip(reason)
                continue
            out.append(block)
        elif isinstance(block, Table):
            if all(not cell.strip() for row in block.rows for cell in row):
                report.skip("empty_table")
                continue
            out.append(block)
        elif isinstance(block, Figure):
            out.append(block)
    return out
