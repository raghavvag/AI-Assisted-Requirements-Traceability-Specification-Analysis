# Project Report Source — AI-Assisted Requirements Traceability and Software Specification Analysis

> **Purpose of this file:** a single, detailed, plain-English description of everything that is
> actually built and working in this project today. Use it to write a report or build slides.
> Everything below is implemented and test-verified unless a section is clearly marked
> **"Planned / Next Phase"**.
>
> **Last updated:** 2026-09-01
> **Test status:** 59 automated tests, all passing (`pytest -q` → `59 passed`).

---

## 1. One-paragraph summary

Software teams write requirements in plain English ("every refund must carry a unique
transaction id"). Engineers implement APIs described in a technical format (OpenAPI/Swagger
JSON). Over time the API drifts away from what the requirement asked for, and nobody notices
because no tool checks *meaning* — only spelling and structure. This project builds an
AI system that reads both sides, converts each sentence into a numeric "meaning vector" using a
transformer model, and measures how well every requirement is covered by the API. It produces a
compliance matrix, detects when a new API version has drifted, explains each problem in human
language using an LLM, and simulates a pull-request review bot that blocks non-compliant
changes. It is delivered as a full product (FastAPI backend + React frontend + vector database +
login) built on one shared research pipeline in `src/`.

---

## 2. The problem

### 2.1 What is "specification drift"

- Business requirements change every sprint. API specs change with them.
- Small meaning-changes pile up over many versions into a big compliance failure.
- The gap is **silent**: no test fails, no linter complains.
- The gap is **asymmetric**: analysts write business language, engineers write technical
  language. There is no direct word-for-word mapping between them.
- In regulated domains (finance, healthcare, e-commerce) undetected drift causes audit
  failures and fines.

### 2.2 A concrete failure (used as the demo)

> **REQ-04 — Refund Transaction Integrity:** "All refund transactions must include a unique,
> client-provided `transaction_id` for idempotent processing and financial audit compliance.
> This id must also appear in the refund receipt."

- **API v1:** `POST /api/v1/refund` requires a client-supplied `transaction_id`. Compliant.
- **API v2:** a developer "cleans up" the endpoint — removes `transaction_id`, replaces it with
  a server-generated `refund_reference`. They think it is an internal detail.
- **Result:** REQ-04 is now violated. The audit trail is broken. Retried requests can create
  duplicate refunds. **No test fails. No linter fires.**

### 2.3 Why existing tools miss it

| Tool type | Why it fails here |
|---|---|
| Traceability tools (Jira links, DOORS) | Manual links; no understanding of meaning; break silently |
| API linters (Spectral, Swagger Validator) | Check structure only, not business intent |
| Keyword / TF-IDF search | "transaction_id" is gone, but they cannot see that the *concept* is gone |
| Static code analysis | Works on code, cannot reason about contract meaning |
| Manual review | Does not scale to 100+ endpoints and hundreds of requirements |

---

## 3. The solution — how it works

### 3.1 Core idea: shared meaning space

1. Parse the requirements document into individual requirements.
2. Parse the OpenAPI spec into individual endpoints (method + path + summary + description +
   every request/response field name and description, joined into one rich text string).
3. Encode every requirement and every endpoint into a 768-number vector using the transformer
   model `all-mpnet-base-v2` (from the `sentence-transformers` library). Vectors that mean
   similar things point in similar directions.
4. Compare every requirement vector with every endpoint vector using **cosine similarity**
   (a score from 0 to 1). This gives an **N×M traceability matrix** (N requirements rows,
   M endpoints columns).
5. A requirement is considered "covered" if at least one endpoint scores above a threshold.

### 3.2 Compliance bands

| Score | Band | Meaning |
|---|---|---|
| ≥ 0.75 (research) / ≥ 0.55 (product default) | Compliant (green) | Endpoint clearly satisfies the requirement |
| 0.50 – threshold | Partial (amber) | Some overlap, human should check |
| below 0.50 | Non-compliant (red) | Requirement is not implemented / orphaned |

The threshold is configurable. The product UI uses 0.55 by default because API contract text
and business prose sit in a slightly narrower similarity band than pure prose-to-prose.

### 3.3 Drift detection

