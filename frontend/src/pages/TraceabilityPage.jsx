// frontend/src/pages/TraceabilityPage.jsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Play, RefreshCw, Network, Grid3x3, ListChecks, Columns2, Cpu, X, FileText } from 'lucide-react'
import { motion, AnimatePresence } from 'motion/react'
import toast from 'react-hot-toast'

import { analysisApi, apiError } from '../services/api'
import { SrsDrawer } from '../components/SrsPreview'
import Traceability3DGraph from '../components/Traceability3DGraph'
import ComplianceHeatmap from '../components/ComplianceHeatmap'
import CoverageList from '../components/CoverageList'
import {
  EmptyState, Loading, PageHeader, PageShell, Panel, Segmented, StatCard, StatGrid, bandOf,
} from '../components/ui'

const THRESHOLD = 0.55

const VIEWS = [
  { key: 'coverage', label: 'Coverage', icon: ListChecks },
  { key: 'graph',    label: 'Graph',    icon: Network },
  { key: 'heatmap',  label: 'Heatmap',  icon: Grid3x3 },
  { key: 'split',    label: 'Split',    icon: Columns2 },
]

/** Slide-over holding the LLM explanation for one requirement. */
function ExplainDrawer({ projectId, reqId, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })

  useEffect(() => {
    let alive = true
    setState({ loading: true, data: null, error: '' })
    analysisApi.explain(projectId, reqId)
      .then((res) => alive && setState({ loading: false, data: res.data, error: '' }))
      .catch((e) => alive && setState({
        loading: false, data: null,
        error: e.response?.data?.detail || 'Explanation unavailable.',
      }))
    return () => { alive = false }
  }, [projectId, reqId])

  return (
    <motion.aside
      initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
      transition={{ type: 'spring', stiffness: 320, damping: 34 }}
      className="fixed top-0 right-0 h-full w-[420px] max-w-[92vw] z-50 glass-strong
                 border-l border-white/10 p-6 overflow-y-auto"
    >
      <div className="flex items-start justify-between mb-5">
        <div>
          <p className="text-[10px] uppercase tracking-[0.14em] text-white/35 mb-1">AI root-cause analysis</p>
          <h3 className="font-bold text-white">{reqId}</h3>
        </div>
        <button onClick={onClose} className="text-white/40 hover:text-white"><X size={18} /></button>
      </div>

      {state.loading && (
        <div className="flex items-center gap-2 text-sm text-white/50">
          <Cpu size={15} className="animate-pulse text-brand-400" /> Asking the model…
        </div>
      )}
      {state.error && <p className="text-sm text-noncompliant">{state.error}</p>}
      {state.data && (
        <>
          <div className="glass rounded-xl p-3 mb-4 text-xs space-y-1.5">
            <div className="flex justify-between gap-3">
              <span className="text-white/40">Best endpoint</span>
              <span className="font-mono text-white/80 text-right break-all">{state.data.best_endpoint}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-white/40">Score</span>
              <span className="tnum text-white/80">{state.data.best_score?.toFixed(4)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-white/40">Category</span>
              <span className="text-white/80">{state.data.category}</span>
            </div>
          </div>
          <p className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap">
            {state.data.explanation}
          </p>
        </>
      )}
    </motion.aside>
  )
}

