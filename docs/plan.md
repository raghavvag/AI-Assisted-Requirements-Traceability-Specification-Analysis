# Production Readiness Plan

**Project:** AI-Assisted Requirements Traceability and Software Specification Analysis
**Stage:** Project-I prototype → Project-II, industry-aligned tool
**Date:** 2026-10-16
**Master checklist:** [`docs/leftover.txt`](leftover.txt). Every task there points back to a section here.

---

## 0. Why this plan exists

At the Project-I review the panel made one main criticism. **We read requirements from plain
`.md`/`.txt` files.** Real teams write their requirements in formal Software Requirements
Specification (SRS) documents, usually Word (`.docx`) or PDF, laid out to IEEE 830 or ISO/IEC/IEEE 29148.
Those documents have cover pages, revision tables, tables of contents, requirement tables, numbered
headings, images and a lot of boilerplate text.

Our parser (`src/parser.py:31 extract_requirements`) does three things:
- reads a file path as UTF-8 text;
- splits it on one regex, `^…REQ-\d+:`;
- raises `ValueError` if nothing matches.

A real SRS fails at the first step.

This plan covers four things:
1. **Section 1**: compares the codebase with the Project-I report (FR1–FR15 and the NFRs).
2. **Section 2**: designs the industrial SRS ingestion pipeline that answers the reviewers.
3. **Sections 3–6**: plan the remaining milestones (shared review engine, live GitHub App, CI/CD,
   code-aware alignment).
4. **Sections 7–8**: cover evaluation, sequencing, effort and risks.

### Decisions already made

| Topic | Decision |
|---|---|
| SRS formats | `.docx` + `.pdf` + `.md`/`.txt` |
| Webhook hosting (demo) | Local backend with a **smee.io** tunnel, no cloud hosting needed |
| Streamlit v1 app | **Retire it.** All new work goes into `backend/` + `frontend/`, with `src/` as the shared core |
| PR "intent" source | The body of the GitHub issue the PR links (`Closes #N`), split into acceptance criteria |
| PR ingestion | A full GitHub App with webhooks. A GitHub Action is also provided as a zero-infrastructure alternative |
| Code-aware model | `jinaai/jina-embeddings-v2-base-code`, behind the existing `AlignmentScorer` interface |
| Role of the LLM | **Explanation only, never the score.** Verdicts stay deterministic and auditable |

---

## 1. Gap analysis: Project-I report vs codebase

### 1.1 Functional requirements

| ID | Requirement (report §3.1.1) | Status | Evidence | Gap / action |
|---|---|---|---|---|
| FR1 | Upload SRS and OpenAPI files | **Partial** | `backend/app/routers/projects.py:138 upload_document` | Accepts only `.md .txt .json .yaml .yml`, and the same list applies to every doc type, so an SRS can be uploaded as `.json`. No size, MIME or content checks. `raw_text` is stored with a lossy `decode("utf-8", errors="replace")`. A re-upload with a new extension leaves the old file on disk. → §2.8 |
| FR2 | Parse and extract requirements | **Partial** | `src/parser.py:31` | Single regex, path only, UTF-8 only. Fails on any formal SRS. → **§2 (main work)** |
| FR3 | Parse endpoints, descriptions, schema | Done | `parser.extract_endpoints`, prance `$ref` resolution | Takes a file path only. The bot needs to pass a dict fetched from the GitHub API. → §3.2 |
| FR4 | Semantic embeddings | Done | `src/embedder.py SemanticEmbedder`, all-mpnet-base-v2, 768-d | — |
| FR5 | Semantic similarity | Done | `src/similarity.py compute_similarity_matrix` | — |
| FR6 | Traceability matrix | Done | `build_traceability_matrix`; Top-K via `get_top_endpoints_for_requirement` | — |
| FR7 | Configurable thresholds | Done, but scattered | `threshold` parameters; `.env` keys | `SIMILARITY_THRESHOLD`, `THRESHOLD_GREEN` etc. are ignored by `Settings` (`extra="ignore"`) and read through `os.getenv` inside `src/`. → §3.3 `.traceability.yml` |
| FR8 | Cross-version drift detection | Done | `src/drift_detector.py compute_drift_report` | Known limit: the REQ-04 refund drift (Δ −0.061) is below the matrix drift threshold. Only the PR reviewer's tiered rule catches it. → §6 (field-level / code-level units) |
| FR9 | Drift severity | Done | `classify_severity` | Duplicated in `pr_reviewer._classify_severity`. → §3.2 |
| FR10 | Natural-language explanations | Done | `src/explainer.py` (OpenAI → Groq → template) | Never raises, so it is safe to call in CI without keys |
| FR11 | Precision / Recall / F1 / MAP | Done (built-in data only) | `src/evaluator.py`; F1 0.71, Recall 1.00 | Uploaded projects always use the default ground truth (`analysis.py:85`). → §7 optional ground-truth upload |
| FR12 | TF-IDF baseline | Done | `compute_tfidf_similarity_matrix`; F1 0.34 | — |
| FR13 | PR compliance review | **Simulated** | `src/pr_reviewer.py`, 3 hardcoded scenarios | `run_pr_review` takes a scenario key, not real changes. → §3.2, §4 |
| FR14 | Project-based web app | Done | React 18 + Vite + FastAPI + Google OAuth/JWT | Also has a duplicate Streamlit app. → retire it (§3.1) |
| FR15 | Vector database | Done | Qdrant `query_points`, NumPy fallback | — |

