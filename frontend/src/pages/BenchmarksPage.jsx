// frontend/src/pages/BenchmarksPage.jsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { BarChart3 } from 'lucide-react'
import Plot from 'react-plotly.js'
import { analysisApi } from '../services/api'

const METRICS = ['Precision', 'Recall', 'F1-Score', 'MAP']

export default function BenchmarksPage() {
  const { id } = useParams()
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    analysisApi.benchmark(id)
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
        <BarChart3 size={48} className="mx-auto mb-4 text-brand-600/40" />
        <p className="text-lg font-medium text-white mb-2">No benchmark data</p>
        <p className="text-sm">Run analysis with the default dataset (traceability_labels.csv required).</p>
      </div>
    </div>
  )

  // Parse report dict — keys are model names
  const report = data.report || {}
  const models = Object.keys(report)
  const transformerKey = models.find(k => k.toLowerCase().includes('transformer') || k.toLowerCase().includes('mpnet')) || models[0]
  const tfidfKey       = models.find(k => k.toLowerCase().includes('tfidf') || k.toLowerCase().includes('tf-idf')) || models[1]

  function getScore(modelKey, metric) {
    if (!modelKey || !report[modelKey]) return 0
    const row = report[modelKey]
    const normalized = metric.toLowerCase().replace('-','').replace(' ','')
    for (const [k,v] of Object.entries(row)) {
      if (k.toLowerCase().replace('-','').replace(' ','') === normalized) return Number(v) || 0
    }
    return 0
  }

  const transformerScores = METRICS.map(m => getScore(transformerKey, m))
  const tfidfScores       = METRICS.map(m => getScore(tfidfKey, m))

  const perReq = data.per_requirement || []

  return (
    <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
      <motion.div initial={{ opacity:0 }} animate={{ opacity:1 }}>
        <h1 className="text-2xl font-bold gradient-text">IR Evaluation Metrics</h1>
        <p className="text-sm text-slate-400">Transformer vs TF-IDF baseline · Precision, Recall, F1, MAP</p>
      </motion.div>

      {/* Top-level metric cards */}
      <div className="grid grid-cols-4 gap-4">
        {METRICS.map((metric, i) => {
          const tScore = transformerScores[i]
          const bScore = tfidfScores[i]
          const delta  = tScore - bScore
          return (
            <div key={metric} className="metric-card">
              <p className="text-[10px] text-slate-500 uppercase tracking-wider">{metric}</p>
              <p className="text-2xl font-bold text-brand-400">{tScore.toFixed(4)}</p>
              <p className={`text-xs ${delta >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {delta >= 0 ? '+' : ''}{delta.toFixed(4)} vs TF-IDF
              </p>
            </div>
          )
        })}
      </div>

      {/* Bar chart */}
      <div className="glass rounded-2xl p-4" style={{height:340}}>
        <Plot
          data={[
            {
              type: 'bar', name: 'Transformer (ours)',
              x: METRICS, y: transformerScores,
              marker: { color: '#3b82f6' },
              text: transformerScores.map(s => s.toFixed(4)),
              textposition: 'outside', textfont: { color:'#93c5fd', size:11 },
            },
            {
              type: 'bar', name: 'TF-IDF Baseline',
              x: METRICS, y: tfidfScores,
              marker: { color: '#475569' },
              text: tfidfScores.map(s => s.toFixed(4)),
              textposition: 'outside', textfont: { color:'#94a3b8', size:11 },
            },
          ]}
          layout={{
            barmode: 'group',
            title: { text:'Transformer vs TF-IDF Retrieval Metrics', font:{ color:'#e2e8f0' } },
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color:'#94a3b8' },
            yaxis: { range:[0,1.15], gridcolor:'rgba(255,255,255,0.05)', title:'Score' },
            legend: { orientation:'h', yanchor:'bottom', y:1.02, xanchor:'right', x:1, font:{ color:'#94a3b8' } },
            margin: { l:50,r:20,t:50,b:40 },
            autosize: true,
          }}
          config={{ responsive:true, displayModeBar:false }}
          style={{ width:'100%', height:'100%' }}
          useResizeHandler
        />
      </div>

      {/* Per-requirement table */}
      {perReq.length > 0 && (
        <div className="glass rounded-2xl p-4">
          <h2 className="text-sm font-semibold text-white mb-3">Per-Requirement Breakdown</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-slate-400">
              <thead>
                <tr className="border-b border-white/5">
                  {Object.keys(perReq[0]).map(col => (
                    <th key={col} className="text-left py-2 px-3 text-[10px] uppercase tracking-wider text-slate-600">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {perReq.map((row, i) => (
                  <tr key={i} className="border-b border-white/5 hover:bg-white/2">
                    {Object.entries(row).map(([k, v]) => (
                      <td key={k} className="py-2 px-3 font-mono">
                        {typeof v === 'number' ? v.toFixed(4) : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