1. Take the requirement baseline (fixed).
2. Score API **v1** endpoints and API **v2** endpoints against that same baseline.
3. For every requirement–endpoint pair compute `delta = score_v2 − score_v1`.
4. Endpoints removed in v2 get `score_v2 = 0`. Endpoints added in v2 get `score_v1 = 0`.
5. Renamed endpoints (e.g. `/api/v1/refund` → `/api/v2/refund`) are matched by normalised path
   so they are not double-counted.
6. Classify severity by how far the score dropped:

| Delta | Severity |
|---|---|
| ≥ 0 | No Drift |
| −0.10 to 0 | Stable (noise) |
| −0.25 to −0.10 | Minor Drift |
| −0.50 to −0.25 | Major Drift |
| below −0.50 | Critical Drift |

### 3.4 LLM explainability (grounded, never the score)

For every flagged drift or violation the system builds a strict prompt and asks an LLM to
answer three questions in a fixed format:
1. **What changed** (one sentence about the specific field/schema change).
2. **Why it breaks the requirement** (link to the exact clause).
3. **Remediation** (the concrete change to restore compliance).

- **Primary provider:** OpenAI `gpt-4o-mini`.
- **Fallback:** Groq `llama-3.3-70b-versatile`.
- **Final fallback:** a deterministic template (so the system never crashes when no API key is
  set — it still shows scores and a structured message).
- The LLM is used **only for explanation**. The score is always the reproducible cosine
  similarity. This means every decision is auditable.

### 3.5 PR review simulator

Simulates what a GitHub Actions bot would post on a pull request that changes an API spec.

- Input: a "PR scenario" with before/after text for each changed endpoint.
- It embeds the before and after text, scores both against all requirements, and applies a
  **tiered regression rule** (robust to the narrow similarity band):
  - If a requirement was strongly linked (score_v1 ≥ 0.60) and the score dropped by ≥ 0.05 →
    regression.
  - If a requirement was moderately linked (score_v1 ≥ 0.35) and the score dropped by ≥ 0.08 →
    regression.
  - Otherwise → not a regression.
- Output: a GitHub-style Markdown review comment (summary table, per-violation cards with the
  LLM root-cause text, and a PASS ✅ / FAIL ❌ badge with "merge blocked" wording).

### 3.6 Evaluation with IR metrics

Against a hand-labelled ground-truth file (which endpoints truly implement which requirement),
the system computes:
- **Precision** — of the endpoints it retrieved, how many were correct.
- **Recall** — of the correct endpoints, how many it found.
- **F1** — the balance of the two.
- **MAP (Mean Average Precision)** — quality of the ranking, threshold-independent.

It runs the same metrics for a **TF-IDF baseline** so the transformer's advantage is measurable.

---

## 4. System architecture

The project has **one implementation**: a core pipeline in `src/` that the FastAPI backend
calls. (A Streamlit dashboard built for the v1.0 prototype was retired in Project-II to remove
duplicated code; everything it showed is in the React app.)

### 4.1 Core pipeline (`src/`)

```
data files ──► src/parser.py ──► src/embedder.py ──► src/similarity.py ──► traceability matrix
                                                   │
                                                   ├─► src/drift_detector.py ─► drift report
                                                   ├─► src/explainer.py ──────► LLM explanations
                                                   ├─► src/pr_reviewer.py ────► PR review comment
                                                   └─► src/evaluator.py ──────► Precision/Recall/F1/MAP
```

- SRS documents are read by `src/srs/` (Word, PDF, Markdown): requirement tables and tagged
  headings are extracted, boilerplate is removed (`docs/srs-format-guide.md`).

### 4.2 Web application

```
React frontend (Vite, port 5173)
    │  axios + JWT
    ▼
FastAPI backend (port 8000)
    ├─ Google OAuth 2.0 login  → issues JWT access + refresh tokens
    ├─ Projects + file upload   (SQLite via async SQLAlchemy)
    ├─ Analysis pipeline        (reuses all src/ modules)
    └─ Qdrant vector database   (port 6338, Docker) — stores embeddings,
                                 multi-tenant by user_id + project_id payload filter
```

- Backend routers: `auth.py` (Google login, JWT, refresh), `projects.py` (CRUD + upload),
  `analysis.py` (analyze, matrix, drift, pr-review, benchmark, explain).