### 1.2 Non-functional requirements

| NFR | Status | Gap |
|---|---|---|
| Performance | OK | The PR-review endpoint re-encodes requirements on every call. Cache embeddings per analysis (minor). |
| Scalability | OK for the prototype | One `AnalysisResult` per project, which is fine for now |
| Reliability | **Gap** | Parse errors become HTTP 500 `Pipeline error`. Upload is not validated. The frontend shows a generic "Failed to upload SRS" toast and drops the backend's `detail`. |
| Security | **Gap** | No upload size limit, no magic-byte check, no zip-bomb guard for `.docx`. Project isolation through JWT is done. |
| Usability | OK | The user cannot see which requirements were extracted before running the analysis. → §2.9 preview |
| Maintainability | **Gap** | Two parallel implementations (`src`+Streamlit and `backend`+`frontend`). No headless CLI. → §3 |
| Extensibility | Partial | The `src/alignment/` contracts exist. `GitDiffArtifactSource`, `PythonASTArtifactSource`, `JiraContextSource` and `SiameseBertAlignmentScorer` are stubs. → §6 |
| Explainability | Done | — |
| Testability (implied) | **Gap** | 59 tests (48 pipeline + 11 alignment). No backend router tests, no `pr_reviewer` tests, no CI, no `pytest.ini`/`conftest.py`. |

### 1.3 Summary

The **semantic core is done and validated**: parsing endpoints, embeddings, similarity, the matrix,
drift, explanations, metrics and the vector store. What is still missing is mostly at the edges of
the system:
1. **Getting real documents in.** This is the industrial SRS parser.
2. **Getting results out to where developers work.** This means the GitHub App and CI.
3. **One clean codebase.** This means retiring Streamlit and having one review engine.

---

## 2. Industrial SRS ingestion (answer to the reviewers)

### 2.1 Design goals
1. **Accept real documents.** Word `.docx` (python-docx), PDF (pdfplumber) and the existing `.md`/`.txt`.
2. **Find requirements wherever authors put them:** in requirement tables, in tagged
   paragraphs or headings (`FR1`, `FR-1.2`, `REQ-04`, `[NFR-3]`, `BNK-REQ-01`), and as a last resort
   in "shall/must" statements inside requirement sections.
3. **Throw away noise.** This includes images, cover pages, revision tables, the table of contents,
   glossary, references, appendices, captions, page numbers, running headers and footers, and
   formatting glyphs.
4. **Zero downstream change.** The public function `src.parser.extract_requirements(path)` keeps its
   signature and its `{id, title, text}` output. The embedder, similarity, drift, evaluator,
   explainer, vector store and alignment source stay untouched.
5. **Transparent.** Every run produces an `ExtractionReport` that says what was found, by which
   strategy, what was skipped and why. The UI shows it before the analysis runs.

### 2.2 Pipeline

```
 upload / path
      │
      ▼
 ┌──────────────┐   ┌─────────────────────────────┐   ┌─────────────┐   ┌──────────────────────┐   ┌─────────────┐
 │ Reader       │──►│ Block stream (common IR)    │──►│ Noise filter│──►│ Strategies           │──►│ Normaliser  │──► requirements[] + ExtractionReport
 │ docx|pdf|md  │   │ Heading(level,text)         │   │ sections,   │   │ 1 Table              │   │ IDs, titles,│
 └──────────────┘   │ Paragraph(text,style,list)  │   │ captions,   │   │ 2 Tagged para/heading│   │ metadata,   │
                    │ Table(rows[][])  + locator  │   │ TOC, glyphs │   │ 3 Shall-statements   │   │ dedupe      │
                    └─────────────────────────────┘   └─────────────┘   └──────────────────────┘   └─────────────┘
```

All three formats turn into the **same block stream**, so the strategies are written once and
tested once.

### 2.3 Module layout

```
src/srs/
  __init__.py          extract_requirements_with_report(path, options) -> (list[dict], ExtractionReport)
  blocks.py            Heading / Paragraph / Table dataclasses, Block union, Locator
  options.py           ExtractionOptions (id_pattern, include_nfr, extra_blocklist, ...)
  readers/
    docx_reader.py     python-docx → blocks
    pdf_reader.py      pdfplumber  → blocks
    markdown_reader.py legacy regex fast path + markdown → blocks
  noise.py             section blocklist, line filters, text cleaning
  strategies.py        TableStrategy, TaggedStrategy, ShallStatementStrategy
  normalise.py         canonical IDs, titles, metadata, dedupe, length warnings
  report.py            ExtractionReport
src/parser.py          extract_requirements(path) → dispatch by extension (signature unchanged)
```

### 2.4 Readers

**DOCX (`python-docx` ≥ 1.2):**
- Walk the body **in document order** with `Document.iter_inner_content()`, so paragraphs and
  tables stay interleaved and a requirement table stays inside its section.
