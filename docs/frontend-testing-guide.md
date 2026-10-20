# Frontend Test Flow

One linear pass that exercises every feature. ~5 min. Updated 2026-08-31.

## Start (3 terminals)

```bash
docker compose up -d                                          # Qdrant :6338
cd backend  && python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                                    # :5173
```

Gate: `curl http://localhost:8000/api/health` → `"status":"ok"`, `"google_client_id_loaded":true`, `"qdrant":{"status":"green"}`.
If `google_client_id_loaded` is `false`, fix `backend/.env` and **restart uvicorn** — login will fail otherwise.

> Open **`http://localhost:5173`** exactly. Not `127.0.0.1` — Google matches the origin string literally.

---

## The flow

| # | Do this | Expect |
|---|---|---|
| 1 | Open `/` | Landing page. Black bg, 3D neon graph, headline "Specification drift, made visible." Stat strip `768 / 0.71 / 1.00` |
| 2 | Move the mouse over the hero | Graph tilts and follows the cursor |
| 3 | Scroll slowly to the bottom | Graph morphs cloud → matrix and dims; every heading animates in word-by-word; PR card morphs into the AI diagnosis card; the 4 result numbers count up |
| 4 | Click nav **Problem / How it works / Results** | Smooth inertia scroll, heading lands clear of the nav bar |
| 5 | Visit `/nonsense` | Redirects to `/` |
| 6 | Click **Sign in** → Google | Lands on `/dashboard`, sidebar shows your name + avatar |
| 7 | Press F5 | Still logged in (no bounce to `/login`) |
| 8 | **+ New Project** → name `Demo` → Create | Toast "Project created!", navigates to `/projects/{id}` |
| 9 | Click **Run Analysis** | **First run 30–60 s** (model loads). Success toast |
| 10 | Read the 4 metric cards | `100.0%` · `10` · `20` · `18` |
| 11 | Toggle **3D Graph / Heatmap / Split** | Graph: 30 nodes. Heatmap: 10×20 grid, hover shows `Req / Endpoint / Score`. Split: both |
| 12 | Click **↻** | Reloads instantly — no re-run, no wait |
| 13 | Sidebar → **Drift Analysis** | KPIs `100.0%` · `100.0%` · `2` · `0`, timeline chart, 2 drift cards |
| 14 | Click the **REQ-09** card | LLM root-cause text appears (template text if no `OPENAI_API_KEY` — that is not a failure) |
| 15 | Sidebar → **PR Bot** → scenario 1 → Run | 🟢 **PR Approved**, 0 violations, markdown says `PASS ✅` |
| 16 | Scenario 2 → Run | 🔴 **PR Blocked**, 1 violation `REQ-04` (Minor Drift), Δ ≈ `-0.069` |
| 17 | Scenario 3 → Run | 🔴 **PR Blocked**, 1 violation `REQ-09` (Minor Drift), Δ ≈ `-0.172` |
| 18 | Sidebar → **Benchmarks** | Cards `0.62 / 1.00 / 0.71 / 1.00`; blue bars clearly above grey; 10-row table |
| 19 | Dashboard → card → **Upload**, drop the three files from `data/` (see below), Upload | Each slot shows a green message (`10 requirements extracted` for the SRS), then the **Review extracted requirements** step lists REQ-01…REQ-10 |
| 20 | Try uploading a `.exe` or `.zip` as SRS | Rejected by the dropzone ("use .docx, .pdf, .md, .txt") |
| 21 | Delete the project (trash icon → confirm) | Card gone; `/api/health` `vectors_count` drops by ~50 |
| 22 | Sign out (sidebar, bottom) | Back at `/login`, `localStorage` empty |

Step 19 files: `data/requirements/ecommerce_srs.md` → SRS · `data/api_specs/store_v1.json` → API_V1 · `data/api_specs/store_v2.json` → API_V2.

### Formal SRS documents (.docx / .pdf)

