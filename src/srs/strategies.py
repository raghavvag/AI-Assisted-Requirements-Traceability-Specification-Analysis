"""
src/srs/strategies.py — Requirement extraction strategies
=========================================================
Strategies read the cleaned block stream and emit Candidates (raw requirement
records). They run in priority order; `normalise.finalize()` merges them by
canonical ID.

  1. TableStrategy          requirement tables, in three layouts:
                              * header row   | ID | Title | Description | Priority |
                              * key/value    | ID | FR-01 |  | Description | ... |
                              * headerless   | FR-01 | The system shall ... |
  2. TaggedStrategy         paragraphs / headings that START with an ID tag
                            ("FR-1: ...", "[REQ-4] ...", "3.2.1 FR-2 Login").
                            The body runs to the next tag or a heading of the
                            same or higher level. Inline cross-references
                            ("see REQ-02") never start a requirement.
  3. ShallStatementStrategy fallback for SRS documents without IDs: every
                            shall/must sentence inside a requirements section.
  4. DiagramStrategy        (src/srs/diagrams.py) requirements read out of
                            figures: UML, flowcharts, architecture diagrams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.srs.blocks import Block, Heading, Paragraph, Row, Table
from src.srs.noise import clean_text
from src.srs.report import ExtractionReport

STRATEGY_TABLE = "table"
STRATEGY_TAGGED = "tagged"
STRATEGY_SHALL = "shall_statement"
STRATEGY_DIAGRAM = "diagram"
STRATEGY_PRIORITY = {STRATEGY_TABLE: 0, STRATEGY_TAGGED: 1, STRATEGY_SHALL: 2, STRATEGY_DIAGRAM: 3}

# Metadata keys a strategy may fill.
META_PRIORITY = "priority"
META_TYPE = "type"
META_ACCEPTANCE = "acceptance_criteria"
META_RATIONALE = "rationale"
META_SOURCE = "origin"
META_ID_SOURCE = "id_source"


# ---------------------------------------------------------------------------
# Data passed between strategies and the normaliser
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """A requirement as found in the document, before normalisation."""

    raw_id: str
    strategy: str
    position: Tuple[int, int]            # (block index, row / sentence index) for document order
    locator: str
    title: str = ""
    description: str = ""
    section: Tuple[str, ...] = ()        # heading path, outermost first
    metadata: Dict[str, str] = field(default_factory=dict)


class SectionContext:
    """Heading path in effect at every block index."""

    def __init__(self, blocks: Sequence[Block]) -> None:
        self._paths: List[Tuple[Tuple[int, str], ...]] = []
        stack: List[Tuple[int, str]] = []
        for block in blocks:
            if isinstance(block, Heading):
                while stack and stack[-1][0] >= block.level:
                    stack.pop()
                stack.append((block.level, block.text))
            self._paths.append(tuple(stack))

    def path(self, index: int) -> Tuple[str, ...]:
        """Heading texts, outermost first. The document title (level 0) is left
        out: it names the document ("Software Requirements Specification"), not
        a section."""
        return tuple(t for lvl, t in self.levelled_path(index) if lvl > 0)

    def levelled_path(self, index: int) -> Tuple[Tuple[int, str], ...]:
        return self._paths[index] if 0 <= index < len(self._paths) else ()


# ---------------------------------------------------------------------------
# ID tag matching
# ---------------------------------------------------------------------------

_NUMBERING = r"(?:\d+(?:\.\d+)*\.?\s+)?"
_DASHES = "–—"


class TagMatcher:
    """Compiled regexes for one ID pattern.

    paragraph  "FR-1: text"  "FR1 - text"  "REQ-04. text"  "FR-3) text"  "FR-2<TAB>text"
               "FR-2 The system shall ..." (space only when a capital letter follows, so
               a sentence like "REQ-02 requires ..." is not mistaken for a tag)
               "[REQ-4] text"  "(NFR-2) text"  "FR-7" (tag alone)
    heading    additionally "FR-1 User Login" and "3.2.1 FR-1 User Login"
    """

    def __init__(self, id_pattern: str) -> None:
        idp = f"(?P<id>{id_pattern})"
        end = r"(?![A-Za-z0-9])"
        self.id_only = re.compile(rf"\s*[\[(]?\s*(?:{id_pattern})\s*[\])]?\s*[:.]?\s*")
        self._bracketed = re.compile(
            rf"^\s*{_NUMBERING}[\[(]\s*{idp}\s*[\])]\s*[:{_DASHES}\-.]?\s*(?P<rest>.*)$", re.S
        )
        self._paragraph = re.compile(
            rf"^\s*{_NUMBERING}{idp}{end}"
            rf"(?:\s*[:{_DASHES}]\s*|\s+-\s+|\s*\.\s+|\s*\)\s*|\t+\s*|\s+(?=[A-Z])|\s*$)(?P<rest>.*)$",
            re.S,
        )
        self._heading = re.compile(rf"^\s*{_NUMBERING}{idp}{end}[\s:.{_DASHES}\-]*(?P<rest>.*)$", re.S)
        # Start-of-line test used by line-based readers (Markdown, PDF) to split
        # paragraphs before a new requirement tag.
        self.line_start = re.compile(
            rf"^\s*{_NUMBERING}[\[(]?\s*(?:{id_pattern}){end}\s*[\])]?"
            rf"(?:\s*[:{_DASHES}]|\s+-\s|\s*\.\s|\s*\)|\t|\s+(?=[A-Z])|\s*$)"
        )

    def match_paragraph(self, text: str) -> Optional[Tuple[str, str]]:
        m = self._bracketed.match(text) or self._paragraph.match(text)
        return (m.group("id"), m.group("rest").strip()) if m else None

    def match_heading(self, text: str) -> Optional[Tuple[str, str]]:
        m = self._bracketed.match(text) or self._heading.match(text)
        return (m.group("id"), m.group("rest").strip()) if m else None

    def is_id(self, text: str) -> bool:
        return bool(self.id_only.fullmatch(text))


# ---------------------------------------------------------------------------
# 1. Tables
# ---------------------------------------------------------------------------

def _norm_header(cell: str) -> str:
    t = clean_text(cell).lower()
    if t.strip() == "#":
        return "#"
    t = re.sub(r"\(.*?\)", " ", t)                 # "Priority (H/M/L)" -> "priority"
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Ordered: earlier aliases win when several columns match the same field.
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "id": (
        "req id", "requirement id", "id", "req no", "req number", "requirement no",
        "requirement number", "requirement ref", "req ref", "ref id", "ref", "reference",
        "identifier", "req", "no", "#", "key", "fr id", "nfr id", "srs id", "item id",
        "story id", "us id", "use case id", "uc id", "sl no", "s no", "sr no",
    ),
    "text": (
        "description", "requirement description", "requirement text", "requirement statement",
        "statement", "requirement", "requirements", "functional requirement", "text", "details",
        "detail", "specification", "desc", "user story", "story", "shall statement",
    ),
    "title": (
        "title", "requirement title", "name", "requirement name", "short name", "short description",
        "summary", "feature", "feature name", "function", "function name", "capability", "subject",
    ),
    META_PRIORITY: ("priority", "moscow", "importance", "criticality", "rank"),
    META_TYPE: ("type", "requirement type", "category", "kind", "class", "classification"),
    META_ACCEPTANCE: (
        "acceptance criteria", "acceptance criterion", "acceptance", "fit criterion", "fit criteria",
        "verification criteria", "verification", "acceptance test", "test criteria", "validation criteria",
    ),
    META_RATIONALE: ("rationale", "justification", "reason", "purpose"),
    META_SOURCE: ("source", "origin", "stakeholder", "originator", "raised by"),
}
_PREFIX_MATCH_FIELDS = {"text", "title", META_ACCEPTANCE}


def _field_for(header: str) -> Optional[Tuple[str, int]]:
    """(field, alias rank) for a normalised header cell, or None."""
    if not header:
        return None
    for fname, aliases in _ALIASES.items():
        for rank, alias in enumerate(aliases):
            if header == alias:
                return fname, rank
    for fname, aliases in _ALIASES.items():
        if fname in _PREFIX_MATCH_FIELDS:
            for rank, alias in enumerate(aliases):
                if header.startswith(alias + " "):
                    return fname, rank + 100
    if header.endswith(" id") and len(header.split()) <= 3:
        return "id", 200
    return None


@dataclass
class ColumnMap:
    header_row: int                      # 0-based index into table.rows
    columns: Dict[str, int]              # field -> column index
    extra_text: List[int] = field(default_factory=list)
    header_values: Row = ()


def map_header(row: Row, row_index: int) -> Optional[ColumnMap]:
    best: Dict[str, Tuple[int, int]] = {}            # field -> (rank, col)
    text_cols: List[Tuple[int, int]] = []
    for col, cell in enumerate(row):
        hit = _field_for(_norm_header(cell))
        if not hit:
            continue
        fname, rank = hit
        if fname == "text":
            text_cols.append((rank, col))
        if fname not in best or rank < best[fname][0]:
            best[fname] = (rank, col)
    if "id" not in best or "text" not in best:
        return None
    columns = {f: c for f, (_, c) in best.items()}
    extra = [c for _, c in sorted(text_cols) if c != columns["text"]]
    if extra and "title" not in columns:
        columns["title"] = extra.pop(0)
    if columns["id"] == columns["text"]:
        return None
    return ColumnMap(header_row=row_index, columns=columns, extra_text=extra, header_values=row)


def _cell(row: Row, col: Optional[int]) -> str:
    if col is None or col >= len(row):
        return ""
    return row[col].strip()


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _looks_like_id(cell: str) -> bool:
    c = cell.strip().strip("[]()").strip()
    return bool(c) and len(c) <= 40 and len(c.split()) <= 4 and any(ch.isdigit() for ch in c)


class TableStrategy:
    name = STRATEGY_TABLE

    def __init__(self, tags: TagMatcher) -> None:
        self.tags = tags

    # -- detection ------------------------------------------------------------

    def header_map(self, table: Table) -> Optional[ColumnMap]:
        """Header row among the first 3 rows (a merged title row may sit above it)."""
        for i, row in enumerate(table.rows[:3]):
            cmap = map_header(row, i)
            if cmap:
                return cmap
        return None

    def is_key_value(self, table: Table) -> bool:
        keys = {(_field_for(_norm_header(r[0])) or ("", 0))[0] for r in table.rows if r}
        return table.n_cols >= 2 and "id" in keys and ("text" in keys or "title" in keys)

    def headerless_id_col0(self, table: Table) -> bool:
        rows = [r for r in table.rows if any(c.strip() for c in r)]
        if table.n_cols < 2 or len(rows) < 1:
            return False
        hits = sum(1 for r in rows if self.tags.is_id(_first_line(r[0])))
        return hits >= max(1, round(0.6 * len(rows)))

    def is_requirement_table(self, table: Table) -> bool:
        return bool(self.header_map(table)) or self.is_key_value(table) or self.headerless_id_col0(table)

    # -- extraction -----------------------------------------------------------

    def extract(
        self,
        blocks: Sequence[Block],
        ctx: SectionContext,
        report: ExtractionReport,
    ) -> Tuple[List[Candidate], Set[int]]:
        """Returns (candidates, indices of tables consumed as requirement tables)."""
        out: List[Candidate] = []
        consumed: Set[int] = set()
        for idx, block in enumerate(blocks):
            if not isinstance(block, Table):
                continue
            cmap = self.header_map(block)
            if cmap:
                found = self._from_header_table(block, idx, cmap, ctx, report)
            elif self.is_key_value(block):
                found = self._from_key_value_table(block, idx, ctx, report)
            elif self.headerless_id_col0(block):
                found = self._from_header_table(block, idx, self._headerless_map(block), ctx, report)
            else:
                continue
            if found:
                consumed.add(idx)
                out.extend(found)
        report.found(self.name, len(out))
        return out, consumed

    def _headerless_map(self, table: Table) -> ColumnMap:
        def avg_words(col: int) -> float:
            vals = [len(_cell(r, col).split()) for r in table.rows]
            return sum(vals) / max(1, len(vals))

        text_col = max(range(1, table.n_cols), key=avg_words)
        columns = {"id": 0, "text": text_col}
        if text_col != 1 and avg_words(1) <= 12:
            columns["title"] = 1
        return ColumnMap(header_row=-1, columns=columns)

    def _split_id_cell(self, cell: str) -> Tuple[str, str]:
        """ID cell may hold "FR-1 User Login": returns (id, inline title)."""
        first = _first_line(cell)
        if self.tags.is_id(first):
            return first.strip().strip("[]()").rstrip(":.").strip(), ""
        m = self.tags.match_heading(first)
        if m:
            return m[0], m[1]
        return first, ""

    def _from_header_table(
        self, table: Table, idx: int, cmap: ColumnMap, ctx: SectionContext, report: ExtractionReport
    ) -> List[Candidate]:
        cols = cmap.columns
        header_norm = tuple(_norm_header(c) for c in cmap.header_values) if cmap.header_values else ()
        found: List[Candidate] = []
        prev: Optional[Candidate] = None

        for r_no, row in table.iter_rows():
            if r_no - 1 <= cmap.header_row:
                continue
            if header_norm and tuple(_norm_header(c) for c in row) == header_norm:
                report.skip("repeated_header_row")
                continue

            raw_id, inline_title = self._split_id_cell(_cell(row, cols["id"]))
            text = _cell(row, cols["text"])
            extra = [t for t in (_cell(row, c) for c in cmap.extra_text) if t]

            if not raw_id and not text:
                report.skip("empty_table_row")
                continue
            # Continuation row: ID cell empty, or repeated by a vertical merge.
            if prev is not None and (not raw_id or raw_id == prev.raw_id):
                if text and text not in prev.description:
                    prev.description = f"{prev.description}\n{text}".strip()
                self._fill_metadata(prev, row, cols)
                continue
            title = _cell(row, cols.get("title")) or inline_title
            if not text and not extra and _cell(row, cols.get("title")):
                # Title and Description cells merged (or the description left
                # empty): the only text in the row is the statement itself.
                text, title = _cell(row, cols.get("title")), inline_title
            if not text and not extra:
                report.skip("table_row_without_text")
                continue
            if not _looks_like_id(raw_id):
                report.skip("table_row_invalid_id")
                continue

            cand = Candidate(
                raw_id=raw_id,
                strategy=self.name,
                position=(idx, r_no),
                locator=str(table.locator.with_row(r_no)),
                title=title,
                description="\n".join([text] + extra).strip(),
                section=ctx.path(idx),
            )
            self._fill_metadata(cand, row, cols)
            found.append(cand)
            prev = cand
        return found

    @staticmethod
    def _fill_metadata(cand: Candidate, row: Row, cols: Dict[str, int]) -> None:
        for key in (META_PRIORITY, META_TYPE, META_ACCEPTANCE, META_RATIONALE, META_SOURCE):
            value = _cell(row, cols.get(key))
            if value and not cand.metadata.get(key):
                cand.metadata[key] = value

    def _from_key_value_table(
        self, table: Table, idx: int, ctx: SectionContext, report: ExtractionReport
    ) -> List[Candidate]:
        found: List[Candidate] = []
        cur: Optional[Candidate] = None
        for r_no, row in table.iter_rows():
            if not row:
                continue
            hit = _field_for(_norm_header(row[0]))
            value = "\n".join(c for c in row[1:] if c.strip()).strip()
            if not hit:
                if cur is not None and value:
                    report.skip("kv_row_ignored")
                continue
            fname = hit[0]
            if fname == "id":
                raw_id, inline_title = self._split_id_cell(value)
                if not _looks_like_id(raw_id):
                    report.skip("table_row_invalid_id")
                    cur = None
                    continue
                cur = Candidate(
                    raw_id=raw_id, strategy=self.name, position=(idx, r_no),
                    locator=str(table.locator.with_row(r_no)), title=inline_title,
                    section=ctx.path(idx),
                )
                found.append(cur)
            elif cur is None:
                continue
            elif fname == "text":
                cur.description = f"{cur.description}\n{value}".strip()
            elif fname == "title":
                cur.title = cur.title or value
            elif value:
                cur.metadata.setdefault(fname, value)
        kept = [c for c in found if c.description or c.title]
        report.skip("table_row_without_text", len(found) - len(kept))
        return kept


# ---------------------------------------------------------------------------
# 2. Tagged paragraphs / headings
# ---------------------------------------------------------------------------

_LABEL = re.compile(
    r"^(?P<label>description|requirement|statement|title|name|priority|type|category|rationale|"
    r"justification|source|origin|acceptance criteria|acceptance criterion|fit criterion|"
    r"dependencies|dependency|status|risk|owner|version|stability|verification method)"
    r"\s*[:\-–]\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_LABEL_FIELD = {
    "description": "text", "requirement": "text", "statement": "text",
    "title": "title", "name": "title",
    "priority": META_PRIORITY, "type": META_TYPE, "category": META_TYPE,
    "rationale": META_RATIONALE, "justification": META_RATIONALE,
    "source": META_SOURCE, "origin": META_SOURCE,
    "acceptance criteria": META_ACCEPTANCE, "acceptance criterion": META_ACCEPTANCE,
    "fit criterion": META_ACCEPTANCE,
}
_SUBHEADING_FIELD = re.compile(r"^(acceptance criteria|acceptance criterion|fit criteri(on|a)|"
                               r"description|rationale)\b", re.IGNORECASE)


class _OpenRequirement:
    def __init__(self, raw_id: str, rest: str, level: Optional[int], idx: int,
                 locator: str, section: Tuple[str, ...], is_heading: bool) -> None:
        self.raw_id, self.level, self.idx = raw_id, level, idx
        self.locator, self.section, self.is_heading = locator, section, is_heading
        self.title = ""
        self.lines: List[str] = []
        self.acceptance: List[str] = []
        self.metadata: Dict[str, str] = {}
        self.mode = "text"                    # where un-labelled lines go
        if is_heading or (rest and len(rest.split()) <= 12 and not rest.rstrip().endswith((".", ";"))):
            self.title = rest
        elif rest:
            self.add_line(rest)

    def add_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        m = _LABEL.match(line)
        if m:
            target = _LABEL_FIELD.get(m.group("label").lower())
            value = m.group("value").strip()
            if target is None:                 # dependencies / status / owner: not semantic content
                self.mode = "ignore"
                return
            if target == "text":
                self.mode = "text"
                if value:
                    self.lines.append(value)
            elif target == "title":
                self.title = self.title or value
            elif target == META_ACCEPTANCE:
                self.mode = META_ACCEPTANCE
                if value:
                    self.acceptance.append(value)
            elif target in (META_PRIORITY, META_TYPE, META_SOURCE):
                # single-line attributes: the description may continue afterwards
                if value:
                    self.metadata.setdefault(target, value)
            else:                              # rationale: kept as metadata, not embedded
                self.mode = "ignore"
                if value:
                    self.metadata.setdefault(target, value)
            return
        if self.mode == "text":
            self.lines.append(line)
        elif self.mode == META_ACCEPTANCE:
            self.acceptance.append(line)

    def sub_heading(self, text: str) -> None:
        m = _SUBHEADING_FIELD.match(text)
        if not m:
            self.mode = "text"
            return
        key = m.group(1).lower()
        self.mode = META_ACCEPTANCE if key.startswith(("acceptance", "fit")) else (
            "ignore" if key == "rationale" else "text")

    def close(self, strategy: str) -> Candidate:
        md = dict(self.metadata)
        if self.acceptance:
            md[META_ACCEPTANCE] = " ".join(self.acceptance)
        return Candidate(
            raw_id=self.raw_id, strategy=strategy, position=(self.idx, 0), locator=self.locator,
            title=self.title, description=" ".join(self.lines), section=self.section, metadata=md,
        )


class TaggedStrategy:
    name = STRATEGY_TAGGED

    def __init__(self, tags: TagMatcher, table_strategy: Optional[TableStrategy] = None) -> None:
        self.tags = tags
        self.tables = table_strategy or TableStrategy(tags)

    def extract(
        self,
        blocks: Sequence[Block],
        ctx: SectionContext,
        report: ExtractionReport,
        consumed_tables: Set[int] = frozenset(),
    ) -> List[Candidate]:
        out: List[Candidate] = []
        cur: Optional[_OpenRequirement] = None

        def close() -> None:
            nonlocal cur
            if cur is not None:
                out.append(cur.close(self.name))
                cur = None

        for idx, block in enumerate(blocks):
            if isinstance(block, Heading):
                m = self.tags.match_heading(block.text)
                if m:
                    close()
                    cur = _OpenRequirement(m[0], m[1], block.level, idx, str(block.locator),
                                           tuple(t for lvl, t in ctx.levelled_path(idx)[:-1] if lvl > 0),
                                           is_heading=True)
                elif cur is not None and (cur.level is None or block.level <= cur.level):
                    close()
                elif cur is not None:
                    cur.sub_heading(block.text)
            elif isinstance(block, Paragraph):
                m = self.tags.match_paragraph(block.text)
                if m:
                    close()
                    cur = _OpenRequirement(m[0], m[1], None, idx, str(block.locator),
                                           ctx.path(idx), is_heading=False)
                elif cur is not None:
                    cur.add_line(block.text)
            elif isinstance(block, Table):
                if idx in consumed_tables:
                    close()
                elif cur is not None:
                    for _, row in block.iter_rows():
                        cells = [c.replace("\n", " ").strip() for c in row if c.strip()]
                        if len(cells) == 2:
                            cur.add_line(f"{cells[0]}: {cells[1]}")
                        elif cells:
                            cur.add_line(" | ".join(cells))
        close()
        report.found(self.name, len(out))
        return out


# ---------------------------------------------------------------------------
# 3. Shall-statement fallback
# ---------------------------------------------------------------------------

_REQ_SECTION = re.compile(r"requirement|feature|function|use case|capabilit|user stor|system behaviou?r",
                          re.IGNORECASE)
_SHALL = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


class ShallStatementStrategy:
    name = STRATEGY_SHALL
    id_prefix = "SRS-AUTO-"

    def extract(self, blocks: Sequence[Block], ctx: SectionContext, report: ExtractionReport) -> List[Candidate]:
        found = self._scan(blocks, ctx, require_section=True)
        if not found:
            found = self._scan(blocks, ctx, require_section=False)
            if found:
                report.warn("No requirement IDs and no 'Requirements' section headings were found; "
                            "every shall/must sentence in the document was treated as a requirement.")
        elif found:
            report.warn(f"No requirement IDs were found; {len(found)} shall/must statement(s) were "
                        f"given generated IDs ({self.id_prefix}001...). Add IDs to the SRS for stable traceability.")
        for n, cand in enumerate(found, start=1):
            cand.raw_id = f"{self.id_prefix}{n:03d}"
        report.found(self.name, len(found))
        return found

    def _scan(self, blocks: Sequence[Block], ctx: SectionContext, require_section: bool) -> List[Candidate]:
        out: List[Candidate] = []
        for idx, block in enumerate(blocks):
            if not isinstance(block, Paragraph):
                continue
            path = ctx.path(idx)
            if require_section and not any(_REQ_SECTION.search(h) for h in path):
                continue
            for s_no, sentence in enumerate(_SENTENCE_SPLIT.split(block.text)):
                sentence = sentence.strip()
                if _SHALL.search(sentence) and len(sentence.split()) >= 4:
                    out.append(Candidate(
                        raw_id="", strategy=self.name, position=(idx, s_no),
                        locator=str(block.locator), description=sentence, section=path,
                        metadata={META_ID_SOURCE: "generated"},
                    ))
        return out