- Work out the heading level from, in order:
  1. a `Heading N`/`Title` style;
  2. the paragraph's `w:outlineLvl`;
  3. a numbered-heading regex `^\d+(\.\d+)*\s+\S` on short, bold paragraphs. Many corporate
     templates use custom heading styles, so this fallback matters.
- **Content controls (`w:sdt`).** SRS templates often wrap fields in content controls. Unwrap
  `w:sdt/w:sdtContent` children so their text is not lost.
- **Tracked changes.** Keep inserted text (`w:ins` runs). Ignore deleted text (`w:delText`).
- **Tables:**
  - Dedupe horizontally merged cells by `cell._tc` identity (python-docx returns the same cell
    once per grid column it spans).
  - Join multi-paragraph cells with spaces.
  - Detect repeated header rows (`w:tblHeader`).
  - Treat a 1×1 table (a callout box) as a paragraph.
  - Recurse into nested tables.
- The reader never touches images, shapes, headers, footers, comments or footnotes. Reading only the
  body removes most of the noise for free.

**PDF (`pdfplumber`):**
- Find tables with `page.find_tables()`. Extract text *outside* the table bounding boxes so table
  text is not duplicated.
- Infer headings from font size and weight: a line counts as a heading if its size is at least 1.15×
  the median body size, or if it is bold, short and numbered.
- **Running headers and footers:** remove lines in the top or bottom 8% of the page that repeat on
  at least 50% of pages. Also remove page-number lines (`^\d+$`, `Page \d+ of \d+`).
- Remove hyphenation at line breaks and join wrapped lines into paragraphs.
- **Scanned PDF** (no text layer): fail with a clear message, "This PDF has no text layer (scanned).
  OCR is not supported; export the SRS as .docx or a text PDF". OCR is out of scope.

**Markdown / text:**
- **Run the existing regex first.** This keeps today's behaviour exactly, so the 48 existing
  pipeline tests keep passing.
- Only if that finds nothing, convert to blocks (`#` headings, pipe tables, paragraphs) and run the
  strategies.

### 2.5 Noise filtering (`noise.py`)

| Noise | Rule |
|---|---|
| Front and back matter | Skip a whole section when its heading matches the blocklist: *table of contents, contents, revision history, document history, change log, document conventions, intended audience, reading suggestions, definitions, acronyms, abbreviations, glossary, references, bibliography, appendix, index, approval(s), sign-off*. Users can extend the list. |
| Table of contents | Paragraph style starts with `TOC`, or the line ends with dot leaders followed by a page number |
| Captions | `^(Figure|Fig\.|Table|Diagram)\s*\d+` |
| Page numbers, empty lines, glyph-only lines | `^\s*(Page\s+)?\d+(\s+of\s+\d+)?\s*$`, whitespace, lines that are only `•▪◦–—*` |
| Images and shapes | Never read (DOCX). For PDF, images are not text. |
| Text cleanup | Unicode NFKC; replace smart quotes and dashes with ASCII; remove NBSP and zero-width characters; strip bullet glyphs; collapse whitespace |

### 2.6 Extraction strategies

The strategies run in priority order. Results are merged by canonical ID: the first source wins and
conflicts are added to the report as warnings.

1. **TableStrategy** (most reliable; industrial SRSs often use requirement tables)
   - A table counts as a requirement table when its header row has:
     - an **ID column**, matching `id | req id | req. no | requirement id | ref | no.`, **and**
     - a **text column**, matching `description | requirement | statement | text | details`.
   - Optional columns are mapped into metadata: `title|name|feature`, `priority`, `type|category`,
     `acceptance criteria`, `rationale`, `source`.
   - Each data row becomes one requirement. Rows with an empty ID or empty text are skipped and
     reported.

2. **TaggedStrategy** (for paragraphs and headings that start with an ID tag)
   - Default tag regex (can be overridden with `srs.id_pattern`):
     ```
     (?:[A-Z][A-Z0-9]{0,9}[-_.])?(?:REQ|FR|NFR|SRS|SR|UR|BR|SYS|FUNC|US)[-_. ]?\d{1,4}(?:\.\d{1,3})*
     ```
   - The tag can be wrapped as `[FR-1]` or `(FR-1)`. It must be followed by `: – — - . )` or a tab,
     or be the start of a heading line such as `3.2.1 FR-1 User Login`.
   - The body is made of the following paragraphs and list items, until the **next tag** or a
     **heading of the same or higher level**.
   - A tag is only recognised at the **start** of a block, so cross-references like "see REQ-02" in
     running text never split a requirement.

3. **ShallStatementStrategy** (fallback, only runs when strategies 1 and 2 find nothing)
   - Inside sections whose heading matches `requirement|feature|function|use case|capabilit`, each
     sentence containing *shall* or *must* becomes a requirement.
   - IDs are generated as `SRS-AUTO-001…` and the requirement is flagged `id_source: "generated"`.
   - Typical case: an informal SRS with no IDs.

### 2.7 Normalisation and output contract
- **Canonical ID:** trim, upper-case the prefix, turn internal whitespace into `-` (`FR 3` → `FR-3`).
  The original stays in `raw_id`.
- **Title:** the Title column if there is one; otherwise the text up to the first `:` or the end of
  the first sentence (at most 12 words); otherwise the first 8 words.
