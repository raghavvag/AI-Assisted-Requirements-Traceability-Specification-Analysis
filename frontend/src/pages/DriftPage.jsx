// frontend/src/pages/DriftPage.jsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { TrendingDown, AlertTriangle, ChevronDown, ChevronUp, Cpu } from 'lucide-react'
import Plot from 'react-plotly.js'
import { analysisApi } from '../services/api'
import toast from 'react-hot-toast'

const SEVERITY_BADGE = {
  'Critical Drift': 'badge-noncompliant',
  'Major Drift':    'badge-noncompliant',
  'Minor Drift':    'badge-partial',
  'Stable':         'badge-compliant',
  'No Drift':       'badge-compliant',
}

function DriftCard({ pair, projectId }) {
  const [open,    setOpen]    = useState(false)
  const [explain, setExplain] = useState('')
  const [loading, setLoading] = useState(false)

  async function loadExplanation() {
    if (explain) { setOpen(!open); return }
    setLoading(true); setOpen(true)
    try {
      const res = await analysisApi.explain(projectId, pair.requirement_id)
      setExplain(res.data.explanation || 'No explanation returned.')
    } catch {
      setExplain('LLM explanation unavailable. Set GROQ_API_KEY in backend .env.')
    } finally { setLoading(false) }
  }

  const isCritical = ['Critical Drift','Major Drift'].includes(pair.severity)

  return (
    <motion.div
      initial={{ opacity:0, y:10 }} animate={{ opacity:1, y:0 }}
      className={`glass rounded-xl border overflow-hidden
        ${isCritical ? 'border-red-500/20' : 'border-amber-500/20'}`}
    >
      <div className="p-4 flex items-center gap-4 cursor-pointer" onClick={loadExplanation}>
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0
          ${isCritical ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-400'}`}>
          <AlertTriangle size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={SEVERITY_BADGE[pair.severity] || 'badge-partial'}>{pair.severity}</span>
            <span className="text-xs text-slate-500 font-mono">{pair.requirement_id}</span>
          </div>
          <p className="text-sm text-white font-medium truncate">{pair.endpoint_id}</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-400 shrink-0">
          <div className="text-center">
            <p className="text-green-400 font-bold">{pair.score_v1?.toFixed(3)}</p>
            <p className="text-[10px] text-slate-600">v1.0</p>
          </div>
          <div className="text-slate-600">→</div>
          <div className="text-center">
            <p className="text-red-400 font-bold">{pair.score_v2?.toFixed(3)}</p>
            <p className="text-[10px] text-slate-600">v2.0</p>
          </div>
          <div className="text-center">
            <p className={`font-bold ${pair.delta < 0 ? 'text-red-400' : 'text-green-400'}`}>
              {pair.delta >= 0 ? '+' : ''}{pair.delta?.toFixed(3)}
            </p>
            <p className="text-[10px] text-slate-600">Δ</p>
          </div>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </div>

      {open && (
        <div className="border-t border-white/5 px-4 py-3">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Cpu size={14} className="animate-pulse" /> Calling LLM...
            </div>
          ) : (
            <div className="text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">
              {explain}
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}

export default function DriftPage() {
  const { id } = useParams()
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    analysisApi.drift(id)
      .then(res => setData(res.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <span className="spinner" style={{width:48,height:48}} />
    </div>
  )

  if (!data || data.message) return (
    <div className="flex items-center justify-center h-full text-slate-500">
      <div className="text-center">
        <TrendingDown size={48} className="mx-auto mb-4 text-brand-600/40" />
        <p className="text-lg font-medium text-white mb-2">No drift data</p>
        <p className="text-sm">Run analysis with an OpenAPI v2 spec to detect version drift.</p>
      </div>
    </div>
  )

  const pairs      = data.pairs     || []
  const drifted    = pairs.filter(p => p.drifted)
  const v1Score    = (data.compliance_v1 * 100).toFixed(1)
  const v2Score    = (data.compliance_v2 * 100).toFixed(1)
  const delta      = (data.compliance_v2 - data.compliance_v1) * 100

  return (
    <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
      <motion.div initial={{ opacity:0 }} animate={{ opacity:1 }}>
        <h1 className="text-2xl font-bold gradient-text">Drift Analysis</h1>
        <p className="text-sm text-slate-400">Version-over-version compliance regression tracking</p>
      </motion.div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label:'Compliance v1.0', value:`${v1Score}%`, color:'text-green-400' },
          { label:'Compliance v2.0', value:`${v2Score}%`, color: delta < 0 ? 'text-red-400':'text-green-400' },
          { label:'Drifted Pairs',   value:data.n_drifted, color:'text-amber-400' },
          { label:'Critical Drift',  value:data.n_critical, color:'text-red-400' },
        ].map(({ label,value,color }) => (
          <div key={label} className="metric-card">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</p>
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* Timeline chart */}
      <div className="glass rounded-2xl p-4" style={{height:220}}>
        <Plot
          data={[{
            type: 'scatter', mode: 'lines+markers+text',
            x: ['v1.0 (Baseline)', 'v2.0 (Current)'],
            y: [data.compliance_v1, data.compliance_v2],
            text: [`${v1Score}%`, `${v2Score}%`],
            textposition: 'top center',
            line: { color:'#3b82f6', width:3 },
            marker: { size:14, color:['#22c55e','#ef4444'], line:{width:2,color:'#fff'} },
          }]}
          layout={{
            paper_bgcolor:'transparent', plot_bgcolor:'transparent',
            font: { color:'#94a3b8', size:11 },
            yaxis: { range:[0,1.1], tickformat:'.0%', gridcolor:'rgba(255,255,255,0.05)' },
            xaxis: { color:'#94a3b8' },
            margin: { l:50,r:20,t:20,b:40 },
            autosize:true,
            showlegend:false,
          }}
          config={{ responsive:true, displayModeBar:false }}
          style={{ width:'100%', height:'100%' }}
          useResizeHandler
        />
      </div>

      {/* Drifted pairs */}
      <div>
        <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <AlertTriangle size={15} className="text-amber-400" />
          Drifted Pairs ({drifted.length}) — Click to get AI explanation
        </h2>
        <div className="flex flex-col gap-3">
          {drifted.length === 0 ? (
            <p className="text-slate-500 text-sm text-center py-8">No drifted pairs detected. All requirements are stable.</p>
          ) : (
            drifted.map((pair, i) => (
              <DriftCard key={i} pair={pair} projectId={id} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
