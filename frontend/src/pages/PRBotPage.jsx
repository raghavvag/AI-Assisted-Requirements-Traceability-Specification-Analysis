// frontend/src/pages/PRBotPage.jsx
// Two tabs: "Simulated" runs the built-in PR scenarios through the review
// engine; "Live reviews" shows what the GitHub App posted on real pull requests
// (PRReviewRun rows from GET /api/github/reviews), refreshed every 5 seconds.
// Deep links: ?tab=live&review=<id> opens a review (used by the dashboard).
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import {
  CheckCircle2, Code2, ExternalLink, FileCode2, FlaskConical, GitCommitHorizontal,
  GitPullRequest, Inbox, MessageSquare, Play, Radio, RefreshCw, ShieldAlert, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'

import { analysisApi, apiError, githubApi } from '../services/api'
import Markdown from '../components/Markdown'
import { VERDICTS, VerdictBadge, ago, useNow } from '../components/live'
import { EmptyState, Loading, PageHeader, PageShell, Panel, Segmented, StatCard, StatGrid } from '../components/ui'

const POLL_MS = 5000

const SCENARIOS = [
  {
    key: 'add_endpoint',
    pr: '#41',
    title: 'Add optional discount_code field',
    detail: 'Additive, non-breaking change to the cart endpoint.',
    expect: 'Expected: approved — nothing loses alignment.',
    tone: 'good',
  },
  {
    key: 'remove_idempotency',
    pr: '#42',
    title: 'Replace client transaction_id with a server reference',
    detail: 'Drops the client-provided idempotency key from the refund endpoint.',
    expect: 'Expected: blocked — REQ-04 (refund audit trail).',
    tone: 'bad',
  },
  {
    key: 'drop_2fa_requirement',
    pr: '#43',
    title: 'Remove the 2FA step-up gate from checkout',
    detail: 'High-value orders would process without step-up verification.',
    expect: 'Expected: blocked — REQ-09 (two-factor enforcement).',
    tone: 'bad',
  },
]

const TABS = [
  { key: 'simulated', label: 'Simulated', icon: FlaskConical },
  { key: 'live', label: 'Live reviews', icon: Radio },
]

// "Critical Drift" / "Major Drift" / "Minor Drift"
const SEVERITY = {
  Critical: 'bg-noncompliant/15 text-noncompliant ring-noncompliant/30',
  Major:    'bg-orange-400/12 text-orange-300 ring-orange-400/30',
  Minor:    'bg-partial/12 text-partial ring-partial/30',
}
const severityClass = (s = '') => SEVERITY[Object.keys(SEVERITY).find((k) => s.startsWith(k))] || SEVERITY.Minor

export default function PRBotPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'live' ? 'live' : 'simulated'
  const setTab = (key) => setParams(key === 'live' ? { tab: 'live' } : {}, { replace: true })

  return (
    <PageShell scroll>
      <PageHeader
        title="PR Compliance Bot"
        subtitle={tab === 'simulated'
          ? 'Simulated CI review — re-scores changed endpoints against the requirement baseline'
          : 'Reviews the GitHub App posted on real pull requests — updates live'}
      >
        <Segmented value={tab} onChange={setTab} options={TABS} />
      </PageHeader>
      {tab === 'simulated' ? <SimulatedReview /> : <LiveReviews />}
    </PageShell>
  )
}

// ---------------------------------------------------------------------------
// Simulated
// ---------------------------------------------------------------------------