- **Text** (what gets embedded) = `"{title}. {description}"` + `" Acceptance criteria: …"` if
  present. Whitespace is collapsed. This is the same composition the `.md` parser uses today, so
  scores stay comparable.
- **Metadata:**
  - `priority` comes from a column or an inline `Priority: High` (the inline form is removed from
    the text);
  - `rationale` is kept in metadata and **not** embedded;
  - `type` is `functional` or `non_functional`, taken from the ID prefix (`NFR`) or the section
    heading (`Non-Functional`, `Performance`, `Security`, …).
- **Checks:**
  - duplicate ID: keep the first and add a warning;
  - text over ~350 words: warning, because mpnet truncates at 384 tokens;
  - text under 5 words: warning.
- **`include_nfr`** (default `true`): when `false`, non-functional requirements are dropped. This
  helps the metric runs, which have functional ground truth only.

**Output contract (backward compatible).** Required fields: `id`, `title`, `text`. Optional fields:
`type`, `priority`, `section`, `raw_id`, `id_source`, and `source = {file, format, locator}`.

```json
{
  "id": "REQ-04",
  "title": "Refund Transaction Integrity",
  "text": "Refund Transaction Integrity. All refund transactions must include ...",
  "type": "functional",
  "priority": "High",
  "section": "3.2 Payments",
  "raw_id": "REQ-04",
  "source": { "file": "ecommerce_srs_ieee.docx", "format": "docx", "locator": "table 2 / row 5" }
}
```

Every consumer only reads `id`, `title` and `text`: embedder `r["text"]`; similarity, drift,
evaluator and explainer read `id`/`title`/`text`; the vector store payload; `schemas.RequirementNode`.
So the extra keys are safe.

**`ExtractionReport`** contains:
- `format`;
- `n_requirements`;
- `by_strategy` (`{"table": 6, "tagged": 4}`);
- `skipped` (counts by reason: `toc`, `caption`, `blocklisted_section:Revision History`, …);
- `duplicates`;
- `warnings[]`;
- `options_used`.

### 2.8 Backend changes
- **Per-doc-type allow-list:**
  - `SRS`: `.docx .pdf .md .txt`
  - `API_V1`/`API_V2`: `.json .yaml .yml`
- **Limits and checks:**
  - `MAX_UPLOAD_MB = 10` (setting), returns 413;
  - magic-byte check: `.docx` must start with `PK\x03\x04`, `.pdf` with `%PDF`, otherwise 400;
  - reject `.docm` (macro-enabled);
  - zip-bomb guard: the sum of `ZipInfo.file_size` must be ≤ 50 MB.
- **Parse at upload time** inside `run_in_threadpool`:
  - 0 requirements → **422** with the `ExtractionReport` (warnings + skipped counts), so the user can
    fix the document;
  - success → store the extracted JSON and the report in the existing, unused `Document.raw_text`
    column, so no schema migration is needed.
- **Clean up** the stale `SRS.<old-ext>` file when a new extension is uploaded.
- **New endpoint:** `GET /api/projects/{id}/srs/requirements` returns `{requirements, report}` for
  the preview.
- **Unchanged:** `pipeline.run_analysis_pipeline` still calls `extract_requirements(srs_path)`.
  Parsing is deterministic, so a re-parse at analysis time gives the same result.
- **Turn parse errors into 422** (`ValueError`) instead of a generic 500.

### 2.9 Frontend changes
- `DashboardPage.jsx UploadModal`:
  - set `accept` per dropzone (SRS: `.docx .pdf .md .txt` with the right MIME types; specs:
    `.json .yaml .yml`);
  - SRS label: "Requirements SRS (.docx / .pdf / .md / .txt)";
  - show `err.response.data.detail` in the error toast.
- New `components/SrsPreview.jsx`, built from the existing `components/ui.jsx` primitives:
  - "Extracted **N** requirements · 6 from tables · 4 from tagged headings";
  - warnings list;
  - collapsible table (ID · title · type · priority · source locator).
  - It appears after an SRS upload and on the project page before "Run analysis".
- Traceability page: show `type` and `priority` badges on requirement nodes when present (optional).

### 2.10 Fixtures and tests
`scripts/build_srs_fixtures.py` generates the fixtures deterministically with python-docx and
commits them to `tests/fixtures/srs/`:

| Fixture | What it exercises | Expected |
|---|---|---|
| `ecommerce_srs_ieee.docx` | IEEE-830 layout:<br>• cover page and revision-history table<br>• TOC field and boilerplate (Purpose, Scope, Definitions)<br>• an embedded PNG and a caption<br>• REQ-01..06 in an `ID / Title / Description / Priority` table<br>• REQ-07..10 as tagged `Heading 3` + body paragraphs<br>• an appendix | Exactly REQ-01..10. **Text identical** to `data/requirements/ecommerce_srs.md` |
| `messy_srs.docx` | • `FR1`, `FR-2`, `FR 3`, `[REQ-4]`, `FR-5.1`<br>• merged cells and multi-paragraph cells<br>• repeated header row and a 1×1 callout table<br>• content control and tracked insertion + deletion<br>• inline cross-reference ("as per FR-2")<br>• duplicate ID<br>• NFR section | Golden JSON; 1 duplicate warning; NFRs typed `non_functional` |
| `banking_srs.docx` | BNK-REQ-01..08 in tables (mirrors `data/samples/banking`) | 8 requirements |
| `untagged_srs.docx` | No IDs, only "The system shall…" under "Functional Requirements" | `SRS-AUTO-*` via the shall-statement fallback |
| `ecommerce_srs_ieee.pdf` | PDF exported once from the first fixture (Word "Save as PDF", committed) | Same IDs and texts as the docx (whitespace-normalised) |
| `scanned.pdf` | Image-only PDF | Clear "no text layer" error |

