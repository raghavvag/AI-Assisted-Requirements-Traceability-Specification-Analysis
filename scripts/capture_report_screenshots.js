#!/usr/bin/env node
/**
 * scripts/capture_report_screenshots.js — Project-II report, Section 6.4 figures
 * ===========================================================================
 * Drives the running React app with Playwright and saves Figures 6.1 – 6.9 as
 * PNGs in <repo>/demo/.
 *
 * The journey uses a dedicated project, "E-Commerce Platform (Report Demo)", that
 * is deleted and recreated on every run so the figures are reproducible:
 *   upload ecommerce_srs_ieee.docx + store_v1.json + store_v2.json (6.2, 6.3)
 *   -> Run Analysis -> heatmap (6.4) -> coverage (6.5) -> drift (6.6)
 *   -> REQ-04 explanation (6.7) -> PR #42 review (6.8) -> benchmarks (6.9)
 *   -> back to the dashboard (6.1, taken last so the card shows "Analysis ready").
 * The .docx fixture carries the same 10 requirements as the built-in demo SRS,
 * so the benchmark matches the published F1 0.71 vs 0.34.
 *
 * Install (once, from the repo root):
 *   cd frontend && npm install -D playwright && npx playwright install chromium
 *
 * Run (backend on :8000 and `npm run dev` on :5173 must already be up):
 *   node scripts/capture_report_screenshots.js
 *
 * Signing in: the app only supports Google OAuth, which automation cannot do, so
 * the script injects a session into localStorage. Either
 *   - copy one from your browser: open http://localhost:5173 signed in, run
 *     `copy(localStorage.access_token)` in the DevTools console, then
 *     set REPORT_ACCESS_TOKEN to it (valid for 60 minutes), or
 *   - leave it unset and the script runs scripts/mint_report_token.py, which
 *     signs a token for an existing user with SECRET_KEY from backend/.env.
 *
 * Environment (all optional):
 *   BASE_URL             app URL                        (http://localhost:5173)
 *   OUT_DIR              output folder, repo-relative   (demo)
 *   REPORT_ACCESS_TOKEN  JWT copied from the browser    (minted if unset)
 *   REPORT_USER_EMAIL    user to mint a token for       (first user in the DB)
 *   PYTHON               interpreter for the minter     (python / python3)
 *   PR_MODE              simulated | live               (simulated)
 *   PR_SCENARIO          add_endpoint | remove_idempotency | drop_2fa_requirement
 *                                                       (remove_idempotency)
 *   SCALE                device pixel ratio             (2)
 *   HEADED=1             show the browser while it runs
 */

'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const ROOT = path.resolve(__dirname, '..')

// Playwright is installed as a frontend devDependency; resolve it from there.
let playwright
try {
  playwright = require(require.resolve('playwright', { paths: [path.join(ROOT, 'frontend'), ROOT] }))
} catch {
  console.error('Playwright is not installed. Run:\n  cd frontend && npm install -D playwright && npx playwright install chromium')
  process.exit(1)
}
const { chromium, request } = playwright

// ── Configuration ───────────────────────────────────────────────────────────
const BASE_URL = (process.env.BASE_URL || 'http://localhost:5173').replace(/\/$/, '')
const OUT_DIR = path.resolve(ROOT, process.env.OUT_DIR || 'demo')
const PROJECT_NAME = 'E-Commerce Platform (Report Demo)'
const PROJECT_DESC = 'Nexus storefront: IEEE 830 SRS (.docx) traced against OpenAPI v1 and v2'
const VIEWPORT = { width: 1600, height: 1000 }   // wide enough for numbers in all 20 heatmap columns
const SCALE = Number(process.env.SCALE || 2)
const PR_MODE = process.env.PR_MODE || 'simulated'
const PR_SCENARIO = process.env.PR_SCENARIO || 'remove_idempotency'
const PR_NUMBER = { add_endpoint: '#41', remove_idempotency: '#42', drop_2fa_requirement: '#43' }
const DRIFT_REQS = ['REQ-04', 'REQ-09']          // preferred rows for the explainability figure

