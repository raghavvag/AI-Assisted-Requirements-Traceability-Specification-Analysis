# Manual Steps

These are things only you can do: they need your accounts, your machine, a human check, or a decision.
The task list (what gets built) is in [`docs/leftover.txt`](docs/leftover.txt). This file lists what *you* need to do.

**Last updated:** 2026-10-16, M3 T3.5–T3.9 done (live review flow).
**How to use it:** tick a box when it's done. Items are in the order you should do them. A ⚠️ means it blocks later work.

---

## A. Do now

### A1. ⚠️ Commit the work
`git log` still shows only the initial commit. Everything from M1 and M2 is uncommitted.

```powershell
git add .gitignore
git add frontend/src/components/Traceability3DGraph.jsx frontend/src/pages/BenchmarksPage.jsx frontend/src/pages/DriftPage.jsx frontend/src/pages/PRBotPage.jsx frontend/src/components/ComplianceHeatmap.jsx frontend/src/components/ui.jsx
git commit -m "feat(frontend): shared UI primitives, compliance heatmap and redesigned analysis pages" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"

git add -A src backend frontend/src app scripts tests pytest.ini requirements.txt .traceability.yml manual.md docs/plan.md docs/leftover.txt docs/srs-format-guide.md docs/frontend-testing-guide.md docs/PITCH.md docs/PROJECT_REPORT_SOURCE.md docs/validation_results.md docs/architecture knowledge.md roadmap.md startup_guide.md
git commit -m "feat: industrial SRS ingestion (M1) and shared review engine + CLI (M2)" -m "M1: .docx/.pdf/.md SRS ingestion (src/srs), validated uploads, SRS preview API and UI, golden fixtures and F1/MAP parity tests." -m "M2: retire Streamlit; extract_endpoints_from_spec; .traceability.yml config (src/config.py); review engine src/review (spec diff, tiered regression rule with trace-link scoping, PASS/WARN/BLOCK, markdown/JSON report, intent check); PR scenarios as OpenAPI mini-specs; python -m src.cli (extract-srs, analyze, review; exit 0/1/2)." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] Both commits made (`git status` is clean, apart from the files in A2)

### A2. Decide what stays out of git
- [ ] Add `docs/PITCH.html`, `docs/PITCH.jpeg`, `docs/PITCH.pdf` and `docs/PITCH.png` to `.gitignore` (you said you would).
- [ ] Decide about `docs/Software_Specification_Analysis_BCSE497J Project I Report (1).docx`. It contains student names and register numbers, so commit it only if the repo will stay private.

### A3. Clean-up I could not do
- [ ] Delete `D:\tmp_claude`. It's a temp folder I used while C: was full, and it now also holds caches from Word and V8. Close Word first, then run `Remove-Item -Recurse -Force D:\tmp_claude`.
- [ ] Optional: delete the root `app.db`. It's the retired Streamlit app's database and is gitignored.

### A4. ⚠️ Keep disk space free
C: was down to 520 KB, and that broke `pip`. The model cache (`~/.cache/huggingface`, about 420 MB) and pip both write to C:.
- [ ] Keep at least **5 GB free on C:**. Run `pip cache purge` when space is low; it's safe and frees about 2 GB.

### A5. Python environment
There is no virtualenv. Packages are in your user site-packages, which is how `huggingface_hub` 2.x got in and broke `transformers`.
- [ ] Recommended: create one venv for the project.
  ```powershell
  python -m venv .venv; .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt -r backend/requirements.txt
  ```
- [ ] If anything upgrades `huggingface_hub` to 1.x or later again, run `pip install "huggingface_hub>=0.30,<1.0"`. Both requirements files now pin it.

### A6. Security
- [ ] **Rotate the OpenAI API key** that was once in `.env.example` (found in Project I). The templates are clean now, but the old key should be treated as leaked: create a new key, put it in `backend/.env`, and revoke the old one.

---

## B. Checks that need a person

### B1. Click through the UI with a real Google login
I tested every API call through the Vite proxy with a minted token. I couldn't click through the screens, because no browser tool was available.
- [ ] Follow `docs/frontend-testing-guide.md`: the main flow (steps 1–22) and **D1–D9** (Word/PDF upload, the review step, the SRS drawer).
- [ ] The PR Bot numbers have changed. Expect REQ-04 Δ ≈ **−0.069** and REQ-09 Δ ≈ **−0.172** (they were −0.061 and −0.123).

### B2. T1.24: real-world SRS documents
- [ ] Find **2 or more public IEEE-830-style SRS `.docx` files** (university or project templates).
- [ ] Run `python -m src.cli extract-srs <file>` on each, then check:
  - no boilerplate (TOC, revision history, glossary) appears in the output;
  - at least 95% of the requirements with IDs are found.
- [ ] Note the results (file, number found / total, misses) for the report (task T6.1). If one fails, send it to me with the output.

### B3. Report screenshots (Section 6.4, Figures 6.1–6.9)
`scripts/capture_report_screenshots.js` drives the app with Playwright and writes the 9 PNGs to `demo/`. It recreates a project called **"E-Commerce Platform (Report Demo)"** on every run. The figures were generated on 2026-10-20 and all 9 passed. Re-run it only if the UI changes.
- [x] Install once: `npm install -D playwright; npx playwright install chromium` (done at the repo root; `frontend/` works too)
- [ ] Start the backend and `npm run dev`. In a signed-in browser tab on `http://localhost:5173`, run `copy(localStorage.access_token)` in the DevTools console.
- [ ] `$env:REPORT_ACCESS_TOKEN = "<paste>"; node scripts/capture_report_screenshots.js`. The token is valid for 60 minutes. If you leave it unset, the script runs `scripts/mint_report_token.py`, which signs a token for the first user using `backend/.env`.
- [ ] For a real explanation in Figure 6.7, `OPENAI_API_KEY` or `GROQ_API_KEY` must be set. Without one, the card shows the "unavailable" fallback text.

