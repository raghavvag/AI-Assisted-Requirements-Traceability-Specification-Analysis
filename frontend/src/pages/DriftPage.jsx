// frontend/src/pages/DriftPage.jsx
// ---------------------------------------------------------------------------
// Version-over-version compliance regression.
//
// Layout bug fixed here: the timeline chart lived in a flex child with only an
// inline `height:220`. Flex children shrink by default, so the box collapsed
// while Plotly's SVG kept its measured height — the chart then painted straight
// over the drifted-pairs list below it. Chart wrappers are now `shrink-0` with
// `overflow-hidden`, which is the actual fix.
// ---------------------------------------------------------------------------

import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { AlertTriangle, ChevronDown, ChevronUp, Cpu, TrendingDown } from 'lucide-react'
import Plot from 'react-plotly.js'
import toast from 'react-hot-toast'

import { analysisApi } from '../services/api'
import {
  EmptyState, Loading, PageHeader, PageShell, Panel, Segmented,
  StatCard, StatGrid, PLOT_CONFIG, PLOT_THEME,
} from '../components/ui'

const SEVERITY = {
  'Critical Drift': { rank: 0, tone: 'bad',  dot: '#fb7185', label: 'Critical' },
  'Major Drift':    { rank: 1, tone: 'bad',  dot: '#fb7185', label: 'Major' },
  'Minor Drift':    { rank: 2, tone: 'warn', dot: '#fbbf24', label: 'Minor' },
  'Stable':         { rank: 3, tone: 'good', dot: '#34d399', label: 'Stable' },
  'No Drift':       { rank: 4, tone: 'good', dot: '#34d399', label: 'No drift' },
}
const sevOf = (s) => SEVERITY[s] || SEVERITY['Minor Drift']

const TONE_CLASS = {
  bad:  { ring: 'border-noncompliant/25', chip: 'bg-noncompliant/12 text-noncompliant ring-noncompliant/25', icon: 'bg-noncompliant/15 text-noncompliant' },
  warn: { ring: 'border-partial/25',      chip: 'bg-partial/12 text-partial ring-partial/25',                icon: 'bg-partial/15 text-partial' },
  good: { ring: 'border-compliant/25',    chip: 'bg-compliant/12 text-compliant ring-compliant/25',          icon: 'bg-compliant/15 text-compliant' },
}

function DriftRow({ pair, projectId }) {
  const [open, setOpen] = useState(false)
  const [explain, setExplain] = useState('')
  const [loading, setLoading] = useState(false)

  const sev = sevOf(pair.severity)
  const cls = TONE_CLASS[sev.tone]
  const removed = pair.endpoint_status === 'removed' || pair.score_v2 === 0

  async function toggle() {
    if (open) { setOpen(false); return }
    setOpen(true)
    if (explain) return
    setLoading(true)
    try {
      const res = await analysisApi.explain(projectId, pair.requirement_id)
      setExplain(res.data.explanation || 'No explanation returned.')
    } catch (e) {
      setExplain(e.response?.data?.detail || 'LLM explanation unavailable — set OPENAI_API_KEY in backend/.env.')
    } finally { setLoading(false) }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      className={`glass rounded-xl border ${cls.ring} overflow-hidden`}
    >
      <button onClick={toggle} className="w-full text-left p-3.5 flex items-center gap-3 hover:bg-white/[0.025] transition-colors">
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${cls.icon}`}>
          <AlertTriangle size={16} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 ${cls.chip}`}>
              {sev.label}
            </span>
            <span className="font-mono text-[11px] text-brand-300">{pair.requirement_id}</span>
            {removed && (
              <span className="text-[10px] uppercase tracking-wider text-noncompliant/80 font-semibold">
                endpoint removed in v2
              </span>
            )}
          </div>
          <p className="text-[13px] text-white/85 font-mono truncate">{pair.endpoint_id}</p>
          {pair.requirement_title && (
            <p className="text-[11px] text-white/35 truncate mt-0.5">{pair.requirement_title}</p>
          )}
        </div>

        {/* v1 -> v2 -> delta, aligned so the eye can scan the column */}
        <div className="hidden sm:flex items-center gap-3 shrink-0 tnum">
          <div className="text-center w-14">
            <p className="text-sm font-bold text-compliant">{pair.score_v1?.toFixed(3)}</p>
            <p className="text-[9px] text-white/30 uppercase tracking-wider">v1</p>
          </div>
          <span className="text-white/20">→</span>
          <div className="text-center w-14">
            <p className={`text-sm font-bold ${pair.score_v2 === 0 ? 'text-noncompliant' : 'text-white/70'}`}>
              {pair.score_v2?.toFixed(3)}
            </p>
            <p className="text-[9px] text-white/30 uppercase tracking-wider">v2</p>
          </div>
          <div className="text-center w-16">
            <p className={`text-sm font-bold ${pair.delta < 0 ? 'text-noncompliant' : 'text-compliant'}`}>
              {pair.delta >= 0 ? '+' : ''}{pair.delta?.toFixed(3)}
            </p>
            <p className="text-[9px] text-white/30 uppercase tracking-wider">Δ</p>
          </div>
        </div>

        <span className="text-white/25 shrink-0">{open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</span>
      </button>

      {open && (
        <div className="border-t border-white/5 px-4 py-3 bg-white/[0.015]">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-white/50">
              <Cpu size={13} className="animate-pulse text-brand-400" /> Generating root-cause analysis…
            </div>
          ) : (
            <p className="text-xs text-white/70 whitespace-pre-wrap leading-relaxed">{explain}</p>
          )}
        </div>
      )}
    </motion.div>
  )
}

