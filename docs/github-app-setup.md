# GitHub App setup: Traceability Bot

How to run the live pull-request review bot (Milestone 3) against a real repository. The design is in
`docs/plan.md` §4. The code is in `backend/app/routers/github.py` (webhook) and
`backend/app/services/github/` (auth, API client, review flow).

```
GitHub ──webhook──► smee.io ──► smee-client ──► 127.0.0.1:8000/api/github/webhook
                                                   │ HMAC check, dedupe, 202
                                                   ▼ background job
   check run (in progress) → .traceability.yml → PR files → base/head spec + SRS → linked issue
   → review engine → check run (success / neutral / failure) → PR comment → PRReviewRun row
```

On each pull request that changes the spec, the SRS or `.traceability.yml`, the bot posts:

- a **Requirements Traceability** check run: `success` = PASS, `neutral` = WARN, `failure` = BLOCK;
- **one** PR comment, edited in place on every push (it carries a hidden `<!-- traceability-bot -->` marker);
- a row in the app's **PR Bot → Live reviews** tab.

---

## 1. Register the app

1. Open <https://smee.io>, click **Start a new channel** and copy the URL (`https://smee.io/AbC123…`).
2. Generate a webhook secret:
   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
3. GitHub → **Settings → Developer settings → GitHub Apps → New GitHub App**:

   | Field | Value |
   |---|---|
   | GitHub App name | `traceability-bot` (its URL slug becomes `GITHUB_APP_SLUG`) |
   | Homepage URL | any URL, e.g. this repository |
   | Webhook | **Active** |
   | Webhook URL | the smee channel URL from step 1 |
   | Webhook secret | the secret from step 2 |
   | Where can this app be installed | Only on this account |

4. **Repository permissions** (everything else stays *No access*):

   | Permission | Access | Used for |
   |---|---|---|
   | Checks | **Read & write** | create and complete the *Requirements Traceability* check run |
   | Pull requests | **Read & write** | list the PR's changed files, read the PR body |
   | Issues | **Read** | read the linked issue's acceptance criteria |
   | Contents | **Read** | fetch `.traceability.yml`, the spec and the SRS at the base and head commits |
   | Metadata | **Read** | selected automatically |

   The PR comment is posted through the Issues comments API. **Pull requests: write** covers it on pull requests.

5. **Subscribe to events:** ✔ **Pull request** and ✔ **Check run**.
6. Click **Create GitHub App** and note the **App ID** at the top of the settings page.
7. **Private key:** on the same page, **Generate a private key**. Move the downloaded `.pem` **outside the
   repository** (e.g. `C:\keys\traceability-bot.pem`). `*.pem` is gitignored; confirm with
   `git check-ignore -v x.pem`.
8. **Install App** (left menu) → your account → **Only select repositories** → `traceability-demo-store`.

## 2. Environment variables (`backend/.env`)

The template is `backend/.env.example`. `backend/.env` is gitignored.

| Variable | Required | Value |
|---|---|---|
| `GITHUB_APP_ID` | yes | the App ID from step 6 |
| `GITHUB_APP_PRIVATE_KEY_PATH` | yes* | path to the `.pem`, e.g. `C:/keys/traceability-bot.pem` (forward slashes: in a double-quoted value, `\t` becomes a tab) |
| `GITHUB_APP_PRIVATE_KEY` | yes* | the PEM itself, with `\n` for line breaks (for hosts without files) |
| `GITHUB_WEBHOOK_SECRET` | yes | the secret from step 2 (must match the app exactly) |
| `GITHUB_APP_SLUG` | no | `traceability-bot` |
| `GITHUB_CHECK_NAME` | no | `Requirements Traceability` (the name branch protection requires) |
| `GITHUB_API_URL` | no | only for GitHub Enterprise Server, e.g. `https://ghe.example.com/api/v3` |
| `OPENAI_API_KEY` / `GROQ_API_KEY` | no | LLM explanations in the comment; without one, template text is used |

