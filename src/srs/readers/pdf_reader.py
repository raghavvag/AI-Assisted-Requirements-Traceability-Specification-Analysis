"""
src/srs/readers/pdf_reader.py — PDF -> block stream (pdfplumber)
================================================================
PDF has no paragraphs, headings or tables, only positioned glyphs, so structure
is rebuilt from layout:

  * tables      `page.find_tables()`; their text is excluded from the line
                stream so it is not read twice. A table that continues on the
                next page (same column count, first thing on the page) is merged.
  * headings    a line whose font is >= 1.15x the median body size, or a short
                bold line that does not end like a sentence. Level = numbering
                depth ("3.2.1" -> 3), otherwise rank of the font size.
  * running     lines in the top/bottom 8% of the page that repeat (digits
    headers/    ignored) on at least half the pages are dropped, as are page
    footers     numbers ("12", "Page 3 of 9").
  * paragraphs  consecutive lines are joined; a new paragraph starts on a
                vertical gap, a bullet, a requirement tag, or a bold/regular
                switch. A line ending in "-" is joined without a space.
  * scanned     no text layer at all -> ScannedPdfError (OCR is out of scope).
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.srs.blocks import Block, BlockKind, Heading, LocatorFactory, Paragraph, Table
from src.srs.errors import InvalidDocumentError, ScannedPdfError
from src.srs.noise import clean_text
from src.srs.report import ExtractionReport

logger = logging.getLogger(__name__)

MARGIN_FRACTION = 0.08
HEADING_SIZE_RATIO = 1.15
PARAGRAPH_GAP_RATIO = 1.45

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_BULLET = re.compile(r"^\s*(?:[•▪◦●○■➢►·]|[-*](?=\s)|\d{1,2}[.)]\s)")


@dataclass
class _Line:
    text: str
    top: float
    bottom: float
    x0: float
    size: float
    bold: bool
    page: int
    in_margin: bool = False


def read_pdf(
    path: Union[str, Path],
    report: Optional[ExtractionReport] = None,
    *,
    tag_line: Optional["re.Pattern[str]"] = None,
) -> List[Block]:
    """Read a text-layer PDF into an ordered block stream."""
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException

    report = report if report is not None else ExtractionReport()
    path = Path(path)
    try:
        pdf = pdfplumber.open(str(path))
    except (PdfminerException, ValueError, OSError) as exc:
        raise InvalidDocumentError(f"Could not open '{path.name}' as a PDF: {exc}") from exc

    page_items: List[Tuple[int, List[Tuple[float, Any]]]] = []
    margin_lines: List[Tuple[int, _Line, bool]] = []      # (page, line, is_top)
    total_chars = 0
    with pdf:
        n_pages = len(pdf.pages)
        try:
            for pno, page in enumerate(pdf.pages, start=1):
                total_chars += len(page.chars)
                tables = page.find_tables()
                boxes = [t.bbox for t in tables]
                filtered = page.filter(lambda o, b=boxes: o.get("object_type") != "char" or not _inside(o, b))
                items: List[Tuple[float, Any]] = []
                for raw in filtered.extract_text_lines(strip=True, return_chars=True):
                    line = _to_line(raw, pno)
                    if line is None:
                        continue
                    h = float(page.height)
                    if line.bottom < h * MARGIN_FRACTION or line.top > h * (1 - MARGIN_FRACTION):
                        line.in_margin = True
                        margin_lines.append((pno, line, line.bottom < h * MARGIN_FRACTION))
                    items.append((line.top, line))
                for t in tables:
                    rows = t.extract()
                    if rows:
                        items.append((float(t.bbox[1]), [[c or "" for c in r] for r in rows]))
                items.sort(key=lambda it: it[0])
                page_items.append((pno, items))
        except PdfminerException as exc:
            raise InvalidDocumentError(f"PDF is damaged or encrypted: {exc}") from exc

    if total_chars == 0:
        raise ScannedPdfError(
            "This PDF has no text layer (it looks scanned). OCR is not supported; "
            "export the SRS from Word as .docx or as a text PDF.",
            report,
        )

    drop = _running_header_footer_keys(margin_lines, n_pages)
    body_sizes = [ln.size for _, items in page_items for _, ln in items
                  if isinstance(ln, _Line) and not ln.in_margin]
    median = statistics.median(body_sizes) if body_sizes else 11.0
    heading_sizes = sorted({round(s, 1) for s in body_sizes if s >= median * HEADING_SIZE_RATIO}, reverse=True)

    builder = _BlockBuilder(report, median, heading_sizes, tag_line)
    for pno, items in page_items:
        builder.start_page(pno)
        for _, item in items:
            if isinstance(item, _Line):
                if item.in_margin and _margin_key(item.text) in drop:
                    report.skip("running_header_footer")
                    continue
                if _PAGE_NUMBER.match(item.text):
                    report.skip("page_number")
                    continue
                builder.add_line(item)
            else:
                builder.add_table(item)
    return builder.finish()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inside(obj: Dict[str, Any], boxes: Sequence[Tuple[float, float, float, float]]) -> bool:
    cx = (float(obj["x0"]) + float(obj["x1"])) / 2
    cy = (float(obj["top"]) + float(obj["bottom"])) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)


def _to_line(raw: Dict[str, Any], page: int) -> Optional[_Line]:
    text = clean_text(raw.get("text", ""))
    chars = [c for c in raw.get("chars", []) if str(c.get("text", "")).strip()]
    if not text or not chars:
        return None
    sizes = Counter(round(float(c.get("size", 0)), 1) for c in chars)
    bold = sum(1 for c in chars if "bold" in str(c.get("fontname", "")).lower()) >= 0.9 * len(chars)
    return _Line(text=text, top=float(raw["top"]), bottom=float(raw["bottom"]), x0=float(raw["x0"]),
                 size=sizes.most_common(1)[0][0], bold=bold, page=page)


def _margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _running_header_footer_keys(margin_lines: Sequence[Tuple[int, _Line, bool]], n_pages: int) -> set:
    if n_pages < 2:
        return set()
    pages_per_key: Dict[str, set] = {}
    for pno, line, _ in margin_lines:
        pages_per_key.setdefault(_margin_key(line.text), set()).add(pno)
    return {k for k, pages in pages_per_key.items() if len(pages) >= max(2, n_pages * 0.5)}


class _BlockBuilder:
    def __init__(self, report: ExtractionReport, median: float, heading_sizes: List[float],
                 tag_line: Optional["re.Pattern[str]"]) -> None:
        self.report = report
        self.median = median
        self.heading_sizes = heading_sizes
        self.tag_line = tag_line
        self.loc = LocatorFactory()
        self.blocks: List[Block] = []
        self.para: List[_Line] = []
        self.page = 1
        self.nothing_on_page_yet = True

    def start_page(self, page: int) -> None:
        self._flush()
        self.page = page
        self.nothing_on_page_yet = True

    # -- lines ----------------------------------------------------------------

    def _is_heading(self, ln: _Line) -> Optional[int]:
        words = len(ln.text.split())
        numbered = _NUMBERED.match(ln.text)
        if ln.size >= self.median * HEADING_SIZE_RATIO and words <= 20:
            if numbered:
                return numbered.group(1).count(".") + 1
            rank = next((i for i, s in enumerate(self.heading_sizes) if round(ln.size, 1) >= s), len(self.heading_sizes))
            return rank + 1
        if (
            ln.bold and words <= 12
            and not ln.text.rstrip().endswith((".", ",", ";", ":"))
            and not re.match(r"^[A-Za-z][\w ]{0,24}:", ln.text)    # "Priority: High" labels
            and not (self.tag_line and self.tag_line.match(ln.text))
        ):
            if numbered:
                return numbered.group(1).count(".") + 1
            return len(self.heading_sizes) + 1
        return None

    def add_line(self, ln: _Line) -> None:
        level = self._is_heading(ln)
        if level is not None:
            self._flush()
            self._emit(Heading(level=level, text=ln.text, locator=self.loc.next(BlockKind.HEADING, ln.page)))
            return
        if self.para:
            prev = self.para[-1]
            gap = ln.top - prev.bottom
            line_h = max(1.0, prev.bottom - prev.top)
            new_para = (
                gap > line_h * PARAGRAPH_GAP_RATIO
                or prev.page != ln.page
                or bool(_BULLET.match(ln.text))
                or bool(self.tag_line and self.tag_line.match(ln.text))
                or prev.bold != ln.bold
                or (self.tag_line is not None and self.tag_line.match(prev.text) is not None
                    and len(self.para) == 1 and len(prev.text.split()) <= 12
                    and not prev.text.rstrip().endswith("."))
            )
            if new_para:
                self._flush()
        self.para.append(ln)

    def _flush(self) -> None:
        if not self.para:
            return
        text = ""
        for ln in self.para:
            if not text:
                text = ln.text
            elif text.endswith("-") and not text.endswith(" -"):
                text += ln.text                       # "server-" + "computed"
            else:
                text += " " + ln.text
        first = self.para[0]
        is_list = bool(_BULLET.match(first.text))
        self._emit(Paragraph(text=clean_text(text), locator=self.loc.next(BlockKind.PARAGRAPH, first.page),
                             is_list=is_list))
        self.para = []

    # -- tables ---------------------------------------------------------------

    def add_table(self, rows: List[List[str]]) -> None:
        self._flush()
        cleaned = tuple(tuple(_cell_text(c) for c in r) for r in rows)
        n_cols = max((len(r) for r in cleaned), default=0)
        prev = self.blocks[-1] if self.blocks else None
        # A table that is the first thing on a page and has the same columns as
        # the table that ended the previous page is its continuation.
        if self.nothing_on_page_yet and isinstance(prev, Table) and prev.n_cols == n_cols:
            body = cleaned[1:] if cleaned and prev.rows and cleaned[0] == prev.rows[0] else cleaned
            self.blocks[-1] = Table(rows=prev.rows + body, locator=prev.locator, header_rows=prev.header_rows)
            self.report.skip("table_continued_across_pages")
            self.nothing_on_page_yet = False
            return
        self._emit(Table(rows=cleaned, locator=self.loc.next(BlockKind.TABLE, self.page)))

    def _emit(self, block: Block) -> None:
        self.blocks.append(block)
        self.nothing_on_page_yet = False

    def finish(self) -> List[Block]:
        self._flush()
        return self.blocks


def _cell_text(cell: str) -> str:
    """Cell lines are visual wraps, not paragraphs: join them like body lines."""
    out = ""
    for line in (clean_text(raw) for raw in str(cell).splitlines()):
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and not out.endswith(" -"):
            out += line
        else:
            out += " " + line
    return out
