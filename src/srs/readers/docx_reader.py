"""
src/srs/readers/docx_reader.py — Word (.docx) -> block stream
============================================================
Walks the document body in document order with `Document.iter_inner_content()`
and emits Heading / Paragraph / Table blocks.

Industrial SRS edge cases handled here:
  * w:sdt content controls (template fields, rich-text controls) are unwrapped
    in a pre-pass so their content is not lost; the built-in "Table of
    Contents" control is removed instead.
  * Tracked changes: inserted text (w:ins) is kept, deleted text (w:del,
    w:moveFrom) is dropped. python-docx's `Paragraph.text` only reads
    `w:r | w:hyperlink`, so text is read from the XML directly.
  * Tables: horizontally merged cells keep their text in the first grid column
    and "" in spanned columns (columns stay aligned with the header);
    vertically merged cells repeat the top cell's text; multi-paragraph cells
    are joined with "\\n"; header rows marked "repeat as header" are counted;
    1-column tables (callout boxes, layout wrappers) are unwrapped into their
    content; nested tables are emitted after their parent.
  * Never read: images, shapes, text boxes, headers/footers, comments,
    footnotes. The body walk does not visit them, so most visual noise is
    removed for free.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Union

from docx import Document as open_docx
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.styles.style import BaseStyle
from docx.table import Table as DocxTable
from docx.table import _Cell
from docx.text.paragraph import Paragraph as DocxParagraph
from lxml import etree

from src.srs.blocks import Block, BlockKind, Heading, LocatorFactory, Paragraph, Row, Table
from src.srs.errors import InvalidDocumentError, UnsupportedFormatError
from src.srs.noise import clean_text
from src.srs.report import ExtractionReport

logger = logging.getLogger(__name__)

DEFAULT_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}

# Visible text of a paragraph in document order. Excludes deleted / moved-away
# revisions, text boxes (drawing noise) and the mc:Fallback duplicate of
# alternate content.
_TEXT_NODES = etree.XPath(
    "./descendant::w:t[not(ancestor::w:del) and not(ancestor::w:moveFrom)"
    " and not(ancestor::w:txbxContent) and not(ancestor::mc:Fallback)]"
    " | ./descendant::w:r[not(ancestor::w:del) and not(ancestor::w:txbxContent)]/w:tab"
    " | ./descendant::w:r[not(ancestor::w:del) and not(ancestor::w:txbxContent)]/w:br"
    " | ./descendant::w:r[not(ancestor::w:del) and not(ancestor::w:txbxContent)]/w:cr"
    " | ./descendant::w:noBreakHyphen",
    namespaces=_NS,
)
_HAS_GRAPHIC = etree.XPath("./descendant::w:drawing | ./descendant::w:pict", namespaces=_NS)
_OUTLINE_LVL = etree.XPath("./w:pPr/w:outlineLvl/@w:val", namespaces=_NS)
_NUM_PR = etree.XPath("./w:pPr/w:numPr", namespaces=_NS)
_TBL_HEADER = etree.XPath("./w:trPr/w:tblHeader", namespaces=_NS)
_RUNS_WITH_TEXT = etree.XPath("./descendant::w:r[w:t and not(ancestor::w:del)]", namespaces=_NS)
_RUN_BOLD = etree.XPath("./w:rPr/w:b", namespaces=_NS)
_DOC_PART_GALLERY = etree.XPath("./w:sdtPr/w:docPartObj/w:docPartGallery/@w:val", namespaces=_NS)

_W_T, _W_TAB, _W_BR, _W_CR, _W_NBH = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:noBreakHyphen")
_W_VAL = qn("w:val")

_HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def read_docx(
    path: Union[str, Path],
    report: Optional[ExtractionReport] = None,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> List[Block]:
    """Read a .docx file into an ordered block stream."""
    report = report if report is not None else ExtractionReport()
    path = Path(path)
    guard_docx_archive(path, max_uncompressed_bytes)

    try:
        document = open_docx(str(path))
    except (PackageNotFoundError, KeyError, ValueError, etree.XMLSyntaxError) as exc:
        raise InvalidDocumentError(f"Could not open '{path.name}' as a Word document: {exc}") from exc

    body = document.element.body
    report.skip("toc", _remove_toc_controls(body))
    _unwrap_content_controls(body)

    walker = _DocxWalker(report)
    blocks = list(walker.walk_document(document))
    logger.debug("docx %s -> %d blocks", path.name, len(blocks))
    return blocks


def guard_docx_archive(path: Union[str, Path], max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES) -> None:
    """Reject files that are not a sane .docx package before python-docx opens them.

    * not a zip (also how password-protected .docx files look: they are OLE)
    * missing word/document.xml
    * macro-enabled (vbaProject.bin)
    * decompresses beyond the limit (zip bomb)
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise InvalidDocumentError(
            "Not a valid .docx file (it is not a zip package). Password-protected or legacy "
            ".doc files must be re-saved as an unprotected .docx."
        ) from exc
    names = {i.filename for i in infos}
    if "word/document.xml" not in names:
        raise InvalidDocumentError("Not a Word document: word/document.xml is missing.")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise UnsupportedFormatError("Macro-enabled documents are not accepted. Save the SRS as a plain .docx.")
    total = sum(i.file_size for i in infos)
    if total > max_uncompressed_bytes:
        raise InvalidDocumentError(
            f"Document expands to {total / 1e6:.0f} MB when decompressed "
            f"(limit {max_uncompressed_bytes / 1e6:.0f} MB)."
        )