Fixtures are in `tests/fixtures/srs/` (regenerate with `python scripts/build_srs_fixtures.py --pdf`).
Use a fresh project per row, or re-upload over the previous SRS.

| # | Upload as SRS | Expect |
|---|---|---|
| D1 | `ecommerce_srs_ieee.docx` | Review step: **10** requirements · chips `Word`, `6 from requirement tables`, `4 from tagged headings / paragraphs`. "Boilerplate sections removed: Revision History · 1.2 Document Conventions · 1.3 Definitions… · 1.4 References · Appendix A: Glossary". REQ-01 → Location `table 3 / row 2`, priority `High` |
| D2 | Then **Run Analysis** with `store_v1.json` / `store_v2.json` | Exactly the same numbers as the `.md` run (Expected values below) — same requirement text, same scores |
| D3 | `ecommerce_srs_ieee.pdf` | 10 requirements, chip `PDF`; noise line mentions running headers/footers and "tables joined across pages" |
| D4 | `messy_srs.docx` | 12 requirements (FR1 … FR-7, NFR-1, NFR-2 with an `NFR` chip), 1 warning "Duplicate ID FR-2 … ignored" |
| D5 | `untagged_srs.docx` | 3 requirements with `generated ID` chips (`SRS-AUTO-001…`) and a warning to add IDs |
| D6 | `scanned.pdf` | Red message under the slot: "This PDF has no text layer (it looks scanned)…" — nothing saved |
| D7 | A Word file with no requirements (any letter / notes) | Red message "No requirements were found in '…'. The parser looks for …" |
| D8 | A `.txt` renamed to `.docx` | "This is not a valid .docx file…" |
| D9 | Project page → **SRS** button (header) | Drawer with the same preview; for a project with no upload it says "built-in demo SRS" |

---

## Expected values (built-in demo data, threshold 0.55)

| Where | Value |
|---|---|
| Compliance / reqs / endpoints / compliant pairs | `100%` · `10` · `20` · `18` |
| 3D graph | 30 nodes, ~67 edges |
| Drift pairs | `REQ-09` Δ `-0.111`, `REQ-03` Δ `-0.109` — both *Minor Drift*, 0 critical |
| PR Bot | approve / block REQ-04 / block REQ-09 |
| Benchmarks (τ = 0.40) | Transformer `0.62 / 1.00 / 0.71 / 1.00` vs TF-IDF `0.50 / 0.28 / 0.34 / 1.00` |

±0.02 across machines; verdicts and ordering are stable.
**REQ-04 does not appear on the Drift page** — its Δ (−0.061) is below the −0.10 matrix threshold. Only the PR Bot's tiered rule catches it. Expected; see `validation_results.md` §4.

---

## Must-know

- **First `Run Analysis` can exceed the 60 s axios timeout** and show an error while the backend still finishes. Wait ~30 s, hit **↻** — the matrix is there.
- **Order is fixed:** Traceability → Drift → PR Bot → Benchmarks. The last three 404 until an analysis exists.
- **No upload needed** for steps 9–18; the built-in `data/` set is used automatically.
- **Qdrant down is fine** — the pipeline falls back to NumPy. Worth testing once with `docker compose stop`.

## If it breaks

| Symptom | Fix |
|---|---|
| Google button won't load / `403 origin_mismatch` | Add `http://localhost:5173` to **Authorised JavaScript origins** in Google Cloud Console |
| Login succeeds → bounces to `/login` | `GOOGLE_CLIENT_ID` (backend) ≠ `VITE_GOOGLE_CLIENT_ID` (frontend), or your email isn't an OAuth **Test user**. The toast now shows the real reason |
| `expected one of ['']` | Backend didn't load `backend/.env`. Restart uvicorn; check `/api/health` |
| Blank page / stale styles | `rm -rf frontend/node_modules/.vite && npm run dev` |
| LLM text says "unavailable" | Set `OPENAI_API_KEY` in `backend/.env` (primary) or `GROQ_API_KEY` (fallback) |