function SimulatedReview() {
  const { id } = useParams()
  const [scenario, setScenario] = useState(SCENARIOS[0].key)
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)

  async function run() {
    setRunning(true)
    const toastId = toast.loading('Running compliance review…')
    try {
      const res = await analysisApi.prReview(id, scenario)
      setResult(res.data)
      toast.success(res.data.approved ? 'PR approved' : 'PR blocked', { id: toastId })
    } catch (err) {
      toast.error(apiError(err, 'Review failed.'), { id: toastId })
    } finally { setRunning(false) }
  }

  const active = SCENARIOS.find((s) => s.key === scenario)

  return (
    <>
      <Panel
        title="Pull request scenario"
        hint="Each scenario is a real before/after endpoint schema, embedded and compared live."
        actions={
          <button className="btn-primary" onClick={run} disabled={running}>
            {running ? <span className="spinner" /> : <Play size={14} />}
            {running ? 'Reviewing…' : 'Run review'}
          </button>
        }
        className="shrink-0"
        bodyClass="p-3"
      >
        <div className="grid gap-2 md:grid-cols-3">
          {SCENARIOS.map((s) => {
            const on = scenario === s.key
            return (
              <button
                key={s.key}
                onClick={() => setScenario(s.key)}
                className={`text-left p-3.5 rounded-xl border transition-all h-full
                  ${on ? 'border-brand-400/45 bg-brand-400/8 ring-1 ring-brand-400/20'
                       : 'border-white/8 hover:border-white/15 hover:bg-white/[0.025]'}`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <GitPullRequest size={13} className={on ? 'text-brand-400' : 'text-white/30'} />
                  <span className="font-mono text-[11px] text-white/45">PR {s.pr}</span>
                </div>
                <p className="text-[13px] font-medium text-white leading-snug mb-1">{s.title}</p>
                <p className="text-[11px] text-white/40 leading-snug mb-2">{s.detail}</p>
                <p className={`text-[11px] font-medium ${s.tone === 'good' ? 'text-compliant' : 'text-noncompliant'}`}>
                  {s.expect}
                </p>
              </button>
            )
          })}
        </div>
      </Panel>

      <AnimatePresence mode="wait">
        {result && (
          <motion.div
            key={result.scenario + String(result.approved)}
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="flex flex-col gap-4 shrink-0"
          >
            <VerdictBanner
              approved={result.approved}
              violations={result.violations}
              tag={`${active?.pr} · ${result.scenario}`}
            />
            <ViolationStats violations={result.violations} />
            <ViolationList violations={result.violations} />
            <CommentPanel markdown={result.review_comment} hint="Exactly what the bot would post on the pull request" />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

// ---------------------------------------------------------------------------
// Live reviews
// ---------------------------------------------------------------------------

/** Poll GET /api/github/reviews while the tab is open and the page is visible. */
function useLiveReviews(enabled) {
  const [state, setState] = useState({ reviews: null, configured: true, error: '', updatedAt: null })
  const [refreshing, setRefreshing] = useState(false)
  const failed = useRef(false)

  const load = useCallback(async ({ manual = false } = {}) => {
    if (manual) setRefreshing(true)
    try {
      const res = await githubApi.reviews({ limit: 100 })
      failed.current = false
      setState({ reviews: res.data.reviews, configured: res.data.app_configured, error: '', updatedAt: new Date() })
    } catch (err) {
      const msg = apiError(err, 'Could not load live reviews.')
      if (!failed.current || manual) toast.error(msg)          // one toast per outage, not one per poll
      failed.current = true
      setState((s) => ({ ...s, reviews: s.reviews ?? [], error: msg }))
    } finally { if (manual) setRefreshing(false) }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!enabled) return undefined
    const tick = () => { if (document.visibilityState === 'visible') load() }
    const timer = setInterval(tick, POLL_MS)
    document.addEventListener('visibilitychange', tick)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', tick) }
  }, [enabled, load])

  return { ...state, refreshing, refresh: () => load({ manual: true }) }
}

function LiveReviews() {
  const [params, setParams] = useSearchParams()
  const [auto, setAuto] = useState(true)
  const { reviews, configured, error, updatedAt, refreshing, refresh } = useLiveReviews(auto)
  const selected = params.get('review') ? Number(params.get('review')) : null
  const [detail, setDetail] = useState(null)
  const [fresh, setFresh] = useState(() => new Set())          // ids that arrived since the page opened
  const seen = useRef(null)
  const now = useNow(1000)

  const select = (id) => setParams(id == null ? { tab: 'live' } : { tab: 'live', review: String(id) }, { replace: true })

  // Highlight rows that appear while the page is open (a webhook just landed).
  useEffect(() => {
    if (!reviews) return
    const ids = new Set(reviews.map((r) => `${r.id}:${r.created_at}`))
    if (seen.current) {
      const added = [...ids].filter((k) => !seen.current.has(k)).map((k) => Number(k.split(':')[0]))
      if (added.length) {
        setFresh((f) => new Set([...f, ...added]))
        toast.success(`${added.length} new review${added.length > 1 ? 's' : ''} from GitHub`)
        setTimeout(() => setFresh((f) => new Set([...f].filter((x) => !added.includes(x)))), 6000)
      }
    }
    seen.current = ids
  }, [reviews])

  // (Re)load the detail when the selection changes or the selected row is re-reviewed.
  const selectedRow = reviews?.find((r) => r.id === selected)
  const selectedKey = selectedRow ? `${selectedRow.id}:${selectedRow.created_at}` : String(selected)
  useEffect(() => {
    if (selected == null) { setDetail(null); return undefined }
    let cancelled = false
    githubApi.review(selected)
      .then((res) => { if (!cancelled) setDetail(res.data) })
      .catch((err) => { if (!cancelled) toast.error(apiError(err, 'Could not load the review.')) })
    return () => { cancelled = true }
  }, [selected, selectedKey])

  if (reviews == null) return <Loading label="Loading live reviews…" />

  const toolbar = (
    <div className="flex items-center gap-3">
      <span className="text-[11px] text-white/35 tnum hidden md:inline">
        {error ? <span className="text-noncompliant">offline · {error}</span>
          : updatedAt ? `updated ${ago(updatedAt, now)}` : ''}
      </span>
      <label className="flex items-center gap-1.5 text-[11px] text-white/50 cursor-pointer select-none">
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-cyan-400" />
        Auto-refresh {POLL_MS / 1000}s
        {auto && <span className="w-1.5 h-1.5 rounded-full bg-compliant animate-pulse" />}
      </label>
      <button className="btn-secondary" onClick={refresh} disabled={refreshing}>
        <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} /> Refresh
      </button>
    </div>
  )

  if (!reviews.length) {
    return (
      <Panel title="Live reviews" actions={toolbar} className="shrink-0" bodyClass="p-6">
        <EmptyState
          icon={Inbox}
          title="No live reviews yet"
          description={configured
            ? 'Open or push to a pull request that changes the spec, the SRS or code named in .traceability.yml. The bot reviews it and the run shows up here within seconds.'
            : 'The GitHub App is not configured on this backend. Set GITHUB_APP_ID, the private key and GITHUB_WEBHOOK_SECRET in backend/.env (docs/github-app-setup.md).'}
        />
      </Panel>
    )
  }

  const counts = reviews.reduce((acc, r) => ({ ...acc, [r.verdict]: (acc[r.verdict] || 0) + 1 }), {})

  return (
    <>
      <StatGrid cols={4}>
        <StatCard label="Reviews" value={reviews.length} hint={`across ${new Set(reviews.map((r) => r.repo)).size} repo(s)`} />
        <StatCard label="Passed" value={counts.PASS || 0} hint="no requirement regressed" tone="good" />
        <StatCard label="Blocked" value={counts.BLOCK || 0} hint="merge blocked by the check" tone="bad" />
        <StatCard label="Warn / skipped / errors" value={(counts.WARN || 0) + (counts.SKIPPED || 0) + (counts.ERROR || 0)}
                  hint="partial intent, no spec change, or a failure" tone="warn" />
      </StatGrid>

      <Panel
        title={`Pull request reviews (${reviews.length})`}
        hint="Newest first. Select a row for the violations and the posted comment."
        actions={toolbar}
        className="shrink-0"
        bodyClass="p-2"
      >
        <div className="flex flex-col">
          {reviews.map((r) => (
            <ReviewRow key={r.id} r={r} now={now} selected={selected === r.id} fresh={fresh.has(r.id)}
                       onClick={() => select(selected === r.id ? null : r.id)} />
          ))}
        </div>
      </Panel>

      <AnimatePresence mode="wait">
        {detail && detail.id === selected && (
          <motion.div
            key={`${detail.id}:${detail.created_at}`}
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="flex flex-col gap-4 shrink-0"
          >
            <LiveDetailHeader review={detail} />
            {detail.error && (
              <Panel title="Review error" className="shrink-0" bodyClass="p-3">
                <pre className="code-block text-[11px] whitespace-pre-wrap">{detail.error}</pre>
              </Panel>
            )}
            <ViolationStats violations={detail.violations} />
            <ViolationList violations={detail.violations} repo={detail.repo} sha={detail.head_sha} baseSha={detail.base_sha} />
            <CodeChanges changes={detail.report?.code_changes} repo={detail.repo} sha={detail.head_sha} baseSha={detail.base_sha} />
            {detail.markdown
              ? <CommentPanel markdown={detail.markdown} hint="The comment the bot posted (updated on every push)"
                              link={detail.comment_url} />
              : detail.summary && <CommentPanel markdown={detail.summary} title="Check run summary" hint={detail.title}
                                                link={detail.check_run_url} />}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function ReviewRow({ r, now, selected, fresh, onClick }) {
  const reqs = [...new Set((r.violations || []).map((v) => v.requirement_id))]
  return (
    <div
      role="button" tabIndex={0} onClick={onClick} onKeyDown={(e) => e.key === 'Enter' && onClick()}
      className={`text-left px-3 py-2.5 rounded-xl flex items-center gap-3 transition-colors cursor-pointer
        ${selected ? 'bg-brand-400/8 ring-1 ring-brand-400/20' : fresh ? 'bg-compliant/8' : 'hover:bg-white/[0.03]'}`}
    >
      <VerdictBadge verdict={r.verdict} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-white truncate">
          <a href={r.pr_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
             className="font-mono text-white/55 hover:text-brand-300 hover:underline">
            {r.repo}#{r.pr_number}
          </a>{' '}
          {r.pr_title || r.title}
        </p>
        <p className="text-[11px] text-white/40 truncate flex items-center gap-1.5">
          {r.code_scan && (
            <span className="inline-flex items-center gap-1 px-1.5 rounded bg-brand-400/10 text-brand-300 ring-1 ring-brand-400/20">
              <Code2 size={10} /> code
            </span>
          )}
          <span className="truncate">{r.title}{reqs.length > 0 && ` · ${reqs.join(', ')}`}</span>
        </p>
      </div>
      {r.head_sha && (
        <a href={`https://github.com/${r.repo}/commit/${r.head_sha}`} target="_blank" rel="noreferrer"
           onClick={(e) => e.stopPropagation()} title={r.head_sha}
           className="text-[11px] font-mono text-white/35 hover:text-brand-300 shrink-0 hidden md:flex items-center gap-1">
          <GitCommitHorizontal size={12} />{r.head_sha.slice(0, 7)}
        </a>
      )}
      {r.check_run_url && (
        <a href={r.check_run_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
           title="GitHub check run" className="text-white/30 hover:text-brand-300 shrink-0">
          <ExternalLink size={13} />
        </a>
      )}
      <span className="text-[11px] text-white/35 shrink-0 w-20 text-right tnum" title={fmtDate(r.created_at)}>
        {ago(r.created_at, now)}
      </span>
    </div>
  )
}

function LiveDetailHeader({ review: r }) {
  const v = VERDICTS[r.verdict] || VERDICTS.SKIPPED
  const Icon = v.icon
  return (
    <div className={`rounded-2xl border p-4 flex items-center gap-4 shrink-0 ring-1 ${v.ring} border-transparent flex-wrap`}>
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${v.ring} ${v.text}`}>
        <Icon size={22} />
      </div>
      <div className="min-w-0 flex-1">
        <p className={`font-bold text-[15px] ${v.text}`}>{r.title}</p>
        <p className="text-xs text-white/50 mt-0.5 truncate">
          {r.repo}#{r.pr_number} {r.pr_title && `· ${r.pr_title}`} {r.pr_author && `· @${r.pr_author}`}
          {r.linked_issues?.length > 0 && ` · closes ${r.linked_issues.map((i) => `#${i.number}`).join(', ')}`}
        </p>
        <p className="text-[11px] text-white/35 mt-0.5 font-mono">
          {r.base_sha?.slice(0, 7)} → {r.head_sha?.slice(0, 7)} · {r.trigger} · {fmtDate(r.created_at)}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {r.pr_url && <ExtLink href={r.pr_url} icon={GitPullRequest}>Pull request</ExtLink>}
        {r.check_run_url && <ExtLink href={r.check_run_url}>Check run</ExtLink>}
        {r.comment_url && <ExtLink href={r.comment_url} icon={MessageSquare}>Comment</ExtLink>}
      </div>
    </div>
  )
}

function ExtLink({ href, children, icon: Icon = ExternalLink }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
      <Icon size={12} /> {children}
    </a>
  )
}

// ---------------------------------------------------------------------------
// Shared result pieces
// ---------------------------------------------------------------------------

function VerdictBanner({ approved, violations = [], tag }) {
  return (
    <div className={`rounded-2xl border p-4 flex items-center gap-4 shrink-0
      ${approved ? 'bg-compliant/8 border-compliant/25' : 'bg-noncompliant/8 border-noncompliant/25'}`}
    >
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0
        ${approved ? 'bg-compliant/15 text-compliant' : 'bg-noncompliant/15 text-noncompliant'}`}>
        {approved ? <CheckCircle2 size={22} /> : <XCircle size={22} />}
      </div>
      <div className="min-w-0">
        <p className={`font-bold text-[15px] ${approved ? 'text-compliant' : 'text-noncompliant'}`}>
          {approved ? 'Merge approved' : 'Merge blocked'}
        </p>
        <p className="text-xs text-white/50 mt-0.5">
          {approved
            ? 'No requirement lost alignment in this change.'
            : `${violations.length} compliance violation${violations.length === 1 ? '' : 's'} — the change weakens a requirement that the baseline satisfied.`}
        </p>
      </div>
      {tag && <span className="ml-auto text-[11px] font-mono text-white/25 shrink-0 hidden sm:block">{tag}</span>}
    </div>
  )
}

function ViolationStats({ violations = [] }) {
  if (!violations.length) return null
  return (
    <StatGrid cols={3}>
      <StatCard label="Violations" value={violations.length} hint="requirement / artefact pairs that regressed" tone="bad" />
      <StatCard
        label="Largest drop"
        value={Math.min(...violations.map((v) => v.delta)).toFixed(3)}
        hint="cosine delta base → head"
        tone="bad"
      />
      <StatCard
        label="Requirements hit"
        value={[...new Set(violations.map((v) => v.requirement_id))].join(', ')}
        hint="see remediation below"
        tone="warn"
      />
    </StatGrid>
  )
}

/** "app/refunds.py:11" -> a GitHub blob link at the reviewed commit. */
function codeLink(repo, sha, location) {
  const m = /^(.*):(\d+)$/.exec(location || '')
  if (!repo || !sha || !m) return null
  return `https://github.com/${repo}/blob/${sha}/${m[1]}#L${m[2]}`
}

function Artifact({ v, repo, sha, baseSha }) {
  const kind = v.artifact_kind || 'endpoint'
  if (kind === 'endpoint') {
    return <p className="text-[13px] font-mono text-white/85 break-all">{v.endpoint_id}</p>
  }
  const removed = v.change_status === 'removed'
  const href = codeLink(repo, removed ? baseSha : sha, v.location)     // a removed symbol only exists at the base
  const name = v.endpoint_id.split('::').pop()
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <FileCode2 size={14} className="text-brand-300 shrink-0" />
      <span className="text-[11px] uppercase tracking-wider text-white/40">Python {kind}</span>
      <span className={`text-[13px] font-mono ${removed ? 'text-noncompliant line-through decoration-noncompliant/50' : 'text-white/90'}`}>{name}</span>
      {removed && <span className="text-[10px] uppercase tracking-wider font-semibold text-noncompliant">removed in this PR</span>}
      {href
        ? <a href={href} target="_blank" rel="noreferrer" className="text-[11px] font-mono text-brand-300 hover:underline">{v.location}</a>
        : <span className="text-[11px] font-mono text-white/45">{v.location}</span>}
    </div>
  )
}

function ViolationList({ violations = [], repo, sha, baseSha }) {
  if (!violations.length) return null
  return (
    <Panel title="Violations" hint="Each includes the model's root-cause analysis and a fix." className="shrink-0" bodyClass="p-3">
      <div className="flex flex-col gap-2">
        {violations.map((v, i) => (
          <div key={i} className="glass rounded-xl border border-noncompliant/20 p-4">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="w-7 h-7 rounded-lg bg-noncompliant/15 text-noncompliant flex items-center justify-center shrink-0">
                <ShieldAlert size={14} />
              </span>
              <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 ${severityClass(v.severity)}`}>
                {v.severity}
              </span>
              <span className="font-mono text-[11px] text-brand-300">{v.requirement_id}</span>
              <span className="ml-auto flex items-center gap-3 text-xs tnum">
                <span className="text-white/35 text-[10px] uppercase tracking-wider">score</span>
                <span className="text-compliant">{v.score_v1?.toFixed(3)}</span>
                <span className="text-white/20">→</span>
                <span className="text-white/60">{v.score_v2?.toFixed(3)}</span>
                <span className="text-noncompliant font-semibold">{v.delta?.toFixed(3)}</span>
              </span>
            </div>
            <div className="mb-2"><Artifact v={v} repo={repo} sha={sha} baseSha={baseSha} /></div>
            {v.endpoint_change_description && (
              <p className="text-[11px] text-white/45 mb-1.5">{v.endpoint_change_description}</p>
            )}
            {v.requirement_text && (
              <p className="text-[11px] text-white/35 mb-2 line-clamp-2" title={v.requirement_text}>
                <span className="text-white/50">{v.requirement_id}:</span> {v.requirement_text}
              </p>
            )}
            {v.explanation && (
              <div className="border-t border-white/5 pt-2">
                <p className="text-[10px] uppercase tracking-[0.14em] text-white/30 mb-1">Root cause &amp; remediation</p>
                <Markdown>{v.explanation}</Markdown>
              </div>
            )}
          </div>
        ))}
      </div>
    </Panel>
  )
}

function CodeChanges({ changes, repo, sha, baseSha }) {
  if (!changes?.length) return null
  const tone = { added: 'text-compliant', removed: 'text-noncompliant', modified: 'text-partial' }
  return (
    <Panel title={`Code changes reviewed (${changes.length})`}
           hint="Python symbols whose semantic fingerprint changed, scored with the code embedder."
           className="shrink-0" bodyClass="p-2">
      <div className="flex flex-col">
        {changes.map((c) => {
          const href = codeLink(repo, c.status === 'removed' ? baseSha : sha, c.location)
          return (
            <div key={c.endpoint_id} className="px-3 py-1.5 flex items-center gap-3 text-[12px]">
              <span className={`w-16 text-[10px] uppercase tracking-wider font-semibold ${tone[c.status] || 'text-white/50'}`}>{c.status}</span>
              <span className="text-white/40 w-14 text-[11px]">{c.kind}</span>
              <span className="font-mono text-white/85 truncate">{c.endpoint_id.split('::').pop()}</span>
              {href
                ? <a href={href} target="_blank" rel="noreferrer" className="ml-auto font-mono text-[11px] text-brand-300 hover:underline shrink-0">{c.location}</a>
                : <span className="ml-auto font-mono text-[11px] text-white/40 shrink-0">{c.location}</span>}
            </div>
          )
        })}
      </div>
    </Panel>
  )
}

function CommentPanel({ markdown, title = 'Generated review comment', hint, link }) {
  const [raw, setRaw] = useState(false)
  return (
    <Panel
      title={title}
      hint={hint}
      actions={
        <div className="flex items-center gap-2">
          {link && <ExtLink href={link}>On GitHub</ExtLink>}
          <Segmented value={raw ? 'raw' : 'rendered'} onChange={(k) => setRaw(k === 'raw')}
                     options={[{ key: 'rendered', label: 'Rendered' }, { key: 'raw', label: 'Raw' }]} />
        </div>
      }
      className="shrink-0"
      bodyClass="p-3"
    >
      {raw
        ? <pre className="code-block text-[11px] whitespace-pre-wrap leading-relaxed max-h-[32rem] overflow-y-auto">{markdown}</pre>
        : <div className="max-h-[32rem] overflow-y-auto pr-1"><Markdown>{markdown}</Markdown></div>}
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString() : ''
}
