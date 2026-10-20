"""
src/srs/readers/markdown_reader.py — Markdown / plain text -> requirements
=========================================================================
Two paths:

  1. legacy_extract()  — the original v1 regex parser, unchanged. Plain-text /
     Markdown SRS files in the "REQ-N: Title" + body format keep producing
     byte-identical output (the 48 pipeline tests and all published metrics
     depend on it).
  2. read_markdown()   — fallback when the legacy pattern finds nothing:
     Markdown is turned into the common block stream (headings, pipe tables,
     list items, paragraphs) so the format-independent strategies can run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from src.srs.blocks import Block, BlockKind, Heading, LocatorFactory, Paragraph, Row, Table
from src.srs.noise import clean_text
from src.srs.report import ExtractionReport

# ---------------------------------------------------------------------------
# Legacy path (v1 behaviour)
# ---------------------------------------------------------------------------

# Accepts a bare "REQ-4:" as well as a domain-prefixed id such as "TF-REQ-01:"
# or "AUTH-REQ-12:". Leading Markdown heading markers ("## ") are tolerated.
LEGACY_PATTERN = re.compile(
    r"^\s{0,3}#{0,4}\s*((?:[A-Za-z][A-Za-z0-9]{0,7}-)?REQ-\d+):\s*(.+)$",
    re.MULTILINE,
)


def read_text_file(path: Union[str, Path]) -> str:
    """Read a text SRS. UTF-8 (with or without BOM) first, then Windows-1252,
    which is what Notepad-era SRS exports usually are."""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def legacy_extract(raw_text: str) -> List[Dict[str, str]]:
    """The original regex parser: split on "(PREFIX-)REQ-N: Title" lines; the
    body runs to the next match or EOF. Returns [] when nothing matches."""
    matches = list(LEGACY_PATTERN.finditer(raw_text))
    requirements: List[Dict[str, str]] = []
    for i, match in enumerate(matches):
        req_id = match.group(1).strip()
        req_title = match.group(2).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        body_text = raw_text[body_start:body_end].strip()
        full_text = re.sub(r"\s+", " ", f"{req_title}. {body_text}").strip()
        requirements.append({"id": req_id, "title": req_title, "text": full_text})
    return requirements


# ---------------------------------------------------------------------------
# Block path
# ---------------------------------------------------------------------------

_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_UNDERLINE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^\s{0,6}(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
_HR = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(?=\S)(.+?)(?<=\S)\1")


def _strip_inline_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links -> label
    text = re.sub(r"<!--.*?-->", "", text)                    # comments
    prev = None
    while prev != text:                                        # nested emphasis
        prev, text = text, _EMPHASIS.sub(r"\2", text)
    return text


def _split_row(line: str) -> Row:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return tuple(clean_text(_strip_inline_markdown(c)) for c in re.split(r"(?<!\\)\|", inner))


def read_markdown(
    source: Union[str, Path],
    report: Optional[ExtractionReport] = None,
    *,
    is_text: bool = False,
    tag_line: Optional["re.Pattern[str]"] = None,
) -> List[Block]:
    """Markdown (or plain text) -> block stream. `source` is a path, or the raw
    text itself when `is_text=True`.

    `tag_line`, when given, matches lines that start a requirement ("FR-3: ...").
    Such a line always becomes its own paragraph, so the body that follows on the
    next line is not glued onto the title."""
    raw = source if is_text else read_text_file(source)
    lines = str(raw).splitlines()
    loc = LocatorFactory()
    blocks: List[Block] = []
    para: List[str] = []

    def flush_para() -> None:
        if para:
            text = clean_text(_strip_inline_markdown(" ".join(para)))
            if text:
                blocks.append(Paragraph(text=text, locator=loc.next(BlockKind.PARAGRAPH)))
            para.clear()

    i = 0
    in_code = False
    # Skip YAML front matter.
    if lines and lines[0].strip() == "---":
        end = next((k for k in range(1, len(lines)) if lines[k].strip() in ("---", "...")), None)
        if end is not None:
            i = end + 1

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush_para()
            in_code = not in_code
            i += 1
            continue
        if in_code:
            i += 1
            continue

        m = _ATX_HEADING.match(line)
        if m:
            flush_para()
            text = clean_text(_strip_inline_markdown(m.group(2)))
            if text:
                blocks.append(Heading(level=len(m.group(1)), text=text, locator=loc.next(BlockKind.HEADING)))
            i += 1
            continue

        if para and _SETEXT_UNDERLINE.match(line) and len(para) == 1:
            level = 1 if line.strip().startswith("=") else 2
            text = clean_text(_strip_inline_markdown(para[0]))
            para.clear()
            blocks.append(Heading(level=level, text=text, locator=loc.next(BlockKind.HEADING)))
            i += 1
            continue

        if _HR.match(line):
            flush_para()
            i += 1
            continue

        if _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1]):
            flush_para()
            rows: List[Row] = [_split_row(line)]
            i += 2
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(Table(rows=tuple(rows), locator=loc.next(BlockKind.TABLE), header_rows=1))
            continue

        lm = _LIST_ITEM.match(line)
        if lm:
            flush_para()
            item = [lm.group(1)]
            i += 1
            # continuation lines (indented, not a new item / blank)
            while i < len(lines) and lines[i].startswith(("  ", "\t")) and lines[i].strip() \
                    and not _LIST_ITEM.match(lines[i]):
                item.append(lines[i].strip())
                i += 1
            text = clean_text(_strip_inline_markdown(" ".join(item)))
            if text:
                blocks.append(Paragraph(text=text, locator=loc.next(BlockKind.PARAGRAPH), is_list=True))
            continue

        if not line.strip():
            flush_para()
        elif tag_line is not None and tag_line.match(clean_text(_strip_inline_markdown(line))):
            flush_para()
            para.append(line.strip())
            flush_para()
        else:
            para.append(line.strip())
        i += 1

    flush_para()
    if report is not None and not blocks:
        report.warn("The text file contains no readable paragraphs.")
    return blocks