const UPLOADS = [
  path.join(ROOT, 'tests', 'fixtures', 'srs', 'ecommerce_srs_ieee.docx'),   // SRS
  path.join(ROOT, 'data', 'api_specs', 'store_v1.json'),                     // API_V1
  path.join(ROOT, 'data', 'api_specs', 'store_v2.json'),                     // API_V2
]
// Same options TraceabilityPage sends.
const ANALYZE_BODY = { threshold: 0.55, drift_threshold: -0.10, enable_drift: true }

// The first analysis loads the transformer model; LLM calls depend on the provider.
const ANALYSIS_TIMEOUT = 10 * 60_000
const LLM_TIMEOUT = 3 * 60_000
const UI_TIMEOUT = 30_000

// react-hot-toast's container; hidden only while a screenshot is taken.
const HIDE_TOASTS_CSS = '[style*="z-index: 9999"] { visibility: hidden !important; }'

const log = (...a) => console.log(...a)

// ── Session ─────────────────────────────────────────────────────────────────
function getAccessToken() {
  if (process.env.REPORT_ACCESS_TOKEN) return process.env.REPORT_ACCESS_TOKEN.trim().replace(/^"|"$/g, '')

  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  const args = [path.join(__dirname, 'mint_report_token.py')]
  if (process.env.REPORT_USER_EMAIL) args.push('--email', process.env.REPORT_USER_EMAIL)
  log('• No REPORT_ACCESS_TOKEN set; minting one with scripts/mint_report_token.py')
  const out = execFileSync(python, args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] })
  return JSON.parse(out.trim().split('\n').pop()).access_token
}

async function apiJson(responsePromise, what) {
  const res = await responsePromise
  if (!res.ok()) throw new Error(`${what}: HTTP ${res.status()} ${(await res.text()).slice(0, 300)}`)
  return res.status() === 204 ? null : res.json()
}

async function preflight(api) {
  const app = await api.get('/').catch(() => null)
  if (!app?.ok()) throw new Error(`Frontend not reachable at ${BASE_URL}. Start it with: cd frontend && npm run dev`)
  const health = await api.get('/api/health').catch(() => null)
  if (!health?.ok()) {
    throw new Error('Backend not reachable through the Vite proxy. Start it with: '
      + 'cd backend && python -m uvicorn app.main:app --port 8000')
  }
  const me = await api.get('/api/auth/me')
  if (!me.ok()) throw new Error(`The access token was rejected (HTTP ${me.status()}). Copy a fresh one or re-mint it.`)
  return me.json()
}

/** Fresh demo project every run, so re-runs produce the same figures. */
async function resetProject(api) {
  const projects = await apiJson(api.get('/api/projects'), 'list projects')
  for (const p of projects.filter((x) => x.name === PROJECT_NAME)) {
    await apiJson(api.delete(`/api/projects/${p.id}`), `delete old demo project ${p.id}`)
  }
  return apiJson(api.post('/api/projects', { data: { name: PROJECT_NAME, description: PROJECT_DESC } }), 'create project')
}

// ── Page helpers ────────────────────────────────────────────────────────────
/** Wait for network, fonts, spinners and entrance animations to finish. */
async function settle(page, ms = 800) {
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {})
  await page.evaluate(() => document.fonts?.ready)
  await page.waitForFunction(() => !document.querySelector('.spinner'), null, { timeout: 15_000 }).catch(() => {})
  await page.waitForTimeout(ms)
}

/** Waits until Plotly has drawn axes inside `scope`. */
async function waitForPlot(scope) {
  await scope.locator('.js-plotly-plot .main-svg').first().waitFor({ timeout: UI_TIMEOUT })
  // g.xtick holds the label; heatmaps also draw invisible path.xtick tick marks.
  await scope.locator('.js-plotly-plot g.xtick text').first().waitFor({ timeout: UI_TIMEOUT })
}

/**
 * Screenshot the viewport. With fullHeight, the viewport is first stretched to
 * fit the page's inner scroll container (project pages scroll inside <main>,
 * so Playwright's fullPage option would not include that content).
 */
