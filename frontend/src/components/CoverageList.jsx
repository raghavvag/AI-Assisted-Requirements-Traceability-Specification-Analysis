// frontend/src/components/CoverageList.jsx
// ---------------------------------------------------------------------------
// The plain-language answer to the question the whole product exists to ask:
// "is every requirement actually implemented, and by what?"
//
// A heatmap shows all N x M scores but makes you decode colour. This shows the
// one row that matters per requirement — its best-matching endpoint — sorted
// weakest-first so the problems are at the top instead of buried in a grid.
// ---------------------------------------------------------------------------

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search } from 'lucide-react'
import { BandBadge, ScoreBar, bandOf } from './ui'

export default function CoverageList({
  requirements = [],
  endpoints = [],
  cells = [],
  threshold = 0.55,
  onExplain,
}) {
  const [openId, setOpenId] = useState(null)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('weakest') // 'weakest' | 'document'

  const rows = useMemo(() => {
    const byReq = new Map()
    cells.forEach((c) => {
      if (!byReq.has(c.req_id)) byReq.set(c.req_id, [])
      byReq.get(c.req_id).push(c)
    })
    const epById = new Map(endpoints.map((e) => [e.id, e]))

    return requirements.map((r, i) => {
      const matches = (byReq.get(r.id) || []).slice().sort((a, b) => b.score - a.score)
      const best = matches[0]
      return {
        ...r,
        order: i,
        best,
        bestEndpoint: best ? epById.get(best.ep_id) : null,
        score: best?.score ?? 0,
        top: matches.slice(0, 5),
        covered: (best?.score ?? 0) >= threshold,
      }
    })
  }, [requirements, endpoints, cells, threshold])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? rows.filter((r) =>
          r.id.toLowerCase().includes(q) ||
          (r.title || '').toLowerCase().includes(q) ||
          (r.best?.ep_id || '').toLowerCase().includes(q))
      : rows
    return filtered.slice().sort((a, b) =>
      sort === 'weakest' ? a.score - b.score : a.order - b.order)
  }, [rows, query, sort])

  const covered = rows.filter((r) => r.covered).length

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-white/5 shrink-0">
        <div className="relative flex-1 max-w-xs">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-white/25" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter requirements or endpoints…"
            className="w-full bg-white/5 border border-white/10 rounded-lg pl-7 pr-3 py-1.5
                       text-xs text-white placeholder-white/25 outline-none
                       focus:border-brand-400/50 focus:ring-1 focus:ring-brand-400/20 transition-all"
          />
        </div>
        <button
          onClick={() => setSort((s) => (s === 'weakest' ? 'document' : 'weakest'))}
          className="text-[11px] text-white/40 hover:text-white px-2 py-1.5 rounded-lg hover:bg-white/5 transition-colors whitespace-nowrap"
        >
          Sort: {sort === 'weakest' ? 'weakest first' : 'document order'}
        </button>
        <span className="text-[11px] text-white/30 tnum ml-auto whitespace-nowrap">
          {covered}/{rows.length} covered at ≥ {threshold}
        </span>
      </div>

      {/* Rows */}
      <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-white/5">
        {visible.length === 0 && (
          <p className="text-center text-white/35 text-sm py-10">No requirements match “{query}”.</p>
        )}

        {visible.map((r) => {
          const open = openId === r.id
          const band = bandOf(r.score, threshold)
          return (
            <div key={r.id}>
              <button
                onClick={() => setOpenId(open ? null : r.id)}
                className="w-full text-left px-4 py-3 hover:bg-white/[0.025] transition-colors flex items-start gap-3"
              >
                <span className="mt-0.5 text-white/25 shrink-0">
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </span>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="font-mono text-[11px] font-semibold text-brand-300">{r.id}</span>
                    <span className="text-[13px] text-white font-medium truncate">{r.title}</span>
                    <BandBadge score={r.score} threshold={threshold} />
                    {/* metadata carried over from formal SRS documents (.docx / .pdf) */}
                    {r.type === 'non_functional' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full ring-1 ring-white/10 bg-white/5 text-white/55">NFR</span>
                    )}
                    {r.priority && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full ring-1 ring-brand-400/25 bg-brand-400/10 text-brand-300"
                            title="Priority from the SRS">
                        {r.priority}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-white/40 font-mono truncate">
                    {r.best
                      ? <>best match · {r.best.ep_id}</>
                      : <span className="text-noncompliant">no endpoint scored against this requirement</span>}
                  </p>
                </div>

                <div className="w-40 shrink-0 pt-1.5">
                  <ScoreBar score={r.score} threshold={threshold} />
                </div>
              </button>

              {open && (
                <div className="px-4 pb-4 pl-11 bg-white/[0.015]">
                  <p className="text-xs text-white/50 leading-relaxed mb-3 max-w-3xl">{r.text}</p>

                  <p className="text-[10px] uppercase tracking-[0.14em] text-white/30 mb-1.5">
                    Top matching endpoints
                  </p>
                  <ul className="flex flex-col gap-1 mb-3">
                    {r.top.map((c) => (
                      <li key={c.ep_id} className="flex items-center gap-3">
                        <span className="font-mono text-[11px] text-white/60 truncate flex-1">{c.ep_id}</span>
                        <span className="w-36 shrink-0">
                          <ScoreBar score={c.score} threshold={threshold} />
                        </span>
                      </li>
                    ))}
                  </ul>

                  {onExplain && band !== 'compliant' && (
                    <button onClick={() => onExplain(r.id)} className="btn-secondary !py-1.5 !px-3 !text-xs">
                      Explain with AI
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