Each fixture has a golden `*.expected.json` next to it.

The tests:
- **Golden tests:** IDs, titles and texts match the golden file exactly.
- **Noise tests:** no extracted text contains `Table of Contents`, `Revision`, `Figure`, `Page`, or
  any glossary terms.
- **Parity test:** the `.docx` requirements equal the `.md` requirements, so the embeddings and
  metrics are identical. Assert F1 = 0.71 ± 0.005 and an unchanged MAP on store_v1. This is the key
  evidence for the reviewers: *the formal SRS document produces exactly the same traceability as the
  hand-written prototype input*.
- **Backend tests** (`httpx.AsyncClient` against the app): 413 oversize, 400 wrong extension or bad
  magic bytes, 422 for a docx with no requirements, 201 for the happy path with an extraction
  summary, and a stale-file cleanup test.

### 2.11 Acceptance criteria for the milestone
- All 59 existing tests still pass, plus the new parser and backend tests.
- A real-world IEEE-830 student or industry SRS `.docx` (manual QA, at least 2 public samples)
  extracts with **no noise** and **at least 95% of the tagged requirements** found.
- In the UI: upload `.docx` → preview → analyse → the same matrix as the `.md` dataset.

---

## 3. Consolidation and a shared review engine

This milestone is a prerequisite for both the GitHub App and CI. Both must call **the same engine**
as the simulated PR page, so all three always agree.

### 3.1 Retire the Streamlit v1 app
- Delete `app/streamlit_app.py`, `src/auth.py`, `src/database.py` and `data/_uploads/`.
  Grep first to confirm that nothing outside Streamlit imports them.
- Remove `streamlit` and any v1-only dependencies from the root `requirements.txt`. The root
  requirements become the **core pipeline** dependencies, used by the CLI and CI.
  `backend/requirements.txt` adds the API layer.
- Update the references in `startup_guide.md`, `knowledge.md`, `roadmap.md`,
  `docs/PROJECT_REPORT_SOURCE.md` and the README.

### 3.2 Engine
- `src/parser.py`: split `extract_endpoints_from_spec(spec: dict, source: str = "")` out of the path
  loader. `extract_endpoints(path)` becomes a thin wrapper around it.
- `src/review/engine.py`:
  ```python
  review_spec_change(requirements, spec_base: dict, spec_head: dict,
                     intent: list[ContextUnit] | None = None,
                     policy: ReviewPolicy | None = None,
                     embedder=None, explainer=None) -> ReviewResult
  ```
  It works in five steps:
  1. **Diff endpoints** by normalised path (reuse `drift_detector._normalise_endpoint_path`) into
     added, removed and modified (where `text` changed).
  2. **Regression check.** For each modified or removed endpoint, score base vs head against every
     requirement and apply the **existing tiered rule** (`PRReviewer._is_regression`: score_v1 ≥ 0.60
     → a drop of 0.05 flags; score_v1 ≥ 0.35 → a drop of 0.08 flags). Move the rule into the engine
     as one shared function.
  3. **Intent check** (optional). Score the issue's acceptance criteria against added and modified
     endpoints with `EmbeddingAlignmentScorer` + `ThresholdAlignmentGate`.
  4. **Severity** uses `drift_detector.classify_severity`. Remove the duplicate in `pr_reviewer`.
  5. **Verdict:**
     - **BLOCK** for any regression, or when the intent gate returns BLOCK;
     - **WARN** for a partial intent match;
     - **PASS** otherwise.

     The engine also renders Markdown, reusing `_build_pr_comment`.
- `PRReviewer.run_pr_review(scenario_key, …)` turns each simulated scenario into a pair of base and
  head mini-specs and calls the engine. The PR Bot page and its 3 expected verdicts keep working.

### 3.3 Config: `.traceability.yml`
`src/config.py` loads and validates the config (pydantic). It is the **single source** for
thresholds. Env vars become overrides only.

```yaml
srs: docs/SRS.docx
openapi: openapi.yaml            # or a list
thresholds:
  compliant: 0.60
  partial: 0.40
  drift: -0.10
  regression_strong: -0.05       # tiered rule, score_v1 >= 0.60
  regression_weak: -0.08         # tiered rule, score_v1 >= 0.35
fail_on: block                   # block | critical | never
srs_extraction:
  id_pattern: null               # override the default tag regex
  include_nfr: true
  extra_blocklist: ["Stakeholders"]
```

### 3.4 Headless CLI: `python -m src.cli`
- `extract-srs <file> [--json]` prints the requirements and the extraction report. It is also handy
  for live demos.