async function shoot(page, file, { fullHeight = false, maxHeight = 2800 } = {}) {
  await settle(page)
  const style = await page.addStyleTag({ content: HIDE_TOASTS_CSS })
  try {
    if (fullHeight) {
      const overflow = await page.evaluate(() => {
        const main = document.querySelector('main')
        const scrollers = [main, main?.firstElementChild].filter(Boolean)
        return Math.max(0, ...scrollers.map((el) => el.scrollHeight - el.clientHeight))
      })
      if (overflow > 0) {
        await page.setViewportSize({ width: VIEWPORT.width, height: Math.min(VIEWPORT.height + overflow, maxHeight) })
        await page.waitForTimeout(900)   // let Plotly's resize handler redraw
      }
    }
    await page.screenshot({ path: path.join(OUT_DIR, file), animations: 'disabled', caret: 'hide' })
    log(`  ✓ saved demo/${file}`)
  } finally {
    await style.evaluate((el) => el.remove())
    await page.setViewportSize(VIEWPORT)
  }
}

/**
 * Click a button that triggers a slow API call and wait for that call. axios in
 * the app gives up after 60 s, which a cold model load can exceed; in that case
 * the call is repeated directly with a long timeout and `onRetried` refreshes the UI.
 */
async function clickAndAwait(page, api, button, { urlPart, method = 'POST', retry, onRetried }) {
  const matches = (req) => req.url().includes(urlPart) && req.method() === method
  const done = page.waitForResponse((r) => matches(r.request()), { timeout: ANALYSIS_TIMEOUT })
    .then(async (r) => (r.ok() ? 'ok' : `HTTP ${r.status()}: ${(await r.text()).slice(0, 300)}`))
  const aborted = page.waitForEvent('requestfailed', { predicate: matches, timeout: ANALYSIS_TIMEOUT })
    .then(() => 'aborted')
  done.catch(() => {}); aborted.catch(() => {})

  await button.click()
  const outcome = await Promise.race([done, aborted])
  if (outcome === 'ok') return
  if (outcome === 'aborted' && retry) {
    log('  … the browser request timed out (cold model load); finishing it directly')
    await retry()
    await onRetried?.()
    return
  }
  throw new Error(`${urlPart} failed: ${outcome}`)
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true })
  for (const f of fs.readdirSync(OUT_DIR)) if (f.startsWith('_debug_')) fs.rmSync(path.join(OUT_DIR, f))
  for (const f of UPLOADS) if (!fs.existsSync(f)) throw new Error(`Missing input file: ${f}`)

  const token = getAccessToken()
  const api = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Authorization: `Bearer ${token}` } })
  const user = await preflight(api)
  log(`• Signed in as ${user.name} <${user.email}>`)
  const project = await resetProject(api)
  log(`• Demo project #${project.id} "${project.name}" created`)

  const browser = await chromium.launch({ headless: process.env.HEADED !== '1' })
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: 'dark', locale: 'en-US' })
  // Runs before the app's own scripts on every navigation, so AuthProvider starts signed in.
  await context.addInitScript(({ token, user }) => {
    try {
      localStorage.setItem('access_token', token)
      localStorage.setItem('user', JSON.stringify(user))
    } catch { /* about:blank has no storage */ }
  }, { token, user })
  const page = await context.newPage()
  page.setDefaultTimeout(UI_TIMEOUT)

  const projectUrl = `${BASE_URL}/projects/${project.id}`
  const results = []
  async function figure(id, title, fn) {
    log(`\n▶ Figure ${id}: ${title}`)
    try {
      await fn()
      results.push({ id, ok: true })
    } catch (err) {
      results.push({ id, ok: false, error: err.message.split('\n')[0] })
      console.error(`  ✗ ${err.message}`)
      await page.setViewportSize(VIEWPORT).catch(() => {})
      await page.screenshot({ path: path.join(OUT_DIR, `_debug_${id}.png`) }).catch(() => {})
    }
  }
  const card = () => page.locator('div.cursor-pointer', {
    has: page.getByRole('heading', { name: PROJECT_NAME, exact: true }),
  })
  const modal = page.locator('div.fixed.inset-0.z-50')
  const sidebarLink = (name) => page.getByRole('link', { name, exact: true })

  // 6.2 — Upload modal with the three dropzones filled in.
  await figure('6.2', 'Upload interface', async () => {
    await page.goto(`${BASE_URL}/dashboard`)
    await card().getByRole('button', { name: 'Upload', exact: true }).click()
    await modal.getByRole('heading', { name: 'Upload Artifacts' }).waitFor()
    const inputs = modal.locator('input[type="file"]')
    for (let i = 0; i < UPLOADS.length; i++) await inputs.nth(i).setInputFiles(UPLOADS[i])
    for (const f of UPLOADS) await modal.getByText(path.basename(f), { exact: true }).waitFor()
    await shoot(page, 'Figure_6.2_Upload_Interface.png')
  })

  // 6.3 — SrsPreview: what the parser extracted, before any analysis runs.
  await figure('6.3', 'Extracted requirements', async () => {
    await modal.getByRole('button', { name: 'Upload Files' }).click()
    const review = modal.getByRole('heading', { name: 'Review extracted requirements' })
    await review.waitFor({ timeout: 2 * 60_000 }).catch(async () => {
      throw new Error(`Upload did not reach the review step. Modal says: ${(await modal.innerText()).slice(0, 400)}`)
    })
    // Expand one row to show where in the .docx the requirement was found.
    await modal.locator('li > button', { hasText: 'REQ-04' }).click()
    await modal.getByText('Location:').waitFor()
    await shoot(page, 'Figure_6.3_Extracted_Requirements.png')
    await modal.getByRole('button', { name: 'Looks good' }).click()
    await modal.waitFor({ state: 'detached' })
  })

  // 6.4 — Run the analysis from the UI, then the similarity heatmap.
  await figure('6.4', 'Traceability matrix', async () => {
    await page.goto(projectUrl)
    await page.getByRole('heading', { name: 'Traceability Analysis' }).waitFor()
    await settle(page, 300)
    log('  … running the analysis (the first run loads the embedding model)')
    await clickAndAwait(page, api, page.getByRole('button', { name: 'Run Analysis' }).first(), {
      urlPart: `/api/projects/${project.id}/analyze`,
      retry: () => apiJson(api.post(`/api/projects/${project.id}/analyze`,
        { data: ANALYZE_BODY, timeout: ANALYSIS_TIMEOUT }), 'analyze'),
      onRetried: () => page.getByTitle('Reload cached result').click(),
    })
    await page.getByText('Requirements covered').waitFor()
    await page.getByRole('tab', { name: 'Heatmap' }).click()
    await page.getByRole('heading', { name: 'Similarity matrix' }).waitFor()
    await waitForPlot(page)
    await page.locator('.js-plotly-plot g.ytick text').first().waitFor()
    await shoot(page, 'Figure_6.4_Traceability_Matrix.png')
  })

  // 6.5 — Coverage list: bands per requirement plus SRS priority badges.
  await figure('6.5', 'Compliance and coverage', async () => {
    if (!page.url().startsWith(projectUrl)) await page.goto(projectUrl)
    await page.getByRole('tab', { name: 'Coverage' }).click()
    await page.getByText(/covered at ≥/).waitFor()
    await shoot(page, 'Figure_6.5_Compliance_Coverage.png')
  })

  // 6.6 — v1 -> v2 drift with severity classes.
  await figure('6.6', 'Specification drift', async () => {
    await sidebarLink('Drift Analysis').click()
    await page.getByRole('heading', { name: 'Drift Analysis' }).waitFor()
    if (await page.getByText('No drift data').isVisible()) throw new Error('No drift data: the API_V2 spec was not analysed.')
    await page.getByRole('heading', { name: 'Compliance across versions' }).waitFor()
    await waitForPlot(page)
    await page.getByText(/^Drifted pairs \(\d+\)$/).waitFor()
    await shoot(page, 'Figure_6.6_Specification_Drift.png')
  })

  // 6.7 — Expand a drifted requirement for the LLM root-cause + remediation.
  await figure('6.7', 'Explainability output', async () => {
    let rowButton = null
    for (const req of DRIFT_REQS) {
      const candidate = page.locator('button', { has: page.locator('span.font-mono', { hasText: new RegExp(`^${req}$`) }) }).first()
      if (await candidate.count()) { rowButton = candidate; log(`  … expanding ${req}`); break }
    }
    if (!rowButton) {
      rowButton = page.locator('main button:has(svg.lucide-chevron-down)').first()
      log('  … REQ-04 / REQ-09 not in the drift list; expanding the first drifted pair')
    }
    const row = rowButton.locator('xpath=..')
    await rowButton.click()
    const text = row.locator('p.whitespace-pre-wrap')
    await text.waitFor({ timeout: LLM_TIMEOUT })
    if (/unavailable/i.test(await text.innerText())) {
      log('  ! the LLM did not answer (check OPENAI_API_KEY / GROQ_API_KEY in backend/.env); the figure shows the fallback text')
    }
    await row.evaluate((el) => el.scrollIntoView({ block: el.offsetHeight > window.innerHeight * 0.7 ? 'start' : 'center' }))
    await shoot(page, 'Figure_6.7_Explainability_Output.png')
  })

  // 6.8 — PR bot verdict + the Markdown comment it would post.
  await figure('6.8', 'Pull-request review', async () => {
    await sidebarLink('PR Bot').click()
    await page.getByRole('heading', { name: 'PR Compliance Bot' }).waitFor()

    let live = false
    if (PR_MODE === 'live') {
      await page.getByRole('tab', { name: 'Live reviews' }).click()
      await page.getByText(/^Pull request reviews \(\d+\)$|^No live reviews yet$/).waitFor()
      const rows = page.locator('main button:has(span.rounded-full)')
      if (await page.getByText('No live reviews yet').isVisible()) {
        log('  … no live reviews recorded; using the simulated scenario instead')
        await page.getByRole('tab', { name: 'Simulated' }).click()
      } else {
        const blocked = rows.filter({ has: page.getByText('Block', { exact: true }) })
        await ((await blocked.count()) ? blocked.first() : rows.first()).click()
        await page.getByText(/^(Generated review comment|Check run summary)$/).first().waitFor()
        live = true
      }
    }
    if (!live) {
      const pr = PR_NUMBER[PR_SCENARIO]
      if (!pr) throw new Error(`Unknown PR_SCENARIO "${PR_SCENARIO}"`)
      await page.locator('button', { hasText: `PR ${pr}` }).click()
      await clickAndAwait(page, api, page.getByRole('button', { name: 'Run review' }), {
        urlPart: `/api/projects/${project.id}/pr-review`,
      })
      await page.getByText(/^Merge (blocked|approved)$/).waitFor()
      await page.getByText('Generated review comment').waitFor()
    }
    await shoot(page, 'Figure_6.8_PR_Review.png', { fullHeight: true })
  })

  // 6.9 — Precision / Recall / F1 / MAP, transformer vs TF-IDF.
  await figure('6.9', 'Evaluation dashboard', async () => {
    await sidebarLink('Benchmarks').click()
    await page.getByRole('heading', { name: 'Evaluation Metrics' }).waitFor()
    if (await page.getByText('No benchmark data').isVisible()) throw new Error('No benchmark data: ground-truth labels did not match.')
    await page.getByText('Transformer vs TF-IDF').waitFor()
    await waitForPlot(page)
    const f1 = await page.locator('.metric-card', { hasText: 'F1-Score' }).innerText()
    if (!/0\.71\d/.test(f1)) log(`  ! F1 card reads "${f1.replace(/\s+/g, ' ')}", not the published 0.71`)
    await shoot(page, 'Figure_6.9_Evaluation_Dashboard.png')
  })

  // 6.1 — Dashboard, taken last so the demo card shows its uploads and finished analysis.
  await figure('6.1', 'Project dashboard', async () => {
    await page.goto(`${BASE_URL}/dashboard`)
    await page.getByRole('heading', { name: 'My Projects' }).waitFor()
    await card().waitFor()
    await card().hover()   // highlights the card ("selected") and shows its actions
    await shoot(page, 'Figure_6.1_Dashboard.png')
  })

  await browser.close()
  await api.dispose()

  log('\nSummary')
  for (const r of results.sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))) {
    log(`  Figure ${r.id}  ${r.ok ? 'ok' : `FAILED: ${r.error} (see demo/_debug_${r.id}.png)`}`)
  }
  if (results.some((r) => !r.ok)) process.exitCode = 1
}

main().catch((err) => {
  console.error(`\n✗ ${err.message}`)
  process.exit(1)
})