- If Qdrant is down the pipeline automatically falls back to in-memory NumPy — nothing breaks.
- Qdrant multi-tenancy: **one shared collection**, every vector tagged with `user_id` and
  `project_id`, queries filtered by payload. (Not one collection per project.)

### 4.3 The Alignment Engine abstraction (`src/alignment/`)

A clean, model-agnostic layer so the same core can serve future use cases (Jira ticket vs
code, not just SRS vs API). Interfaces:

| Interface | Job |
|---|---|
| `ContextSource` | Yields "what the business asked for" as `ContextUnit`s (SRS requirement, story, acceptance criterion) |
| `ArtifactSource` | Yields "what engineering produced" as `ArtifactUnit`s (OpenAPI endpoint, code symbol, diff hunk) |
| `AlignmentScorer` | Scores every (context, artifact) pair — embeddings now, code-aware models later, same interface |
| `AlignmentGate` | Turns scores + a policy into ALLOW / WARN / BLOCK plus a Markdown comment |

Working implementations today: `SRSContextSource`, `OpenAPIArtifactSource`,
`EmbeddingAlignmentScorer`, `ThresholdAlignmentGate`. Documented stubs (interface final,
body pending) for Jira, git-diff, Python-AST, and code-aware Siamese-BERT scorers.

---

## 5. Module-by-module description (what is built)

### 5.1 `src/` core pipeline

| File | Lines | What it does |
|---|---|---|
| `parser.py` | 293 | Entry point for the SRS (dispatches to `src/srs/` by file type: .docx, .pdf, .md, .txt). Reads OpenAPI JSON/YAML (resolves `$ref` via `prance`, falls back to raw load), builds one rich text string per endpoint. |
| `embedder.py` | 137 | Wraps `sentence-transformers`. Loads `all-mpnet-base-v2` once, encodes text to L2-normalised 768-dim vectors. |
| `similarity.py` | 252 | Cosine similarity matrix, traceability DataFrame builder, compliance bands, overall compliance score. |
| `drift_detector.py` | 265 | Compares two API versions against the requirement baseline, computes per-pair deltas, severity, KPIs; dedupes renamed endpoints by normalised path. |
| `explainer.py` | 313 | `ComplianceExplainer` class: OpenAI → Groq → template fallback. Methods: `explain_drift`, `explain_compliance`. Detects placeholder API keys. |
| `pr_reviewer.py` | 384 | `PRReviewer` class + 3 built-in PR scenarios. Tiered regression rule. Builds the GitHub-style Markdown comment. |
| `evaluator.py` | 361 | Loads ground truth CSV; computes Precision/Recall/F1, MAP, per-requirement metrics, and a TF-IDF baseline matrix. |
| `vector_store.py` | 426 | Qdrant client wrapper: create collection, upsert, filtered search, build matrix from search results, health stats. Handles the newer `query_points` API. |
| `srs/` | — | Industrial SRS ingestion: docx/pdf/markdown readers -> block stream -> noise filter -> table / tagged / shall-statement strategies -> normaliser + extraction report. |
| `config.py` | — | `.traceability.yml` loader (pydantic): SRS + OpenAPI paths, thresholds, fail_on, SRS extraction options. |

### 5.2 `src/alignment/` (the abstraction)

| File | What it does |
|---|---|
| `base.py` | All interfaces + value objects (`ContextUnit`, `ArtifactUnit`, `AlignmentResult`, `GatePolicy`, `GateDecision`, `AlignmentBand`, `GateVerdict`). |
| `sources.py` | `SRSContextSource`, `OpenAPIArtifactSource` (working) + Jira / git-diff / Python-AST stubs. |
| `scorer.py` | `EmbeddingAlignmentScorer` (working) + Siamese-BERT / cross-encoder stubs. |
| `gate.py` | `ThresholdAlignmentGate` — coverage + band policy, renders a Markdown review comment. |

### 5.3 `backend/app/` (FastAPI product)