### B4. Regenerating the PDF fixture (only if you change the fixtures)
`python scripts/build_srs_fixtures.py --pdf` exports the PDF through **Microsoft Word on Windows**. On any other machine, run it without `--pdf` and keep the committed PDF.

---

## C. Facts to put in the Project-II report

- **SRS parity.** The IEEE-830 Word SRS and its PDF give exactly the same requirements, F1 0.71 and MAP 1.00 as the plain-text SRS. This is checked by `tests/test_srs_extraction.py::TestParity` against `tests/fixtures/baseline_metrics.json`.
- **Trace-link scoping (`link_top_k = 3`).** Without it, reviewing the whole store_v1 → v2 change flagged REQ-05, REQ-06 and REQ-07 against the 2FA endpoints. None of those pairs are relevant in the ground truth. With scoping, the gate blocks on **REQ-02, REQ-04 and REQ-09**.
  - REQ-04 and REQ-09 are the dataset's two intentional drifts.
  - REQ-02 is a real weakening: v2 `GET /products/{id}` lost its parameter description and its response field list.
- **REQ-09 caveat.** The real `store_v2` checkout change on its own does not trip the gate (REQ-09 Δ −0.060 from 0.453). The PR scenario (#43) blocks REQ-09 because its spec spells out the 2FA removal, and the full-spec review catches REQ-09 through the changed 2FA endpoints.
- **The LLM never decides.** Verdicts are deterministic (`src/review/engine.py`); the LLM only explains violations that were already found.

---

## D. M3 (live GitHub App): set this up now

The whole bot is built and tested against a mocked GitHub (T3.2–T3.9): webhook, app auth, the review flow
(check run → config → PR files → specs + SRS → linked issue → engine → check result → PR comment → DB row), and the
"Live reviews" tab. D1–D8 were done on 2026-10-16 (ping redelivery returned 200 `pong`).

- [x] **D1. Demo repo.** Create **`traceability-demo-store`** on GitHub (public or private). It can be empty for now; I'll provide the files in T3.10.
- [x] **D2. smee channel.** Open <https://smee.io>, click **Start a new channel**, and copy the URL (`https://smee.io/AbC123…`).
- [x] **D3. Webhook secret.** Generate one: `python -c "import secrets; print(secrets.token_hex(32))"`
- [x] **D4. Register the app.** Go to GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.
  - **Name:** e.g. `traceability-bot` (the URL slug becomes `GITHUB_APP_SLUG`).
  - **Homepage URL:** your repo URL.
  - **Webhook:** Active ✔. **Webhook URL** = the smee URL from D2. **Secret** = the value from D3.
  - **Repository permissions:** Checks **Read & write** · Pull requests **Read & write** · Issues **Read** · Contents **Read** · Metadata **Read** (selected automatically).
  - **Subscribe to events:** ✔ Pull request · ✔ Check run.
  - **Where can this app be installed:** Only on this account.
  - Click **Create**, then note the **App ID** shown at the top of the settings page.
- [x] **D5. Private key.** On the app page, **Generate a private key**. Move the downloaded `.pem` **outside the repo**, e.g. `C:\keys\traceability-bot.pem`. Never commit it. `*.pem` should be gitignored; check with `git check-ignore -v x.pem`.
- [x] **D6. Install** the app (left menu, Install App) on **only** `traceability-demo-store`.
- [x] **D7. Fill in `backend/.env`.** The template is in `backend/.env.example`.
  ```
  GITHUB_APP_ID=<App ID>
  GITHUB_APP_PRIVATE_KEY_PATH=C:\keys\traceability-bot.pem
  GITHUB_WEBHOOK_SECRET=<secret from D3>
  GITHUB_APP_SLUG=traceability-bot
  ```
- [x] **D8. Check that it works.**
  1. Restart the backend: `cd backend; python -m uvicorn app.main:app --reload --port 8000`.
  2. Open <http://localhost:8000/api/health>. `config.github_app_configured` should be `true`.
  3. In a second terminal, run `npx smee-client -u <smee URL> -t http://127.0.0.1:8000/api/github/webhook` (keep it running during demos). Use `127.0.0.1`, not `localhost`: Node resolves `localhost` to IPv6 and the events never reach uvicorn.
  4. On the app page go to **Advanced → Recent Deliveries**, open the `ping` delivery and click **Redeliver**. The response should be **200** with `"status": "pong"`. A **401** means the secret in `.env` differs from the app's secret. A **503** means `.env` wasn't loaded (restart uvicorn).
  5. Open a test PR in the demo repo. The uvicorn log should show `Queued review <your-account>/traceability-demo-store#N`.
- [x] **D9. ⚠️ Restart uvicorn** so it picks up the review flow. The startup log must say
  `GitHub App configured (app id …); webhook at /api/github/webhook`. If it says *"settings are present but unusable"*,
  the private key path or PEM is wrong.
- [x] **D10. First live review.** Done 2026-10-20: issues #1–#3 and PRs #4–#6 in `raghavvag/traceability-demo-store`
  (built by `python scripts/setup_demo_repo.py --verify`). Results: #4 **PASS** (success), #5 **BLOCK REQ-04**,
  #6 **BLOCK REQ-09** (failure), each with one bot comment and a Live reviews row. Two fixes were needed on the way:
  - ⚠️ **Restart your smee-client with `-t http://127.0.0.1:8000/api/github/webhook`.** With `localhost`, Node
    connects to IPv6 `::1`, uvicorn only listens on IPv4, and every event is silently dropped.
  - `backend/.env` key path: the double-quoted `C:\Users\…\traceability-bot…pem` turned `\t` into a tab. I
    changed it to forward slashes.
  On each PR you should see:
  - a **Requirements Traceability** check (in progress, then success / neutral / failure);
  - one bot comment (it is edited, not re-posted, on every push);
  - the run in the app under **PR Bot → Live reviews**.

  The first review on a fresh machine downloads the embedding model (~420 MB), so it can take a few minutes.
  Without `OPENAI_API_KEY` the explanations use the template text. That is fine.
  A PR that does not touch the spec / SRS / config gets a **neutral** "No specification change" check. That is expected.
- [ ] **D11. Branch protection:** in the demo repo go to Settings → Branches → rule for `main` →
  **Require status checks to pass** → add `Requirements Traceability` (it appears in the list after the first run).
  A `failure` conclusion then disables the merge button.
- [ ] **D12. Re-run works:** on a PR's Checks tab click **Re-run**. A fresh check run should appear
  (the uvicorn log shows `check_run.rerequested`).

**If something goes wrong:** the check run's summary says why (bad `.traceability.yml`, SRS not found or unreadable,
invalid spec). A crash also shows up as a failed check with the error. Fix it and click **Re-run** (or **Redeliver** the
delivery in Advanced → Recent Deliveries). Redelivering a delivery that already completed is ignored on purpose.