export default function DriftPage() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    let alive = true
    analysisApi.drift(id)
      .then((res) => alive && setData(res.data))
      .catch(() => {})
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [id])

  const model = useMemo(() => {
    if (!data || data.message) return null
    const pairs = data.pairs || []
    const drifted = pairs
      .filter((p) => p.drifted)
      .sort((a, b) => sevOf(a.severity).rank - sevOf(b.severity).rank || a.delta - b.delta)

    const counts = {
      all: drifted.length,
      critical: drifted.filter((p) => p.severity === 'Critical Drift').length,
      major: drifted.filter((p) => p.severity === 'Major Drift').length,
      minor: drifted.filter((p) => p.severity === 'Minor Drift').length,
    }
    // Requirements that lost their last compliant endpoint — the headline risk.
    const affectedReqs = [...new Set(drifted.map((p) => p.requirement_id))]
    return { pairs, drifted, counts, affectedReqs }
  }, [data])

  if (loading) return <PageShell><Loading label="Loading drift report…" /></PageShell>

  if (!model) return (
    <PageShell>
      <PageHeader title="Drift Analysis" subtitle="Version-over-version compliance regression" />
      <Panel fill>
        <EmptyState
          icon={TrendingDown}
          title="No drift data"
          description="Upload an OpenAPI v2 spec and re-run the analysis with drift enabled to compare versions."
        />
      </Panel>
    </PageShell>
  )

  const { drifted, counts, affectedReqs } = model
  const v1 = data.compliance_v1 ?? 0
  const v2 = data.compliance_v2 ?? 0
  const deltaPct = (v2 - v1) * 100

  const filtered = filter === 'all'
    ? drifted
    : drifted.filter((p) => sevOf(p.severity).label.toLowerCase() === filter)

  return (
    <PageShell scroll>
      <PageHeader
        title="Drift Analysis"
        subtitle="What the v2 specification changed about requirement coverage"
      />

      <StatGrid cols={4}>
        <StatCard label="Compliance v1.0" value={`${(v1 * 100).toFixed(1)}%`} hint="baseline" tone="good" />
        <StatCard
          label="Compliance v2.0"
          value={`${(v2 * 100).toFixed(1)}%`}
          hint={`${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)} pts vs baseline`}
          tone={deltaPct < 0 ? 'bad' : 'good'}
        />
        <StatCard
          label="Drifted pairs"
          value={counts.all}
          hint={`across ${affectedReqs.length} requirement${affectedReqs.length === 1 ? '' : 's'}`}
          tone={counts.all ? 'warn' : 'good'}
        />
        <StatCard
          label="Critical drift"
          value={data.n_critical ?? counts.critical}
          hint="requires review before merge"
          tone={(data.n_critical ?? counts.critical) ? 'bad' : 'good'}
        />
      </StatGrid>

      {/* Chart wrapper: shrink-0 + overflow-hidden — see the header comment. */}
      <Panel
        title="Compliance across versions"
        hint={deltaPct < 0
          ? `Coverage fell ${Math.abs(deltaPct).toFixed(1)} points between v1.0 and v2.0`
          : 'Coverage held or improved between versions'}
        className="shrink-0"
        bodyClass="overflow-hidden"
      >
        <div style={{ height: 190 }} className="w-full overflow-hidden">
          <Plot
            data={[{
              type: 'scatter',
              mode: 'lines+markers+text',
              x: ['v1.0 (baseline)', 'v2.0 (current)'],
              y: [v1, v2],
              text: [`${(v1 * 100).toFixed(1)}%`, `${(v2 * 100).toFixed(1)}%`],
              textposition: 'top center',
              textfont: { size: 12, color: '#ffffff' },
              line: { color: '#22d3ee', width: 2.5, shape: 'spline' },
              marker: {
                size: 13,
                color: [ '#34d399', deltaPct < 0 ? '#fb7185' : '#34d399' ],
                line: { width: 2, color: '#050506' },
              },
              hovertemplate: '%{x}<br>Compliance <b>%{y:.1%}</b><extra></extra>',
            }]}
            layout={{
              ...PLOT_THEME,
              yaxis: {
                range: [0, 1.18], tickformat: '.0%',
                gridcolor: 'rgba(255,255,255,0.05)', zeroline: false,
                tickfont: { size: 10, color: 'rgba(255,255,255,0.4)' },
              },
              xaxis: { showgrid: false, tickfont: { size: 11, color: 'rgba(255,255,255,0.5)' } },
              margin: { l: 46, r: 20, t: 22, b: 34 },
              showlegend: false,
            }}
            config={PLOT_CONFIG}
            style={{ width: '100%', height: '100%' }}
            useResizeHandler
          />
        </div>
      </Panel>

      <Panel
        title={`Drifted pairs (${drifted.length})`}
        hint="Sorted by severity. Click a row for AI root-cause analysis."
        actions={
          <Segmented
            value={filter}
            onChange={setFilter}
            options={[
              { key: 'all', label: `All ${counts.all}` },
              ...(counts.critical ? [{ key: 'critical', label: `Critical ${counts.critical}` }] : []),
              ...(counts.major ? [{ key: 'major', label: `Major ${counts.major}` }] : []),
              ...(counts.minor ? [{ key: 'minor', label: `Minor ${counts.minor}` }] : []),
            ]}
          />
        }
        bodyClass="p-3"
        className="shrink-0"
      >
        {filtered.length === 0 ? (
          <p className="text-center text-white/40 text-sm py-10">
            No {filter === 'all' ? '' : `${filter} `}drift detected — coverage is stable.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {filtered.map((pair, i) => (
              <DriftRow key={`${pair.requirement_id}-${pair.endpoint_id}-${i}`} pair={pair} projectId={id} />
            ))}
          </div>
        )}
      </Panel>
    </PageShell>
  )
}