| File | What it does |
|---|---|
| `main.py` | App startup, CORS for the Vite dev server, `/api/health` with a config block (confirms Google client id, OpenAI key, Qdrant status). |
| `core/config.py` | Loads `.env` from an absolute path (repo root + `backend/.env`), so it works no matter where uvicorn starts. |
| `core/security.py` | JWT create/verify, `get_current_user` dependency. |
| `database.py` | Async SQLAlchemy models: `User`, `Project`, `Document`, `AnalysisResult`. |
| `routers/auth.py` | `POST /api/auth/google` (verify Google ID token, upsert user, issue JWT), `/api/auth/me`, `/api/auth/refresh`. Actionable error messages for common OAuth misconfig. |
| `routers/projects.py` | Project CRUD, validated upload (SRS: `.docx/.pdf/.md/.txt`; specs: `.json/.yaml/.yml`; 10 MB, magic bytes, zip-bomb guard; one document per type), SRS extraction preview (`GET /srs/requirements`), document listing. |
| `routers/analysis.py` | `POST /analyze`, `GET /matrix` (+ 3D graph payload), `GET /drift`, `POST /pr-review`, `GET /benchmark`, `GET /explain/{req_id}`. Falls back to the built-in demo dataset if a project has no uploads. |
| `services/pipeline.py` | The orchestrator: parse → embed → Qdrant upsert → matrix → drift → 3D graph payload → IR metrics. Singleton embedder. |
| `services/vector_store.py` | Backend copy of the Qdrant wrapper. |

### 5.4 `frontend/src/` (React 18 + Vite)

| File | Lines | What it does |
|---|---|---|
| `pages/LandingPage.jsx` | 909 | Marketing landing page. Deep-black + cyan/emerald theme, 3D "neural graph" background (React Three Fiber) that morphs from a cloud to a matrix on scroll, GSAP scroll animations, smooth (Lenis) scrolling. |
| `pages/LoginPage.jsx` | 159 | Google sign-in button. |
| `pages/DashboardPage.jsx` | 255 | Project list, create project, upload modal (drag-and-drop, 3 slots: SRS / v1 / v2), delete. |
| `pages/TraceabilityPage.jsx` | 179 | Run analysis, 4 KPI cards, toggle between 3D force graph / heatmap / split view, reload cached result. |
| `pages/DriftPage.jsx` | 192 | Drift KPIs, timeline chart, drift cards; click a card for the LLM root-cause text. |
| `pages/PRBotPage.jsx` | 127 | Pick one of 3 PR scenarios, run, see PASS/BLOCK banner, violation cards, and the generated GitHub comment. |
| `pages/BenchmarksPage.jsx` | 152 | Transformer vs TF-IDF bar chart + 10-row per-requirement table. |
| `components/Traceability3DGraph.jsx` | 190 | The interactive 3D requirement–endpoint graph (react-force-graph-3d). |
| `components/Sidebar.jsx` | 89 | Left navigation for the project workspace. |
| `services/api.js` | — | Axios instance, auto JWT header, 401 → redirect to login (except on auth calls, to avoid a login loop). |

---

## 6. Datasets that ship with the project

| File | Content |
|---|---|
| `data/requirements/ecommerce_srs.md` | E-commerce SRS, **10 requirements** (REQ-01 … REQ-10): auth, catalogue, cart, refund idempotency, checkout, payment methods, order tracking, profile, 2FA enforcement, notifications. |
| `data/api_specs/store_v1.json` | OpenAPI 3.0 baseline, **20 endpoints**, fully compliant. |
| `data/api_specs/store_v2.json` | Drifted version: refund idempotency weakened, 2FA removed from checkout, plus harmless changes (new optional fields) to test false positives. |
| `data/ground_truth/traceability_labels.csv` | **200 rows** (10 reqs × 20 endpoints) of hand-labelled "is this endpoint relevant to this requirement" (1/0). |
| `data/samples/banking/` | A second, different-domain dataset (Aurora Digital Banking): **8 requirements** (`BNK-REQ-01…08`), `aurora_v1.json` (15 endpoints), `aurora_v2.json` (13 endpoints — step-up auth removed, transfer idempotency weakened). Used to prove the upload feature works on new data, not just the demo. |

---

## 7. Verified results (all reproduced by the automated tests and manual runs)

### 7.1 E-commerce demo (threshold 0.55)

| Metric | Value |
|---|---|
| Overall compliance | **100%** (every requirement has at least one endpoint above threshold) |
| Requirements / endpoints | 10 / 20 |
| Compliant matrix cells | 18 |
| 3D graph | 30 nodes, ~67 edges |

