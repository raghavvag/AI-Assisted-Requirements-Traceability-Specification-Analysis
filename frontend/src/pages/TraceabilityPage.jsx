// frontend/src/pages/TraceabilityPage.jsx
// Split view: 3D Force Graph (left) + Plotly heatmap (right)
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { Play, Cpu, RefreshCw, Layers } from 'lucide-react'
import Plot from 'react-plotly.js'
import { analysisApi } from '../services/api'
import Traceability3DGraph from '../components/Traceability3DGraph'
import toast from 'react-hot-toast'

function MetricBadge({ label, value, color = 'blue' }) {
  const colors = {
    blue:  'bg-blue-500/10  text-blue-400  border-blue-500/20',
    green: 'bg-green-500/10 text-green-400 border-green-500/20',
    amber: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    red:   'bg-red-500/10   text-red-400   border-red-500/20',
  }
  return (
    <div className={`metric-card border ${colors[color]}`}>
      <p className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold">{value}</p>
    </div>
  )
}

export default function TraceabilityPage() {
  const { id }   = useParams()
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [view,    setView]    = useState('graph')  // 'graph' | 'heatmap' | 'split'

  async function loadMatrix() {
    setLoading(true)
    try {
      const res = await analysisApi.matrix(id)
      setData(res.data)
    } catch (err) {
      if (err.response?.status !== 404) toast.error('Failed to load matrix.')
    } finally { setLoading(false) }
  }

  async function runAnalysis() {
    setRunning(true)
    const toastId = toast.loading('Running analysis pipeline…')
    try {
      await analysisApi.analyze(id, { threshold: 0.55, drift_threshold: -0.10, enable_drift: true })
      await loadMatrix()
      toast.success('Analysis complete!', { id: toastId })
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Analysis failed.', { id: toastId })
    } finally { setRunning(false) }
  }

  useEffect(() => { loadMatrix() }, [id])

  // Build Plotly heatmap
  const heatmapData = data ? (() => {
    const reqIds = data.requirements?.map(r => r.id) || []
    const epIds  = data.endpoints?.map(e => e.id)   || []
    const z      = reqIds.map(rid => epIds.map(eid => {
      const cell = data.cells?.find(c => c.req_id === rid && c.ep_id === eid)
      return cell?.score ?? 0
    }))
    const shortEp = epIds.map(ep => ep.split('/').pop() || ep)
    return { z, x: shortEp, y: reqIds }
  })() : null

  const compliance = data?.overall_compliance
  const n_req      = data?.requirements?.length ?? 0
  const n_ep       = data?.endpoints?.length ?? 0
  const n_comp     = data?.cells?.filter(c => c.category === 'Compliant')?.length ?? 0

  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Header */}
      <motion.div initial={{ opacity:0 }} animate={{ opacity:1 }}
        className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold gradient-text">Traceability Analysis</h1>
          <p className="text-sm text-slate-400">Semantic requirement-to-endpoint mapping</p>
        </div>
        <div className="flex items-center gap-3">
          {/* View toggle */}
          <div className="glass rounded-xl p-1 flex gap-1">
            {[['graph','3D Graph'],['heatmap','Heatmap'],['split','Split']].map(([key,label]) => (
              <button key={key} onClick={() => setView(key)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all
                  ${view === key ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                {label}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={loadMatrix} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button className="btn-primary" onClick={runAnalysis} disabled={running || loading}>
            {running ? <span className="spinner" /> : <Play size={14} />}
            {running ? 'Analysing…' : 'Run Analysis'}
          </button>
        </div>
      </motion.div>

      {/* Metrics */}
      {data && (
        <motion.div initial={{ opacity:0, y:10 }} animate={{ opacity:1, y:0 }}
          className="grid grid-cols-4 gap-4">
          <MetricBadge label="Overall Compliance"  value={compliance ? `${(compliance*100).toFixed(1)}%` : '—'} color={compliance > 0.7 ? 'green' : compliance > 0.4 ? 'amber' : 'red'} />
          <MetricBadge label="Requirements"        value={n_req} color="blue" />
          <MetricBadge label="Endpoints"           value={n_ep}  color="blue" />
          <MetricBadge label="Compliant Pairs"     value={n_comp} color="green" />
        </motion.div>
      )}

      {/* Visualisation */}
      <div className="flex-1 glass rounded-2xl overflow-hidden min-h-0">
        {loading && !data ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <span className="spinner" style={{width:48,height:48}} />
              <p className="text-slate-400 text-sm mt-4">Loading analysis…</p>
            </div>
          </div>
        ) : !data ? (
          <div className="flex items-center justify-center h-full text-slate-500">
            <div className="text-center">
              <Cpu size={48} className="mx-auto mb-4 text-brand-600/40 animate-pulse" />
              <p className="text-lg font-medium text-white mb-2">No analysis yet</p>
              <p className="text-sm mb-4">Click "Run Analysis" to embed and map your requirements.</p>
            </div>
          </div>
        ) : (
          <div className={`h-full ${view === 'split' ? 'flex' : ''}`}>
            {/* 3D Graph */}
            {(view === 'graph' || view === 'split') && (
              <div className={view === 'split' ? 'w-1/2 h-full border-r border-white/5' : 'w-full h-full'}>
                <Traceability3DGraph
                  nodes={data.graph?.nodes || []}
                  edges={data.graph?.edges || []}
                />
              </div>
            )}

            {/* Plotly Heatmap */}
            {(view === 'heatmap' || view === 'split') && heatmapData && (
              <div className={view === 'split' ? 'w-1/2 h-full p-4' : 'w-full h-full p-4'}>
                <Plot
                  data={[{
                    type: 'heatmap',
                    z: heatmapData.z, x: heatmapData.x, y: heatmapData.y,
                    colorscale: [[0,'#1e1e2e'],[0.35,'#d97706'],[0.55,'#16a34a'],[1,'#22c55e']],
                    zmin: 0, zmax: 1,
                    hoverongaps: false,
                    text: heatmapData.z.map(row => row.map(v => v.toFixed(3))),
                    texttemplate: '%{text}', textfont: { size: 8 },
                    colorbar: { title: 'Score', titlefont: { color:'#94a3b8' }, tickfont: { color:'#94a3b8' } },
                  }]}
                  layout={{
                    title: { text: 'Requirement × Endpoint Cosine Similarity', font: { color:'#e2e8f0' } },
                    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
                    font: { color: '#94a3b8', size: 10 },
                    xaxis: { tickangle: -45, color:'#94a3b8', gridcolor:'rgba(255,255,255,0.05)' },
                    yaxis: { color:'#94a3b8', gridcolor:'rgba(255,255,255,0.05)' },
                    margin: { l:80, r:40, t:50, b:120 },
                    autosize: true,
                  }}
                  config={{ responsive: true, displayModeBar: false }}
                  style={{ width:'100%', height:'100%' }}
                  useResizeHandler
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
