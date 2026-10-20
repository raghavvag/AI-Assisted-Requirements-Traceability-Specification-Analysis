# SRS Format Guide

This guide covers which SRS documents the tool can read, how it finds the requirements in them, and what to change when it does not find them.

**Code:** `src/srs/` · **Design:** [`docs/plan.md` §2](plan.md) · **Tests:** `tests/test_srs_extraction.py`

---

## 1. Supported files

| Format | Notes |
|---|---|
| **Word `.docx`** | This is the main format. Most IEEE 830 and ISO/IEC/IEEE 29148 templates work as they are. |
| **PDF** | The PDF must have a text layer, such as one exported from Word or Google Docs. Scanned PDFs are rejected with a clear message, because OCR is not supported. |
| **Markdown `.md` / text `.txt`** | The original `REQ-N: Title` format. If the file does not use that format, the tool reads the Markdown structure instead (headings, tables, lists). |

These files are **rejected**:
- `.doc` (save it as `.docx`);
- `.docm` and any document that contains macros;
- `.rtf` and `.odt`;
- password-protected files;
- files larger than **10 MB**;
- `.docx` files that expand to more than **50 MB** when unzipped (zip-bomb protection).

## 2. How requirements are found

The tool tries three methods in this order. Every requirement keeps a note of which method found it and where it is in the file, for example `table 3 / row 2` or `page 5 / heading 13`.

### 2.1 Requirement tables (most reliable)

A table counts as a requirement table when its header row has:
- an **ID column**: `ID`, `Req ID`, `Req. No`, `Requirement ID`, `Ref`, `No.`, `#`, `FR ID`, `Use Case ID`, …
- a **text column**: `Description`, `Requirement`, `Requirement Description`, `Statement`, `Text`, `Details`, `User Story`, …

These columns are optional:

| Column header | Stored as |
|---|---|
| `Title` / `Name` / `Feature` / `Summary` | title |
| `Priority` / `MoSCoW` / `Importance` | priority |
| `Type` / `Category` | functional or non-functional |
| `Acceptance Criteria` / `Fit Criterion` / `Verification` | added to the requirement text |
| `Rationale` / `Justification` | kept, but **not** used for matching |
| `Source` / `Stakeholder` | origin |

Two other table layouts also work:
- **Requirement cards:** one two-column table per requirement, with rows such as `Requirement ID | FR-01`, `Title | …` and `Description | …`.
- **Tables without a header row:** at least 60% of the rows start with an ID such as `FR-1 | The system shall …`.

The tool also handles these table quirks:
- merged cells;
- vertically merged ID cells (the next row continues the same requirement);
- several paragraphs inside one cell;
- header rows repeated in the middle of a table;
- section rows that span the whole table;
- tables that continue onto the next page of a PDF.

### 2.2 Tagged headings and paragraphs

A heading or paragraph counts as a requirement when it **starts with** an ID:

```
FR-1: User Login             REQ-04. The system must …      [NFR-2] Encryption
FR1 - User Login             FR-5.1<TAB>Profile update      3.2.1 FR-7 Notifications
FR-2 The system shall …      BNK-REQ-01: Authentication      (UR-3) Export
```

- **Default ID pattern.** An optional prefix, then one of `REQ FR NFR SRS SR UR BR SYS FUNC US`, then a number, which may have dots (`FR-5.1`). Examples: `TF-REQ-01`, `SRS-FR-007`.
- **Where the body ends.** The requirement body is the text that follows, up to the next ID or the next heading of the same or higher level.
- **Labels.** Lines that start with `Description:`, `Priority:`, `Acceptance Criteria:` or `Rationale:` are sorted into the matching fields.
- **Cross-references.** An ID in the middle of a sentence ("as per FR-2") never starts a new requirement.

### 2.3 Fallback: "shall/must" sentences

This method is used only when methods 1 and 2 find nothing:
- Every sentence that contains *shall* or *must* inside a section whose heading mentions *Requirements*, *Features*, *Functions* or *Use Cases* becomes a requirement.
- These requirements get **generated IDs** (`SRS-AUTO-001`, …), and the preview shows a warning.
- Add real IDs to the SRS for traceability that stays stable between versions.

## 3. What is removed

| Removed | How it is recognised |
|---|---|
| Cover page, revision history, document control | Not tagged, or the section heading is on the boilerplate list |
| Table of contents | Word's TOC field, `TOC` paragraph styles, dot-leader lines |
| Definitions, acronyms, glossary, references, appendices, index, approvals and sign-off | Section heading. The whole section is skipped. |
| Images, shapes, text boxes | Never read |
| Headers and footers (Word) | Never read |
| Running headers, footers and page numbers (PDF) | Lines repeated in the top or bottom 8% of the page |
| Captions ("Figure 1: …", "Table 2 – …") | Caption style or caption pattern |
| Tracked deletions | Deleted text is dropped and inserted text is kept |

A heading is only treated as boilerplate when **every** part of it is a boilerplate word. So "Definitions, Acronyms and Abbreviations" is skipped, while real feature sections like "Cart Contents" or "Order Approval" are kept.

## 4. What the tool produces

Each requirement is returned in this form:

```json
{
  "id": "REQ-04", "title": "Refund Transaction Integrity",
  "text": "Refund Transaction Integrity. All refund transactions must include …",
  "type": "functional", "priority": "High",
  "section": "3 Specific Requirements > 3.1 Core Functional Requirements",
  "strategy": "table",
  "source": {"file": "SRS.docx", "format": "docx", "locator": "table 3 / row 5"}
}
```

- `text` is the only field that is embedded. It is built exactly as the original Markdown parser built it (`"{title}. {description}"`).
- Because of that, **the same requirements written in `.docx`, `.pdf` or `.md` produce identical scores**. `TestParity` in the test suite checks this.

## 5. When nothing is found

The upload is rejected with **HTTP 422**. The response includes an extraction report that lists:
- the format;
- how many blocks were read;
- what was skipped, and why.

Common causes and fixes:

| Cause | Fix |
|---|---|
| The IDs use an unusual scheme (`CAP-001`, `R12`) | Set a custom `id_pattern` (see §6) |
| The requirements are in a table with no ID column | Add an ID column, or put the ID at the start of each statement |
| The requirements sit under a heading named like boilerplate, for example "Appendix B: Requirements" | Rename the section |
| The PDF is scanned | Export it again from the original document |

## 6. Options (`ExtractionOptions`)

```python
from src.srs import ExtractionOptions, extract_requirements_with_report
reqs, report = extract_requirements_with_report(
    "SRS.docx",
    ExtractionOptions(id_pattern=r"CAP-\d{3}", include_nfr=False, extra_blocklist=("Stakeholders",)),
)
```

| Option | Default | Effect |
|---|---|---|
| `id_pattern` | see §2.2 | Regex for one requirement ID |
| `include_nfr` | `true` | Set it to `false` to drop non-functional requirements (types are taken from `NFR-` IDs or from Performance/Security/… sections) |
| `extra_blocklist` | `()` | More section headings to skip |
| `max_words` / `min_words` | 350 / 5 | Length warnings. The embedding model reads about 384 tokens, so longer text is cut off. |

These options will also be readable from `.traceability.yml` (`srs_extraction:`) in milestone M2.

## 7. Regenerating the test fixtures

```bash
python scripts/build_srs_fixtures.py          # .docx fixtures + golden JSON
python scripts/build_srs_fixtures.py --pdf    # also the PDF (Windows + Microsoft Word)
pytest tests/test_srs_extraction.py tests/test_upload_api.py -q
```