## E. M4 (CI/CD): live
`.github/workflows/ci.yml`, `.github/workflows/compliance.yml`, `action.yml` and `ruff.toml` were pushed on
2026-10-20 (`08f7638`). The first CI run found a real missing dependency (`sqlalchemy[asyncio]` / greenlet);
fixed in `147dd3d`, after which **CI and Compliance are both green**.
- [x] Commit and push to GitHub with Actions enabled.
- [ ] Optional: add `OPENAI_API_KEY` as a repository secret for LLM explanations in the compliance report.
- [ ] After the first green run, mark **Python (ruff + pytest)**, **Frontend (build)**, **Requirements Traceability
  gate** and **Gate self-test** as **required** on `main` (T4.6), and add a README badge.
- [ ] T4.5 follow-up: once a tag (`v1`) exists, add the action to the demo repo as a second, App-free gate
  (`docs/github-app-setup.md` §7).

## F. M5 (code-aware review): committed
Verified live on the demo repo on 2026-10-20: PR #7 (adds `app/`, PASS, merged); PR #8 drops `transaction_id`
in Python only and is **BLOCKed on REQ-04** with the merge button disabled. A second round fixed two gaps found
while testing REQ-09 (code `link_top_k` 3 -> 5; renames are not removals) and added `app/twofa.py`.
Details: `docs/github-app-setup.md` §8.
- [ ] Keep `#8` (and the REQ-09 PR) open as report evidence; do not merge them.
- [ ] uvicorn now loads two models (mpnet + jina, ~1.3 GB RAM together).
- [ ] Optional: T5.5 evaluation (`data/ground_truth/code_labels.csv`, P/R/F1 per acceptance criterion) for the report.
- [ ] Stop the old smee-client that still targets `localhost` (it silently drops events; the `127.0.0.1` one works).

---

## Cheat sheet

```powershell
pytest -q -m "not slow"                       # fast suite, about 1 minute
pytest -q                                     # everything incl. model tests, about 3 minutes
python -m src.cli review --base-spec data/api_specs/store_v1.json --head-spec data/api_specs/store_v2.json   # exit 1 = blocked
python scripts/snapshot_baseline_metrics.py   # re-record F1/MAP baseline (only if the model or dataset changes)
docker compose up -d                          # Qdrant on 6338 (optional: NumPy fallback otherwise)
cd backend;  python -m uvicorn app.main:app --reload --port 8000
cd frontend; npm run dev                      # http://localhost:5173
```
