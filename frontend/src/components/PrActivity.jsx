// frontend/src/components/PrActivity.jsx
// Dashboard widget: the latest GitHub App reviews across connected repositories,
// refreshed every 10 seconds. A row opens the review in the PR Bot "Live reviews"
// tab (?tab=live&review=<id>) of `projectId`; the PR number links to GitHub.
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Code2, GitPullRequest, Radio } from 'lucide-react'

import { githubApi } from '../services/api'
import { VerdictBadge, ago, useNow } from './live'

const POLL_MS = 10000

export default function PrActivity({ projectId, limit = 6 }) {
  const [data, setData] = useState({ reviews: null, configured: true, error: false })
  const navigate = useNavigate()
  const now = useNow(1000)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    const load = () => githubApi.reviews({ limit })
      .then((res) => alive.current && setData({ reviews: res.data.reviews, configured: res.data.app_configured, error: false }))
      .catch(() => alive.current && setData((d) => ({ ...d, reviews: d.reviews ?? [], error: true })))
    load()
    const t = setInterval(() => document.visibilityState === 'visible' && load(), POLL_MS)
    return () => { alive.current = false; clearInterval(t) }
  }, [limit])

  const { reviews, configured, error } = data
  const open = (id) => projectId && navigate(`/projects/${projectId}/pr-bot?tab=live${id ? `&review=${id}` : ''}`)
  const counts = (reviews || []).reduce((acc, r) => ({ ...acc, [r.verdict]: (acc[r.verdict] || 0) + 1 }), {})

  return (
    <section className="glass rounded-2xl p-5 mb-8 border border-white/5">
      <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Radio size={15} className="text-brand-400" />
          <h2 className="font-semibold text-white text-sm">Live compliance status</h2>
          <span className="text-[11px] text-white/35">latest pull-request reviews from the GitHub App</span>
          {!error && reviews && <span className="w-1.5 h-1.5 rounded-full bg-compliant animate-pulse" title="live" />}
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          {reviews?.length > 0 && (
            <>
              <span className="text-compliant">{counts.PASS || 0} pass</span>
              <span className="text-white/20">·</span>
              <span className="text-noncompliant">{counts.BLOCK || 0} blocked</span>
              <span className="text-white/20">·</span>
              <span className="text-partial">{(counts.WARN || 0) + (counts.SKIPPED || 0) + (counts.ERROR || 0)} other</span>
            </>
          )}
          {projectId && (
            <button onClick={() => open()} className="btn-secondary text-xs py-1 px-2.5 ml-2">
              <GitPullRequest size={12} /> All live reviews
            </button>
          )}
        </div>
      </div>

      {reviews == null ? (
        <div className="h-16 flex items-center justify-center"><span className="spinner" /></div>
      ) : reviews.length === 0 ? (
        <p className="text-xs text-white/40 py-3">
          {error ? 'The backend is not reachable.'
            : configured ? 'No pull request reviewed yet. Open a PR that changes the spec, the SRS or reviewed code.'
            : 'The GitHub App is not configured on this backend (docs/github-app-setup.md).'}
        </p>
      ) : (
        <div className="grid gap-1.5 md:grid-cols-2">
          {reviews.map((r) => {
            const reqs = [...new Set((r.violations || []).map((v) => v.requirement_id))]
            return (
              <div
                key={r.id} role="button" tabIndex={0}
                onClick={() => open(r.id)} onKeyDown={(e) => e.key === 'Enter' && open(r.id)}
                className={`flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-white/[0.04] transition-colors
                  ${projectId ? 'cursor-pointer' : ''}`}
              >
                <VerdictBadge verdict={r.verdict} />
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] text-white truncate">
                    <a href={r.pr_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                       className="font-mono text-white/55 hover:text-brand-300 hover:underline">
                      {r.repo.split('/').pop()}#{r.pr_number}
                    </a>{' '}
                    {r.pr_title || r.title}
                  </p>
                  <p className="text-[11px] text-white/35 truncate flex items-center gap-1.5">
                    {r.code_scan && <Code2 size={11} className="text-brand-300 shrink-0" />}
                    {reqs.length ? `regressed ${reqs.join(', ')}` : r.title}
                  </p>
                </div>
                <span className="text-[11px] text-white/35 shrink-0 tnum">{ago(r.created_at, now)}</span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