# ---------------------------------------------------------------------------
# XML pre-passes
# ---------------------------------------------------------------------------

def _remove_toc_controls(body: etree._Element) -> int:
    """Delete Word's auto-generated TOC content control. Returns paragraphs removed."""
    removed = 0
    for sdt in list(body.iter(qn("w:sdt"))):
        gallery = " ".join(_DOC_PART_GALLERY(sdt)).lower()
        if "table of contents" in gallery and sdt.getparent() is not None:
            removed += max(1, len(list(sdt.iter(qn("w:p")))))
            sdt.getparent().remove(sdt)
    return removed


def _unwrap_content_controls(body: etree._Element) -> None:
    """Replace every w:sdt with the children of its w:sdtContent, in place.

    Works for block-level (body / cell), row-level, cell-level and inline (run)
    controls alike, because the content children are valid wherever the sdt was.
    Processed last-to-first so nested controls are handled before their parents
    move them.
    """
    for sdt in reversed(list(body.iter(qn("w:sdt")))):
        parent = sdt.getparent()
        if parent is None:
            continue
        content = sdt.find(qn("w:sdtContent"))
        idx = parent.index(sdt)
        for offset, child in enumerate(list(content) if content is not None else []):
            parent.insert(idx + offset, child)
        parent.remove(sdt)


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------

class _DocxWalker:
    def __init__(self, report: ExtractionReport) -> None:
        self.report = report
        self.locators = LocatorFactory()

    # -- containers ---------------------------------------------------------

    def walk_document(self, document: DocxDocument) -> Iterator[Block]:
        yield from self._walk(document.iter_inner_content())

    def _walk(self, items: Iterable[Union[DocxParagraph, DocxTable]]) -> Iterator[Block]:
        for item in items:
            if isinstance(item, DocxParagraph):
                block = self._paragraph(item)
                if block is not None:
                    yield block
            else:
                yield from self._table(item)

    # -- paragraphs ---------------------------------------------------------

    def _paragraph(self, par: DocxParagraph) -> Optional[Union[Heading, Paragraph]]:
        raw = paragraph_text(par._p)
        text = clean_text(raw)
        if not text:
            self.report.skip("image" if _HAS_GRAPHIC(par._p) else "empty")
            return None

        style_name = _style_name(par)
        level = _heading_level(par, text)
        if level is not None:
            return Heading(level=level, text=text, locator=self.locators.next(BlockKind.HEADING))
        return Paragraph(
            text=text,
            locator=self.locators.next(BlockKind.PARAGRAPH),
            style=style_name,
            is_list=_is_list(par, style_name),
        )

    # -- tables -------------------------------------------------------------

    def _table(self, tbl: DocxTable, nested: bool = False) -> Iterator[Block]:
        rows: List[Row] = []
        header_rows = 0
        counting_header = True
        nested_tables: List[DocxTable] = []

        for row in tbl.rows:
            try:
                cells = row.cells
                before, after = row.grid_cols_before, row.grid_cols_after
            except (IndexError, ValueError):  # malformed grid; read what is there
                cells, before, after = tuple(_Cell(tc, tbl) for tc in row._tr.tc_lst), 0, 0
            values: List[str] = [""] * before
            prev_tc = None
            for cell in cells:
                if cell._tc is prev_tc:          # same tc repeated = horizontal span
                    values.append("")
                    continue
                prev_tc = cell._tc
                values.append(self._cell_text(cell, nested_tables))
            values.extend([""] * after)
            rows.append(tuple(values))

            if counting_header and _TBL_HEADER(row._tr):
                header_rows += 1
            else:
                counting_header = False

        n_cols = max((len(r) for r in rows), default=0)
        if n_cols <= 1:
            # Callout box / layout wrapper: its content is ordinary document flow.
            self.report.skip("layout_table_unwrapped")
            for row in tbl.rows:
                for cell in _unique_cells(row):
                    yield from self._walk(cell.iter_inner_content())
            return

        if any(cell.strip() for r in rows for cell in r):
            yield Table(
                rows=tuple(rows),
                locator=self.locators.next(BlockKind.TABLE),
                header_rows=header_rows,
                nested=nested,
            )
        for inner in nested_tables:
            yield from self._table(inner, nested=True)

    def _cell_text(self, cell: _Cell, nested_out: List[DocxTable]) -> str:
        """Multi-paragraph cell -> lines joined by "\\n". Nested tables are
        flattened into the text (" | " between cells) and also queued so they
        are emitted as their own Table blocks."""
        lines: List[str] = []
        for item in cell.iter_inner_content():
            if isinstance(item, DocxParagraph):
                t = clean_text(paragraph_text(item._p))
                if t:
                    lines.append(t)
            else:
                nested_out.append(item)
                for row in item.rows:
                    vals = [clean_text(paragraph_text(p._p)) for c in _unique_cells(row) for p in c.paragraphs]
                    line = " | ".join(v for v in vals if v)
                    if line:
                        lines.append(line)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers (module-level so tests can use them directly)