\* Set one of the two private-key variables, not both.

The LLM never decides the verdict. Verdicts come from the deterministic engine (`src/review/engine.py`); the LLM
only explains violations that were already found.

## 3. Start everything

Three terminals:

```powershell
# 1. Backend
cd backend
python -m uvicorn app.main:app --reload --port 8000

# 2. Webhook tunnel (keep it running during the demo)
# Use 127.0.0.1, not localhost: Node resolves localhost to IPv6 ::1, and uvicorn listens on IPv4 only.
npx smee-client -u https://smee.io/<channel> -t http://127.0.0.1:8000/api/github/webhook

# 3. Frontend (for the Live reviews tab)
cd frontend
npm run dev
```

Checks:

1. The uvicorn startup log says `GitHub App configured (app id …); webhook at /api/github/webhook`. If it says
   *"settings are present but unusable"*, the private key path or PEM is wrong.
2. <http://localhost:8000/api/health> shows `"github_app_configured": true`.
3. On the app page, **Advanced → Recent Deliveries** → the `ping` delivery → **Redeliver**. Expect **200**
   with `"status": "pong"`.

## 4. The demo repository

`python scripts/setup_demo_repo.py --verify` builds `traceability-demo-store` (cloned next to this project if
it is missing):

| Branch | Change to `openapi.yaml` | Linked issue | Expected check |
|---|---|---|---|
| `main` | baseline: `docs/SRS.docx`, `openapi.yaml` (store_v1), `.traceability.yml` | | |
| `add_endpoint` | adds `POST /api/v1/cart/discount` | #1 Support discount codes | **success** (PASS) |
| `remove_idempotency` | refund drops the client `transaction_id` | #2 Simplify refunds | **failure** (BLOCK REQ-04) |
| `drop_2fa_requirement` | checkout drops `two_fa_token` and the 2FA gate | #3 Reduce checkout friction | **failure** (BLOCK REQ-09) |

`--verify` reviews each branch with the real engine before committing and stops if a verdict differs. The
issue and PR texts are in `scripts/demo_repo/`. Create the issues **before** the PRs so they get numbers 1–3,
which the PR bodies reference with `Closes #N`.