- `analyze --srs --spec [--ground-truth]` prints the matrix, compliance and, if labels are given,
  metrics.
- `review --srs --base-spec --head-spec [--issue-body FILE] [--config .traceability.yml]
  --format md|json --fail-on block|critical|never`
- **Exit codes:** 0 = PASS/WARN, 1 = BLOCK, 2 = tool or config error.
- Works **without Qdrant** (NumPy similarity) and **without LLM keys** (template explainer).

### 3.5 Test infrastructure
- `pytest.ini`: `testpaths = tests`, plus a `slow` marker for tests that download or load the model.
- `tests/conftest.py`: a session-scoped embedder fixture (loaded once) and a shared `sys.path` setup,
  replacing the per-file `sys.path.insert`.
- New tests: the engine (3 scenarios → PASS/BLOCK/BLOCK), the tiered-rule unit table, the CLI exit
  codes, and `extract_endpoints_from_spec`.

---

## 4. Live GitHub App (PR Review Bot)

### 4.1 Architecture

```
GitHub ──webhook──► smee.io ──► localhost:8000/api/github/webhook
                                     │ verify HMAC, dedupe delivery, 202
                                     ▼ BackgroundTasks
                         ┌────────────────────────────────┐
                         │ 1 create Check Run in_progress │
                         │ 2 read .traceability.yml @head │
                         │ 3 list PR files                │
                         │ 4 fetch base+head spec, SRS    │──► src/srs (docx parser, §2)
                         │ 5 fetch linked issue → ACs     │
                         │ 6 review_spec_change()         │──► src/review/engine (§3)
                         │ 7 complete Check Run           │
                         │ 8 upsert PR comment            │──► ComplianceExplainer
                         │ 9 persist PRReviewRun          │──► SQLite → React "Live reviews"
                         └────────────────────────────────┘
```

