"""
src/srs/blocks.py — Intermediate Representation (IR) for SRS documents
======================================================================
Every reader (.docx, .pdf, .md/.txt) turns its input into the same flat,
ordered stream of blocks. Extraction strategies only ever see this stream, so
they are written and tested once, independent of the file format.

    Heading(level, text)              a section title; level 0 = document title
    Paragraph(text, style, is_list)   a run of body text / one list item
    Table(rows)                       a grid of cell strings (merged cells resolved)
    Figure(image, alt_text, caption)  an embedded picture (UML diagram, flowchart,
                                      architecture drawing); src/srs/diagrams.py
                                      reads requirements out of it

Each block carries a Locator so every extracted requirement can be traced back
to "table 2 / row 5" or "page 4 / paragraph 31" in the source document.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterator, Optional, Tuple, Union


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"


@dataclass(frozen=True)
class Locator:
    """Where a block (or a row of a table block) came from in the source file.

    `ordinal` is 1-based and counts blocks of the same kind in document order,
    which is what a reader would count when looking at the document ("the 3rd
    table"). `page` is only known for paginated formats (PDF).
    """

    kind: BlockKind
    ordinal: int
    page: Optional[int] = None
    row: Optional[int] = None

    def with_row(self, row: int) -> "Locator":
        return replace(self, row=row)

    def __str__(self) -> str:
        label = {
            BlockKind.HEADING: "heading",
            BlockKind.PARAGRAPH: "paragraph",
            BlockKind.TABLE: "table",
            BlockKind.FIGURE: "figure",
        }[self.kind]
        parts = []
        if self.page is not None:
            parts.append(f"page {self.page}")
        parts.append(f"{label} {self.ordinal}")
        if self.row is not None:
            parts.append(f"row {self.row}")
        return " / ".join(parts)


@dataclass(frozen=True)
class Heading:
    """A section heading. Lower `level` = higher in the outline (1 = chapter)."""

    level: int
    text: str
    locator: Locator

    kind: BlockKind = field(default=BlockKind.HEADING, init=False, repr=False)


@dataclass(frozen=True)
class Paragraph:
    """Body text. `style` is the source style name (e.g. "Normal", "TOC 1",
    "Caption", "List Bullet") and is used by the noise filter."""

    text: str
    locator: Locator
    style: str = ""
    is_list: bool = False

    kind: BlockKind = field(default=BlockKind.PARAGRAPH, init=False, repr=False)


Row = Tuple[str, ...]


@dataclass(frozen=True)
class Table:
    """A table as a rectangular-ish grid of cell strings.

    Readers resolve merged cells before building a Table: a horizontally merged
    cell keeps its text in its first grid column and "" in the columns it spans,
    so column positions always line up with the header row. Multi-paragraph
    cells are joined with "\\n".

    `header_rows` = number of leading rows the source marked as header rows
    (Word's "Repeat as header row" / w:tblHeader). 0 when unknown.
    """

    rows: Tuple[Row, ...]
    locator: Locator
    header_rows: int = 0
    nested: bool = False

    kind: BlockKind = field(default=BlockKind.TABLE, init=False, repr=False)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def iter_rows(self) -> Iterator[Tuple[int, Row]]:
        """Yield (1-based row number, row)."""
        for i, row in enumerate(self.rows, start=1):
            yield i, row


@dataclass(frozen=True)
class DiagramStatement:
    """One behaviour a figure specifies, as a requirement sentence. `req_id` is
    the requirement ID printed in the figure (or its caption), if any."""

    text: str
    req_id: str = ""
    title: str = ""


@dataclass(frozen=True)
class DiagramReading:
    """What a diagram interpreter read from a figure (src/srs/diagrams.py).

    `engine` names the source, e.g. "vision:gpt-4o-mini", "ocr:tesseract",
    "alt_text". `summary` is context only and is never embedded."""

    engine: str
    diagram_type: str = "other"
    summary: str = ""
    statements: Tuple[DiagramStatement, ...] = ()


@dataclass(frozen=True)
class Figure:
    """An embedded image. Readers fill `image` (raw bytes as stored in the
    document) and `alt_text`; the extractor pairs the `caption` and an
    interpreter fills `reading`."""

    locator: Locator
    image: bytes = field(repr=False)
    content_type: str = ""
    alt_text: str = ""
    caption: str = ""
    reading: Optional[DiagramReading] = None

    kind: BlockKind = field(default=BlockKind.FIGURE, init=False, repr=False)


Block = Union[Heading, Paragraph, Table, Figure]


class LocatorFactory:
    """Hands out sequential ordinals per block kind while a reader walks a file."""

    def __init__(self) -> None:
        self._counts = {kind: 0 for kind in BlockKind}

    def next(self, kind: BlockKind, page: Optional[int] = None) -> Locator:
        self._counts[kind] += 1
        return Locator(kind=kind, ordinal=self._counts[kind], page=page)