export default function TraceabilityPage() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [view, setView] = useState('coverage')
  const [explainReq, setExplainReq] = useState(null)
  const [showSrs, setShowSrs] = useState(false)

  const loadMatrix = useCallback(async () => {
    setLoading(true)
    try {
      const res = await analysisApi.matrix(id)
      setData(res.data)
    } catch (err) {
      if (err.response?.status !== 404) toast.error('Failed to load matrix.')
    } finally { setLoading(false) }
  }, [id])

  async function runAnalysis() {
    setRunning(true)
    const toastId = toast.loading('Running analysis pipeline… first run loads the model (~30–60s)')
    try {
      await analysisApi.analyze(id, { threshold: THRESHOLD, drift_threshold: -0.10, enable_drift: true })
      await loadMatrix()
      toast.success('Analysis complete', { id: toastId })
    } catch (err) {
      toast.error(apiError(err, 'Analysis failed.'), { id: toastId })
    } finally { setRunning(false) }
  }

  useEffect(() => { loadMatrix() }, [loadMatrix])

  const stats = useMemo(() => {
    if (!data) return null
    const reqs = data.requirements || []
    const eps = data.endpoints || []
    const cells = data.cells || []

    const bestByReq = new Map()
    cells.forEach((c) => {
      const cur = bestByReq.get(c.req_id)
      if (!cur || c.score > cur) bestByReq.set(c.req_id, c.score)
    })
    let covered = 0, partial = 0, uncovered = 0
    reqs.forEach((r) => {
      const b = bandOf(bestByReq.get(r.id) ?? 0, THRESHOLD)
      if (b === 'compliant') covered++
      else if (b === 'partial') partial++
      else uncovered++
    })
    const strongPairs = cells.filter((c) => c.score >= THRESHOLD).length
    return { reqs, eps, cells, covered, partial, uncovered, strongPairs }
  }, [data])

  const hasGraph = (data?.graph?.nodes?.length ?? 0) > 0

  return (
    <PageShell>
      <PageHeader
        title="Traceability Analysis"
        subtitle={`Semantic requirement-to-endpoint mapping · compliance threshold ${THRESHOLD}`}
      >
        <Segmented value={view} onChange={setView} options={VIEWS} />
        <button className="btn-secondary" onClick={() => setShowSrs(true)} title="Requirements extracted from the SRS">
          <FileText size={14} /> SRS
        </button>
        <button className="btn-secondary !px-3" onClick={loadMatrix} disabled={loading} title="Reload cached result">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
        <button className="btn-primary" onClick={runAnalysis} disabled={running || loading}>
          {running ? <span className="spinner" /> : <Play size={14} />}
          {running ? 'Analysing…' : 'Run Analysis'}
        </button>
      </PageHeader>

      {stats && (
        <StatGrid cols={4}>
          <StatCard
            label="Requirements covered"
            value={`${stats.covered}/${stats.reqs.length}`}
            hint={`${stats.partial} partial · ${stats.uncovered} not covered`}
            tone={stats.uncovered ? 'bad' : stats.partial ? 'warn' : 'good'}
          />
          <StatCard
            label="Overall compliance"
            value={data?.overall_compliance != null ? `${(data.overall_compliance * 100).toFixed(1)}%` : '—'}
            hint="requirements with ≥1 matching endpoint"
            tone={data?.overall_compliance >= 0.9 ? 'good' : data?.overall_compliance >= 0.6 ? 'warn' : 'bad'}
          />
          <StatCard
            label="Endpoints analysed"
            value={stats.eps.length}
            hint={`${stats.reqs.length} × ${stats.eps.length} = ${stats.cells.length} pairs scored`}
            tone="info"
          />
          <StatCard
            label="Strong matches"
            value={stats.strongPairs}
            hint={`pairs scoring ≥ ${THRESHOLD}`}
            tone="info"
          />
        </StatGrid>
      )}

      {/* Main surface */}
      {loading && !data ? (
        <Panel fill><Loading label="Loading analysis…" /></Panel>
      ) : !data ? (
        <Panel fill>
          <EmptyState
            icon={Cpu}
            title="No analysis yet"
            description="Embed your requirements and API spec to build the traceability matrix. The first run loads the transformer model and takes about a minute."
            action={
              <button className="btn-primary" onClick={runAnalysis} disabled={running}>
                {running ? <span className="spinner" /> : <Play size={14} />}
                {running ? 'Analysing…' : 'Run Analysis'}
              </button>
            }
          />
        </Panel>
      ) : view === 'coverage' ? (
        <Panel
          fill
          title="Requirement coverage"
          hint="Each requirement and its best-matching endpoint. Weakest first — problems surface at the top."
        >
          <CoverageList
            requirements={stats.reqs}
            endpoints={stats.eps}
            cells={stats.cells}
            threshold={THRESHOLD}
            onExplain={setExplainReq}
          />
        </Panel>
      ) : view === 'graph' ? (
        <Panel fill title="Traceability graph" hint="Requirements linked to the endpoints that implement them" bodyClass="relative">
          {hasGraph
            ? <Traceability3DGraph nodes={data.graph.nodes} edges={data.graph.edges} threshold={THRESHOLD} />
            : <EmptyState icon={Network} title="No graph payload" description="Re-run the analysis to generate the graph." />}
        </Panel>
      ) : view === 'heatmap' ? (
        <Panel
          fill
          title="Similarity matrix"
          hint="Every requirement × endpoint cosine score. Hover a cell for the exact value."
          bodyClass="p-3"
        >
          <ComplianceHeatmap
            requirements={stats.reqs}
            endpoints={stats.eps}
            cells={stats.cells}
            threshold={THRESHOLD}
          />
        </Panel>
      ) : (
        // Split: the graph gives shape, the coverage list gives specifics.
        // Pairing the graph with the heatmap (the old behaviour) put two
        // space-hungry visuals in half-width panes and made both unreadable.
        <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4">
          {/* `fill` here is for min-height:0 — grid items default to min-height:auto,
              which stops the inner h-full canvas from ever shrinking to fit. */}
          <Panel fill title="Traceability graph" hint="Click a node to focus its links" bodyClass="relative">
            {hasGraph
              ? <Traceability3DGraph nodes={data.graph.nodes} edges={data.graph.edges} threshold={THRESHOLD} />
              : <EmptyState icon={Network} title="No graph payload" />}
          </Panel>
          <Panel fill title="Requirement coverage" hint="Weakest first">
            <CoverageList
              requirements={stats.reqs}
              endpoints={stats.eps}
              cells={stats.cells}
              threshold={THRESHOLD}
              onExplain={setExplainReq}
            />
          </Panel>
        </div>
      )}

      <AnimatePresence>
        {explainReq && (
          <>
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setExplainReq(null)}
              className="fixed inset-0 bg-black/55 z-40"
            />
            <ExplainDrawer projectId={id} reqId={explainReq} onClose={() => setExplainReq(null)} />
          </>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showSrs && (
          <>
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setShowSrs(false)}
              className="fixed inset-0 bg-black/55 z-40"
            />
            <SrsDrawer projectId={id} onClose={() => setShowSrs(false)} />
          </>
        )}
      </AnimatePresence>
    </PageShell>
  )
}