# ---------------------------------------------------------------------------

def paragraph_text(p_element: etree._Element) -> str:
    """Visible text of a w:p element including tracked insertions and inline
    content-control text, excluding deletions."""
    parts: List[str] = []
    for node in _TEXT_NODES(p_element):
        tag = node.tag
        if tag == _W_T:
            parts.append(node.text or "")
        elif tag == _W_TAB:
            parts.append("\t")
        elif tag in (_W_BR, _W_CR):
            parts.append("\n")
        elif tag == _W_NBH:
            parts.append("-")
    return "".join(parts)


def _unique_cells(row) -> List[_Cell]:
    out: List[_Cell] = []
    seen = set()
    try:
        cells = row.cells
    except (IndexError, ValueError):
        return out
    for c in cells:
        if id(c._tc) not in seen:
            seen.add(id(c._tc))
            out.append(c)
    return out


def _style_chain(par: DocxParagraph) -> List[BaseStyle]:
    chain: List[BaseStyle] = []
    try:
        style = par.style
    except (KeyError, ValueError):
        return chain
    while style is not None and len(chain) < 6:
        chain.append(style)
        style = style.base_style
    return chain


def _style_name(par: DocxParagraph) -> str:
    chain = _style_chain(par)
    return (chain[0].name or "") if chain else ""


def _heading_level(par: DocxParagraph, text: str) -> Optional[int]:
    """Heading level from (1) direct w:outlineLvl, (2) the style chain
    (Heading N / Title / style outline level), (3) a numbered, bold, short line."""
    direct = _OUTLINE_LVL(par._p)
    if direct:
        lvl = _safe_int(direct[0])
        if lvl is not None and lvl < 9:
            return lvl + 1

    for style in _style_chain(par):
        name = (style.name or "").strip()
        m = _HEADING_STYLE.match(name)
        if m:
            return int(m.group(1))
        if name.lower() in {"title"}:
            return 0
        if name.lower() in {"subtitle"}:
            return None
        style_lvl = _OUTLINE_LVL(style.element)
        if style_lvl:
            lvl = _safe_int(style_lvl[0])
            if lvl is not None and lvl < 9:
                return lvl + 1

    # Custom corporate templates often use a bold "Normal" paragraph like
    # "3.2.1 Payment Processing" as a heading.
    m = _NUMBERED_HEADING.match(text)
    if (
        m
        and len(text.split()) <= 12
        and not text.rstrip().endswith((".", ":", ";", ","))
        and not _NUM_PR(par._p)
        and _is_bold(par)
    ):
        return m.group(1).count(".") + 1
    return None


def _is_bold(par: DocxParagraph) -> bool:
    runs = _RUNS_WITH_TEXT(par._p)
    if not runs:
        return False
    style_bold = any(s.font.bold for s in _style_chain(par) if hasattr(s, "font") and s.font is not None)
    for r in runs:
        b = _RUN_BOLD(r)
        if b:
            if b[0].get(_W_VAL, "true").lower() in {"0", "false", "off"}:
                return False
        elif not style_bold:
            return False
    return True


def _is_list(par: DocxParagraph, style_name: str) -> bool:
    return bool(_NUM_PR(par._p)) or "list" in style_name.lower()


def _safe_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
