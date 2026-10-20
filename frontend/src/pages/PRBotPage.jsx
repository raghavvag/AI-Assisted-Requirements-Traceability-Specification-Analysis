// frontend/src/pages/PRBotPage.jsx
// Two tabs: "Simulated" runs the built-in PR scenarios through the review
// engine; "Live reviews" lists what the GitHub App posted on real pull requests
// (PRReviewRun rows from GET /api/github/reviews).
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import {
  AlertTriangle, CheckCircle2, ExternalLink, FlaskConical, GitPullRequest, Inbox, MinusCircle,
  Play, Radio, RefreshCw, ShieldAlert, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'

import { analysisApi, apiError, githubApi } from '../services/api'
import { EmptyState, Loading, PageHeader, PageShell, Panel, Segmented, StatCard, StatGrid } from '../components/ui'

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

// PASS / WARN / BLOCK come from the engine; SKIPPED / ERROR from the GitHub App flow.
const VERDICTS = {
  PASS:    { label: 'Pass',    icon: CheckCircle2,  text: 'text-compliant',    ring: 'bg-compliant/12 ring-compliant/25' },
  WARN:    { label: 'Warn',    icon: AlertTriangle, text: 'text-partial',      ring: 'bg-partial/12 ring-partial/25' },
  BLOCK:   { label: 'Block',   icon: XCircle,       text: 'text-noncompliant', ring: 'bg-noncompliant/12 ring-noncompliant/25' },
  SKIPPED: { label: 'Skipped', icon: MinusCircle,   text: 'text-white/50',     ring: 'bg-white/5 ring-white/10' },
  ERROR:   { label: 'Error',   icon: AlertTriangle, text: 'text-noncompliant', ring: 'bg-noncompliant/12 ring-noncompliant/25' },
}

export default function PRBotPage() {
  const [tab, setTab] = useState('simulated')
  return (
    <PageShell scroll>
      <PageHeader
        title="PR Compliance Bot"
        subtitle={tab === 'simulated'
          ? 'Simulated CI review — re-scores changed endpoints against the requirement baseline'
          : 'Reviews the GitHub App posted on real pull requests'}
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

function LiveReviews() {
  const [reviews, setReviews] = useState(null)
  const [configured, setConfigured] = useState(true)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)        // id
  const [detail, setDetail] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await githubApi.reviews({ limit: 100 })
      setReviews(res.data.reviews)
      setConfigured(res.data.app_configured)
    } catch (err) {
      toast.error(apiError(err, 'Could not load live reviews.'))
      setReviews([])
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (selected == null) { setDetail(null); return }
    let cancelled = false
    githubApi.review(selected)
      .then((res) => { if (!cancelled) setDetail(res.data) })
      .catch((err) => { if (!cancelled) toast.error(apiError(err, 'Could not load the review.')) })
    return () => { cancelled = true }
  }, [selected])

  if (loading && reviews == null) return <Loading label="Loading live reviews…" />

  if (!reviews?.length) {
    return (
      <Panel title="Live reviews" className="shrink-0" bodyClass="p-6">
        <EmptyState
          icon={Inbox}
          title="No live reviews yet"
          description={configured
            ? 'Open or push to a pull request that changes the spec or SRS named in .traceability.yml. The bot reviews it and the run shows up here.'
            : 'The GitHub App is not configured on this backend. Set GITHUB_APP_ID, the private key and GITHUB_WEBHOOK_SECRET in backend/.env (manual.md section D).'}
          action={<button className="btn-secondary" onClick={load}><RefreshCw size={14} /> Refresh</button>}
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
        <StatCard label="Skipped / errors" value={(counts.SKIPPED || 0) + (counts.ERROR || 0)} hint="no spec change, no config, or a failure" tone="warn" />
      </StatGrid>

      <Panel
        title={`Pull request reviews (${reviews.length})`}
        hint="Newest first. Select a row for the violations and the posted comment."
        actions={
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        }
        className="shrink-0"
        bodyClass="p-2"
      >
        <div className="flex flex-col">
          {reviews.map((r) => (
            <button
              key={r.id}
              onClick={() => setSelected(selected === r.id ? null : r.id)}
              className={`text-left px-3 py-2.5 rounded-xl flex items-center gap-3 transition-colors
                ${selected === r.id ? 'bg-brand-400/8 ring-1 ring-brand-400/20' : 'hover:bg-white/[0.03]'}`}
            >
              <VerdictBadge verdict={r.verdict} />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] text-white truncate">
                  <span className="font-mono text-white/45">{r.repo}#{r.pr_number}</span>{' '}
                  {r.pr_title || r.title}
                </p>
                <p className="text-[11px] text-white/40 truncate">
                  {r.title}
                  {r.violations?.length > 0 && ` · ${[...new Set(r.violations.map((v) => v.requirement_id))].join(', ')}`}
                </p>
              </div>
              <span className="text-[11px] font-mono text-white/30 shrink-0 hidden md:block">{r.head_sha?.slice(0, 7)}</span>
              <span className="text-[11px] text-white/35 shrink-0 w-20 text-right" title={r.created_at}>
                {timeAgo(r.created_at)}
              </span>
            </button>
          ))}
        </div>
      </Panel>

      <AnimatePresence mode="wait">
        {detail && detail.id === selected && (
          <motion.div
            key={detail.id}
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="flex flex-col gap-4 shrink-0"
          >
            <LiveDetailHeader review={detail} />
            <ViolationStats violations={detail.violations} />
            <ViolationList violations={detail.violations} />
            {detail.markdown
              ? <CommentPanel markdown={detail.markdown} hint="The comment the bot posted (updated on every push)" />
              : detail.summary && <CommentPanel markdown={detail.summary} title="Check run summary" hint={detail.title} />}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function LiveDetailHeader({ review: r }) {
  const v = VERDICTS[r.verdict] || VERDICTS.SKIPPED
  const Icon = v.icon
  return (
    <div className={`rounded-2xl border p-4 flex items-center gap-4 shrink-0 ring-1 ${v.ring} border-transparent`}>
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${v.ring} ${v.text}`}>
        <Icon size={22} />
      </div>
      <div className="min-w-0">
        <p className={`font-bold text-[15px] ${v.text}`}>{r.title}</p>
        <p className="text-xs text-white/50 mt-0.5 truncate">
          {r.repo}#{r.pr_number} {r.pr_title && `· ${r.pr_title}`} {r.pr_author && `· @${r.pr_author}`}
          {r.linked_issues?.length > 0 && ` · closes ${r.linked_issues.map((i) => `#${i.number}`).join(', ')}`}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2 shrink-0">
        {r.pr_url && <ExtLink href={r.pr_url}>Pull request</ExtLink>}
        {r.check_run_url && <ExtLink href={r.check_run_url}>Check run</ExtLink>}
      </div>
    </div>
  )
}

function ExtLink({ href, children }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
      <ExternalLink size={12} /> {children}
    </a>
  )
}

function VerdictBadge({ verdict }) {
  const v = VERDICTS[verdict] || VERDICTS.SKIPPED
  return (
    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 w-[68px] text-center shrink-0 ${v.ring} ${v.text}`}>
      {v.label}
    </span>
  )
}

function timeAgo(iso) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
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
      <StatCard label="Violations" value={violations.length} hint="requirements that regressed" tone="bad" />
      <StatCard
        label="Largest drop"
        value={Math.min(...violations.map((v) => v.delta)).toFixed(3)}
        hint="cosine delta v1 → v2"
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

function ViolationList({ violations = [] }) {
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
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-noncompliant/12 text-noncompliant ring-1 ring-noncompliant/25">
                {v.severity}
              </span>
              <span className="font-mono text-[11px] text-brand-300">{v.requirement_id}</span>
              <span className="ml-auto flex items-center gap-3 text-xs tnum">
                <span className="text-compliant">{v.score_v1?.toFixed(3)}</span>
                <span className="text-white/20">→</span>
                <span className="text-white/60">{v.score_v2?.toFixed(3)}</span>
                <span className="text-noncompliant font-semibold">{v.delta?.toFixed(3)}</span>
              </span>
            </div>
            <p className="text-[13px] font-mono text-white/85 mb-2 break-all">{v.endpoint_id}</p>
            {v.explanation && (
              <p className="text-xs text-white/60 leading-relaxed whitespace-pre-wrap border-t border-white/5 pt-2.5">
                {v.explanation}
              </p>
            )}
          </div>
        ))}
      </div>
    </Panel>
  )
}

function CommentPanel({ markdown, title = 'Generated review comment', hint }) {
  return (
    <Panel title={title} hint={hint} className="shrink-0" bodyClass="p-3">
      <pre className="code-block text-[11px] whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
        {markdown}
      </pre>
    </Panel>
  )
}