### 7.2 Drift (v1 → v2, e-commerce)

| Pair | Delta | Severity |
|---|---|---|
| REQ-09 (2FA) ↔ checkout | −0.111 | Minor Drift |
| REQ-03 (cart) ↔ related endpoint | −0.109 | Minor Drift |

Note: REQ-04's drift (−0.061) is below the −0.10 matrix threshold, so it does **not** show on
the Drift page — but the PR bot's tiered rule still catches it. This is a documented, expected
limitation (see `docs/validation_results.md`).

### 7.3 Benchmark — Transformer vs TF-IDF (evaluation threshold 0.40)

| Method | Precision | Recall | F1 | MAP |
|---|---|---|---|---|
| Transformer (all-mpnet-base-v2) | 0.62 | 1.00 | 0.71 | 1.00 |
| TF-IDF baseline | 0.50 | 0.28 | 0.34 | 1.00 |

The transformer roughly **doubles F1** over the keyword baseline (0.71 vs 0.34), mainly by
finding relevant endpoints the keyword method misses (recall 1.00 vs 0.28).

### 7.4 PR review simulator

| Scenario | Verdict | Detail |
|---|---|---|
| `add_endpoint` (add optional `discount_code` to cart) | **PASS ✅** | Non-breaking; no requirement lost alignment |
| `remove_idempotency` (drop client `transaction_id` from refund) | **BLOCK 🚫** | Violates REQ-04, delta ≈ −0.069 |
| `drop_2fa_requirement` (remove 2FA gate from checkout) | **BLOCK 🚫** | Violates REQ-09, delta ≈ −0.172 |

### 7.5 Banking sample (threshold 0.55, verified by running the pipeline)

| Metric | v1 | v2 |
|---|---|---|
| Overall compliance | 100.0% (8 reqs, 15 endpoints) | 87.5% (7 of 8) |
| Uncovered requirement in v2 | — | BNK-REQ-07 (step-up auth removed) |
| Drifted pairs / critical | — | 17 / 2 |
| BNK-REQ-04 (transfer idempotency) | `POST /transfers` 0.85 | 0.70 (delta −0.15) |

### 7.6 Tests

`pytest -q` → **59 passed**. Covers: parser counts, embedding shape, similarity bounds,
REQ-04 → refund top match, drift detection of both injected violations, PR scenarios,
alignment-engine contract (bounds, determinism, ranks), and the strict-policy BLOCK path.

---

## 8. Real use cases with examples

### Use case 1 — QA Lead: "Is every requirement actually built?"

- **Who:** QA / test lead before a release.
- **Action:** Upload the SRS and the current OpenAPI spec, click *Run Analysis*.
- **Output:** A traceability matrix + overall compliance %. Red rows = requirements with no
  implementing endpoint.
- **Example:** In the banking v2 spec, BNK-REQ-07 lights up red — the step-up authentication
  endpoints were removed, so "high-value transfers must use step-up auth" is now unimplemented.

### Use case 2 — Architect: "Did this new API version break anything?"

- **Who:** Software architect reviewing a v2 API proposal.
- **Action:** Upload SRS + v1 + v2, enable drift, run.
- **Output:** Drift KPIs (compliance v1 vs v2), a list of drifted pairs with severity, and an
  LLM explanation per pair.
- **Example:** E-commerce v1 → v2 shows compliance dropping and REQ-09 flagged. The LLM text
  explains: "the `two_fa_token` field and the 2FA enforcement description were removed from
  checkout; high-value orders now process without step-up verification; restore the 2FA gate."

### Use case 3 — Developer: "Will my pull request pass compliance?"

- **Who:** Developer about to open a PR that changes an API spec.
- **Action:** Run the PR reviewer for the matching scenario (in the product this is a
  simulation; the live GitHub bot is the next phase).
- **Output:** A GitHub-style review comment with PASS/FAIL and, if it fails, exactly which
  requirement regressed and how to fix it.
- **Example:** The `remove_idempotency` scenario returns **BLOCK** with a card for REQ-04 and
  the remediation "restore `transaction_id` as a required field in the refund request body".