Push, then create the issues and PRs (from this project's root, with the GitHub CLI):

```powershell
git -C ..\traceability-demo-store push -u origin main add_endpoint remove_idempotency drop_2fa_requirement

$R = "raghavvag/traceability-demo-store"; $T = "scripts/demo_repo"
gh issue create -R $R -t "Support discount codes in the cart" -F $T/issue_1_discount_codes.md
gh issue create -R $R -t "Simplify the refund API"            -F $T/issue_2_simplify_refunds.md
gh issue create -R $R -t "Reduce checkout friction"           -F $T/issue_3_checkout_friction.md

gh pr create -R $R -B main -H add_endpoint         -t "feat(cart): apply promotional discount codes" -F $T/pr_add_endpoint.md
gh pr create -R $R -B main -H remove_idempotency   -t "refactor(refund): server-generated refund reference" -F $T/pr_remove_idempotency.md
gh pr create -R $R -B main -H drop_2fa_requirement -t "refactor(checkout): remove the 2FA step-up gate" -F $T/pr_drop_2fa_requirement.md
```

## 5. Branch protection (make BLOCK block the merge)

The check name only appears in GitHub's list after it has run once, so open one PR first.

1. Demo repo → **Settings → Branches → Add branch protection rule** (or **Rules → Rulesets → New branch
   ruleset**).
2. Branch name pattern: `main`.
3. ✔ **Require a pull request before merging**.
4. ✔ **Require status checks to pass before merging** → search for and add **Requirements Traceability**.
   With rulesets, choose the **traceability-bot** app as the source of the check.
5. Optional: ✔ **Require branches to be up to date before merging**, and ✔ **Do not allow bypassing the
   above settings** (otherwise admins can still merge).
6. **Save**.

A `failure` conclusion now disables **Merge pull request**. `neutral` (WARN) and `success` (PASS) allow it.
To make WARN block too, set `fail_on` in `.traceability.yml`; `fail_on: never` turns BLOCK into neutral.

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| No uvicorn log line when a PR is opened | smee-client is not running, or points at the wrong URL or port. On Windows the target must be `http://127.0.0.1:8000/...`: with `localhost`, smee-client gets `ECONNREFUSED ::1:8000` and drops every event, while GitHub still shows the delivery as 200 (smee.io's reply). Restart it. The delivery is still in **Advanced → Recent Deliveries**; click **Redeliver**. |
| Delivery shows **401** | `GITHUB_WEBHOOK_SECRET` differs from the app's secret. Fix `.env`, restart uvicorn, redeliver. |
| Delivery shows **503** | The app settings were not loaded. Check `.env` and restart uvicorn. |
| Events reach uvicorn but no check run appears | The startup log says *"settings are present but unusable"*, usually because of the key path: a double-quoted Windows path turns `\t` into a tab. Use forward slashes. `--reload` does not watch `.env`, so restart uvicorn after editing it. |
| smee-client was down for a while | smee does not replay old events. Redeliver each missed `pull_request` delivery from **Recent Deliveries**, or push an empty commit (`git commit --allow-empty -m "retrigger"`) to send a fresh `synchronize` event. |
| Redeliver does nothing | On purpose: a delivery whose review already completed is a no-op. To review again, click **Re-run** on the check (below) or push a new commit. |
| Check run is **neutral**, "No .traceability.yml" | The file is missing at the PR's head commit. Commit it on `main` and rebase the branch. |
| Check run is **neutral**, "No specification change" | The PR changed none of the files named in `.traceability.yml`. Expected for unrelated PRs. |
| Check run is **failure** with an error message | Bad config, an unreadable SRS or an invalid spec. The check summary says which. Fix it, then **Re-run**. |
| Check stuck *in progress* | The first review on a machine downloads the embedding model (~420 MB) and can take a few minutes. Watch the uvicorn log. If uvicorn was restarted mid-review, click **Re-run**. |
| "Requirements Traceability" missing in branch protection | It only appears after the check has run at least once in the repo. |
| Merge still allowed on a failing check | The check is not marked required, or you are an admin and bypass is allowed (step 5.5). |

**Re-run:** on the PR's **Checks** tab, click **Re-run**. GitHub sends `check_run.rerequested`; the bot creates a
fresh check run and edits its existing comment.

**Redeliver:** GitHub → app settings → **Advanced → Recent Deliveries** → pick the delivery → **Redeliver**. Each
delivery shows the request GitHub sent and the bot's response, which is the quickest way to debug a webhook.

## 7. Without the App: the GitHub Action

The same engine is also packaged as a composite action (`action.yml` at this repository's root). It needs no
server, tunnel or app registration, and runs on GitHub's own runners:

```yaml
# .github/workflows/traceability.yml in any repository that has a .traceability.yml
name: Traceability
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }        # the base commit must be in the clone
      - uses: raghavvag/AI-Assisted-Requirements-Traceability-Specification-Analysis@main
        with: { config: .traceability.yml, fail-on: block }
```

Inputs: `config`, `base-ref` (default: the merge base with the PR's base branch, or `github.event.before` on
push), `fail-on` and `python-version`. Outputs: `verdict`, `exit-code` and `report`. The report goes to the job
summary. Pin a tag (e.g. `@v1`) once one is published.

| | GitHub App | GitHub Action |
|---|---|---|
| Infrastructure | a running backend + webhook URL (smee locally) | none; GitHub's runners |
| Output | check run with a summary table, one PR comment edited in place, Live reviews tab | job summary, `report.md` / `report.json` artifacts |
| Linked issue acceptance criteria | yes (reads the issue the PR closes) | no |
| SRS formats | `.docx`, `.pdf`, `.md`, `.txt` | the same |
| Speed | model stays loaded between reviews | loads the model on every run (cached download) |
| Blocks merges | required check *Requirements Traceability* | required check = the workflow job |

Both call `src/review/engine.py`, so the verdict for the same change is identical. This repository runs the
action's logic in `.github/workflows/compliance.yml`, which also has a self-test job: it reviews store_v1 → store_v2
and fails if the gate stops blocking the known drift on REQ-04 and REQ-09.

## 8. Code-aware review (M5)

Add a `code:` block to `.traceability.yml` and the bot also reviews changed Python files, even when the PR does
not touch the OpenAPI spec:

```yaml
code:
  paths: ["app/**/*.py"]           # globs; ** spans directories
  exclude: ["tests/**"]
  model: jinaai/jina-embeddings-v2-base-code   # or "lexical" (offline, no model)
  thresholds: {}                   # optional overrides of the calibrated code thresholds
```

How it works (`src/alignment/code_units.py`, `src/review/code.py`):

- Every top-level function, method and class becomes a **semantic fingerprint**: route (FastAPI decorators
  with the router prefix), signature, docstring, the fields of the Pydantic models it takes or returns (with their
  `Field(description=…)`), the parameter attributes it reads, the keys it returns and the functions it calls.
  Line numbers are not part of it, so moving code is not a change.
- Base and head fingerprints are compared. A symbol is *modified* when its fingerprint differs, so a PR that only
  drops a field from `RefundRequest` also re-scores the handler that accepts it.
- A removed symbol whose body reappears almost unchanged under another name or file (≥ 80 % similar) is a
  **rename / move**, reviewed as a modification. Without that, renaming a handler would look like deleting it.
- Changed symbols go through the same rule as endpoints (tiered regression, trace-link top-k, severity),
  scored with the code model. Thresholds are calibrated per embedder: jina `-0.025 / -0.03`, `link_top_k: 5`
  (benign edits move scores by ≤ 0.013; dropping `transaction_id` moves REQ-04 by −0.036 … −0.048), lexical
  fallback `-0.10 / -0.15`, `link_top_k: 5`. Code uses top-5 trace links, not top-3: one requirement is
  implemented by several units (handler, models, policy helper, verifier), and REQ-09's step-up policy is its
  4th-best match.
- The check summary and the comment name the symbol and its `file:line`; the Live reviews tab links it to the line
  on GitHub.

The first code review downloads the jina model (~300 MB, `trust_remote_code=True`) into `~/.cache/huggingface`.
If it cannot load, the deterministic lexical fallback is used and the report says so.

**Known limits**

- A requirement can only regress if some code is traced to it (best match ≥ `relevance_floor`, 0.35). In the
  first demo service REQ-09 had no 2FA module (checkout only called an external `twofa.is_verified`), its best
  match was 0.315, and even deleting the whole 2FA check passed. Adding `app/twofa.py` (challenge, verify,
  `requires_step_up`) gave REQ-09 real trace links (best 0.62).
- Contract, field and symbol removals are caught well. Deleting one `if` inside a function that keeps its
  name, docstring and models barely moves the fingerprint and is not flagged on its own.
- The lexical fallback (no model) catches the REQ-04 field drop but not the REQ-09 step-up removal.

Demo: `python scripts/setup_demo_repo.py --code baseline --verify` (PR adds the service, PASS), merge it, then
`--code regression --verify` (PR drops `transaction_id` in Python only, BLOCK on REQ-04). For REQ-09:
`--code add-2fa` (adds `app/twofa.py`, PASS; only needed for a demo repo set up before it existed), merge it,
then `--code regression-2fa` (deletes the step-up policy, the checkout check and `two_fa_token`, BLOCK on
REQ-09). Live result on
2026-10-20: PR #7 PASS (14 symbols added), PR #8 BLOCK on REQ-04 via `process_refund` (`app/refunds.py:11`),
`RefundRequest` and `RefundReceipt`, merge blocked by the ruleset.