### 4.2 Authentication
- New settings in `backend/app/core/config.py`: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`,
  `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_SLUG`, `GITHUB_CHECK_NAME="Requirements Traceability"`.
- The **app JWT** is RS256, signed with the private key through the existing
  `python-jose[cryptography]`, with `iat = now−60s`, `exp = now+9min` and `iss = app_id`.
- The **installation token** comes from `POST /app/installations/{id}/access_tokens` and is cached
  until 5 minutes before it expires.
- The client is `httpx.AsyncClient` (already a dependency), in
  `backend/app/services/github/{auth.py, client.py}`. It handles 403/429 rate limits by honouring
  `Retry-After` and `X-RateLimit-Reset`.

### 4.3 Webhook endpoint (`backend/app/routers/github.py`)
- `POST /api/github/webhook`:
  - Read the **raw body**. Verify `X-Hub-Signature-256` = `sha256=` + HMAC-SHA256(secret, body)
    with `hmac.compare_digest`. Return 401 on mismatch.
  - Dedupe on `X-GitHub-Delivery`, kept in a small table or an LRU, so redeliveries are idempotent.
  - Events handled:
    - `ping` (200);
    - `installation` and `installation_repositories` (log them);
    - `pull_request` with action `opened | synchronize | reopened | edited` (only when the body
      changed, because the linked issue may have changed);
    - `check_run` with action `rerequested` (the "Re-run" button);
    - everything else returns 204.
  - Return **202 at once** and do the work in `BackgroundTasks`, because GitHub times out after 10 s.
- Register the router in `backend/app/main.py`. The endpoint is not JWT-protected; HMAC is the
  authentication.

### 4.4 Review flow
1. **Create a Check Run** (`POST /repos/{o}/{r}/check-runs`, `status: in_progress`, `head_sha`).
2. **Load config.** Fetch `.traceability.yml` at the head SHA through the contents API. If it is
   missing, complete the run as `neutral` with "No .traceability.yml, skipping".
3. **List PR files** (`GET /pulls/{n}/files`, paginated). If no configured spec or SRS path changed,
   complete as `neutral` with "No specification change".
4. **Fetch artefacts.** Get the base and head spec (`?ref=base_sha` / `?ref=head_sha`). An `added`
   status means the spec is new and there is no base. Get the SRS at head: a `.docx` is decoded from
   base64, written to a temp file and parsed with **§2**.
5. **Get the linked issue.** Parse the PR body with
   `(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#(\d+)`, and also query GraphQL
   `closingIssuesReferences`. Fetch the issue and split its body into acceptance criteria:
   - checkbox lines `- [ ]` / `- [x]`;
   - bullets under an "Acceptance Criteria" heading;
   - otherwise the whole body as one unit.
6. **Run `review_spec_change`** with the SRS requirements, the base and head specs, and the issue
   acceptance criteria.
7. **Complete the Check Run:**
   - `conclusion` is `success` (PASS), `neutral` (WARN) or `failure` (BLOCK);
   - `output.title` reads e.g. "BLOCK: 2 requirements regressed";
   - `output.summary` holds a table with requirement, endpoint, score base→head, Δ and severity;
   - `output.text` holds the explanations.
8. **Upsert one PR comment.** Find the earlier comment by the hidden marker
   `<!-- traceability-bot -->` and `PATCH` it instead of posting a new one on every push.
9. **Persist** a `PRReviewRun` row: id, installation_id, repo, pr_number, head_sha, delivery_id,
   verdict, violations_json, check_run_url and created_at.

### 4.5 Blocking merges
In the demo repo, branch protection on `main` marks the "Requirements Traceability" check as a
**required status check**. A `failure` conclusion then blocks the merge button. This is the
production meaning of BLOCK.

### 4.6 GitHub App registration
- **Permissions:** Checks R/W, Pull requests R/W, Issues R, Contents R, Metadata R.
- **Events:** Pull request, Check run.
- **Webhook URL:** a smee channel, e.g. `https://smee.io/<channel>`. Locally, run
  `npx smee-client -u https://smee.io/<channel> -t http://127.0.0.1:8000/api/github/webhook`.
- Keep the private key `.pem` outside the repo and add it to `.gitignore`.

### 4.7 Demo repository `traceability-demo-store`
- `docs/SRS.docx`: the `ecommerce_srs_ieee.docx` fixture, so the bot shows off the docx parser too.
- `openapi.yaml`: store_v1.
- `.traceability.yml`, plus branch protection.
- Three issues and three PRs that mirror the simulated scenarios:

  | PR | Linked issue | Change | Expected |
  |---|---|---|---|
  | add discount_code to cart | "Support discount codes" | Adds an endpoint | **PASS** |
  | refund cleanup | "Simplify refunds" | Drops the client `transaction_id` | **BLOCK** on REQ-04 |
  | faster checkout | "Reduce checkout friction" | Drops 2FA | **BLOCK** on REQ-09 |

### 4.8 Frontend
`PRBotPage.jsx` gets two tabs:
- **Simulated**: today's page.
- **Live reviews**: a list of `PRReviewRun` rows (repo, PR link, verdict badge, time) with a detail
  view that reuses the existing violation cards.

New endpoint: `GET /api/github/reviews`. It is JWT-protected and filtered to repos the user
registered. For the prototype it shows all.

### 4.9 Tests
- HMAC: valid, invalid and missing signature.
- Recorded webhook payload fixtures (`tests/fixtures/github/*.json`).
- The GitHub REST and GraphQL API mocked with `httpx.MockTransport`. The full flow is asserted for
  the 3 scenarios: check-run conclusion, comment upsert, persisted row.
- The issue-link regex and the acceptance-criteria splitter.
- Idempotent redelivery.
- Token caching and expiry.

---

## 5. CI/CD pipeline integration

### 5.1 This repo's own CI: `.github/workflows/ci.yml`
- Triggers: `push` and `pull_request`.
- **Python job** (3.11):
  - pip cache;
  - `actions/cache` on `~/.cache/huggingface`, keyed on the model name, so the 420 MB mpnet download
    happens once;
  - `ruff check`;
  - `pytest -m "not slow"` on every PR, and the full suite, including slow model tests, on `main`.
- **Frontend job:** Node 20, `npm ci`, `npm run build`, plus lint if it is configured.

### 5.2 Semantic compliance gate: `.github/workflows/compliance.yml`
- Triggers: `pull_request`, and `push` to `main` and `develop`.
- Steps:
  1. `actions/checkout` with `fetch-depth: 0`.
  2. Set up Python and restore the HF cache.
  3. `pip install -r requirements.txt`.
  4. Get the base spec with `git show ${{ github.event.pull_request.base.sha }}:openapi.yaml > /tmp/base.yaml`.
     On `push`, use `github.event.before`.
  5. Run `python -m src.cli review --config .traceability.yml --base-spec /tmp/base.yaml --head-spec openapi.yaml --format md --fail-on block | tee report.md`.
  6. Append `report.md` to `$GITHUB_STEP_SUMMARY`. Upload `report.json` as an artifact.
- The non-zero exit code fails the job. As a required check, it **blocks the merge or deployment**.
- `fail_on: critical` is an option: for `push` to `main` it fails only on Critical drift, which
  answers the old TODO "pipeline fails if critical specification drift is detected".

### 5.3 Reusable action: `action.yml` at the repo root
A composite action with inputs `config`, `base-ref`, `fail-on` and `python-version` that wraps the
same CLI. Any repository can add:

```yaml
- uses: raghavvag/<repo>@v1
  with: { config: .traceability.yml, fail-on: block }
```

**App vs Action, explained in the docs:**
- The **App** gives rich check runs and comments, and needs a server or tunnel.
- The **Action** needs no infrastructure, runs on GitHub's runners, and reports through the job
  summary.

Both share the engine, so the verdicts are identical.

### 5.4 Self-test
A workflow job runs the CLI on `data/api_specs/store_v1.json` → `store_v2.json` and **expects exit
code 1** with REQ-04 and REQ-09 in the report. If the gate ever stops blocking known drift, the job
fails.

---

## 6. Code-aware expansion (optional Phase 2)

The aim is to align requirements and issues with **source code**, not just OpenAPI specs. This
matters for repos with no spec, and it fixes the REQ-04 sub-threshold drift, because a field
disappearing from a handler is clearer in code than in prose.

- **`PythonASTArtifactSource(file_paths)`** (fills the stub in `src/alignment/sources.py`). It emits
  one `ArtifactUnit` per `FunctionDef`, `AsyncFunctionDef` and `ClassDef`. The text is a semantic
  fingerprint, not raw source:
  - qualified name and signature with type hints;
  - docstring;
  - decorators (a FastAPI `@app.post("/refund")` links the code to the endpoint);
  - pydantic model field names used in the signature;
  - names of called functions one hop out.

  `location` is `path:line`.
- **`GitDiffArtifactSource(repo, base, head)`**: `git diff --unified=0`. Each hunk is mapped to its
  enclosing symbol through the AST, and only changed symbols are emitted.
- **`SiameseBertAlignmentScorer`**: `jinaai/jina-embeddings-v2-base-code` with
  `trust_remote_code=True`, normalised cosine, same contract (bounds, determinism, rank).
- **`CompositeAlignmentScorer`**: picks a scorer by `ArtifactUnit.kind`. Prose and endpoints go to
  mpnet; functions, classes and diff hunks go to the jina code model. An optional
  `CrossEncoderReranker` re-ranks the top-K.
- **Evaluation:**
  - a small demo FastAPI service (≈15 handlers), with hand-labelled requirement↔function links in
    `data/ground_truth/code_labels.csv`;
  - P/R/F1/MAP through the existing `src/evaluator.py`;
  - compared with mpnet-only;
  - **phase gate:** per-acceptance-criterion F1 ≥ 0.75.
- **Bot integration:** when a PR changes code but no spec, the engine uses code units (if
  `.traceability.yml` has `code: { paths: ["app/**/*.py"] }`).
- Add contract tests that every `AlignmentScorer` must pass, as listed in
  `docs/architecture/semantic-alignment-roadmap.md` Phase A.

---

## 7. Evaluation, documentation, submission

1. **Extraction accuracy.** Precision and recall of extracted requirements against the golden
   files, per fixture and for at least 2 public real-world SRS documents. Targets: 100% on clean
   fixtures and ≥ 95% on messy ones. Report the noise items removed per category.
2. **Metric parity after the docx change.** Re-run P/R/F1/MAP for `.md`, `.docx` and `.pdf`, which
   must be identical (F1 0.71 vs TF-IDF 0.34, Recall 1.00). Also the banking dataset.
3. **Live bot evaluation.** The 3 demo PRs should give 1 PASS and 2 BLOCK. Record:
   - screenshots of the check runs, comments and the blocked merge button;
   - latency from webhook to check completion;
   - LLM-off vs LLM-on comment quality.
4. **CI evaluation.** The self-test job result and the run time with a warm and a cold HF cache.
5. **Optional: ground-truth upload** (`GROUND_TRUTH` doc type, `.csv`), so uploaded projects can
   compute metrics. This closes the FR11 gap.
6. **Documentation:**
   - update `docs/validation_results.md`, `startup_guide.md` (GitHub App + smee setup) and
     `docs/PROJECT_REPORT_SOURCE.md`;
   - add `docs/github-app-setup.md`;
   - refresh the architecture diagram;
   - add the Project-II report chapters: SRS ingestion methodology, live workflow integration,
     results.
7. **Paper and patent.** Write up the method (block-IR SRS extraction → semantic traceability →
   tiered regression gate → workflow enforcement) and the results tables.

---

## 8. Sequencing, effort, risks

| Milestone | Content | Effort | Depends on |
|---|---|---|---|
| **M0** Housekeeping | Commit the pending frontend work-in-progress, add `pytest.ini`/`conftest.py`, green baseline | 0.5 d | — |
| **M1** Industrial SRS ingestion | §2 | 5–6 d | M0 |
| **M2** Consolidation + engine + CLI | §3 | 2–3 d | M1 (the CLI uses the new parser) |
| **M3** Live GitHub App | §4 | 5–6 d | M2 |
| **M4** CI/CD | §5 | 1.5–2 d | M2 (can run in parallel with M3) |
| **M5** Code-aware (optional) | §6 | 5+ d | M2 |
| **M6** Evaluation + docs | §7 | 2–3 d | M1–M4 |

**Critical path:** M0 → M1 → M2 → M3 → M6, about 15–19 working days. M4 can run alongside M3.

| Risk | Impact | Mitigation |
|---|---|---|
| PDF heading and table heuristics vary widely | Missed or merged requirements | Tables and tags first; the report shows warnings; recommend `.docx` in the UI; golden tests |
| Corporate SRS uses a custom ID scheme | 0 requirements found | Configurable `id_pattern`; shall-statement fallback; the 422 response explains what was seen |
| Long requirement text is truncated by mpnet (384 tokens) | Lower scores | Length warning; acceptance criteria could become sub-units later |
| GitHub webhook 10 s timeout | Redeliveries, duplicate comments | Return 202 at once and work in the background; dedupe by delivery ID; upsert the comment |
| smee drops events while the laptop sleeps | Missed review in a demo | The check-run "Re-run" button (`rerequested`); manual redeliver from the App settings |
| HF model download in CI (420 MB) | Slow or flaky CI | `actions/cache`; `not slow` marker on PRs |
| Private key leak | Security | `.pem` outside the repo and gitignored; env-path setting; never logged |
| Retiring Streamlit breaks the docs or demo | Confusion | One PR that removes the code and updates all docs references |