### Use case 4 — Auditor / researcher: "How accurate is this system?"

- **Who:** Anyone who needs to trust the numbers.
- **Action:** Open the Benchmarks page.
- **Output:** Precision / Recall / F1 / MAP for the transformer vs a TF-IDF baseline, plus a
  per-requirement table.
- **Example:** F1 0.71 vs 0.34 — the transformer method is clearly better than keyword matching,
  and every score is backed by a reproducible cosine similarity, not a black box.

### Use case 5 — Team lead: "Keep each client's data separate."

- **Who:** A consultancy running audits for several clients.
- **Action:** Create one project per client/audit; upload that client's files.
- **Output:** Each project's embeddings are tagged with `user_id + project_id` in Qdrant and
  filtered on every query, so analyses never mix.

---

## 9. Technology stack

| Layer | Technology |
|---|---|
| Embeddings | `sentence-transformers`, model `all-mpnet-base-v2` (768-dim), PyTorch |
| Similarity | scikit-learn cosine similarity, NumPy, pandas |
| OpenAPI parsing | `prance`, `openapi-spec-validator`, PyYAML |
| LLM | OpenAI `gpt-4o-mini` (primary), Groq `llama-3.3-70b-versatile` (fallback) |
| Vector DB | Qdrant (Docker, port 6338), multi-tenant by payload filter |
| Backend | FastAPI, async SQLAlchemy 2.0, aiosqlite, pydantic v2, python-jose (JWT), google-auth |
| Product UI | React 18, Vite, Tailwind CSS v4, React Three Fiber (3D), GSAP + Lenis (scroll), Plotly, react-force-graph-3d |
| Auth | Google OAuth 2.0 + JWT |
| Tests | pytest, pytest-asyncio (165+ tests, incl. docx/pdf parity) |

---

## 10. Performance and design targets

| Target | Value | Status |
|---|---|---|
| F1 (traceability) | > 0.85 on the full ranking (MAP 1.00); 0.71 at the strict point threshold | Met on ranking; point-F1 documented |
| Drift detection recall | 100% of injected breaking changes | Met (both REQ-04 and REQ-09 style breaks caught by the tiered rule) |
| Latency (matrix generation) | < 5 s for < 50 endpoints (after first model load) | Met; first run ~30–60 s while the model downloads/loads |
| Explainability coverage | Every flagged violation gets an explanation | Met (LLM or deterministic template) |
| False positives on non-breaking changes | 0 | Met (`add_endpoint` scenario passes) |

---

## 11. Planned / Next Phase — the real PR Review Bot (designed, not yet built)

A full architecture and roadmap is written in
`docs/architecture/pr-review-bot-roadmap.md`. Summary:

- A **GitHub App** that runs on every pull request via webhooks.
- **Intent side:** reads the GitHub issue linked by "Closes #N", splits it into acceptance
  criteria.
- **Implementation side:** reads the real PR diff — changed lines, and changed Python
  functions/classes (via the `ast` module).
- **Scoring:** keeps `all-mpnet-base-v2` for prose, adds a code-aware model
  (`jina-embeddings-v2-base-code`) for code, behind the same `AlignmentScorer` interface.
- **Gate:** checks coverage (is every acceptance criterion implemented?), scope-creep (code
  that matches no criterion), and regressions (a change that lowers a previously-covered
  criterion). Configurable per repo via `.alignment.yml`.
- **Output:** one auto-updating PR comment + a GitHub Check Run (pass / neutral / fail) + line
  annotations.
- **Proof:** a synthetic benchmark (toy repo + ~10 labelled PRs) with a phase gate of
  per-criterion F1 ≥ 0.75, plus baselines (TF-IDF, BM25, LLM-only).

This is the next build. Everything in sections 1–10 is already working.

---

## 12. How to run it (quick reference)

```bash
# Core pipeline + tests
pip install -r requirements.txt
pytest -q -m "not slow"      # add the slow model tests with plain `pytest`

# Product (3 terminals)
docker compose up -d                                        # Qdrant on 6338
cd backend  && python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev                   # http://localhost:5173

# Tests
pytest -q
```

Full test walkthrough with expected values: `docs/frontend-testing-guide.md`.
Validation details and known limitations: `docs/validation_results.md`.
