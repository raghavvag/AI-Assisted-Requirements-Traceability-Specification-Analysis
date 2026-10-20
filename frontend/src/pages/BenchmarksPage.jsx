// frontend/src/pages/BenchmarksPage.jsx
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { BarChart3 } from 'lucide-react'
import Plot from 'react-plotly.js'

import { analysisApi } from '../services/api'
import {
  EmptyState, Loading, PageHeader, PageShell, Panel,
  StatCard, StatGrid, PLOT_CONFIG, PLOT_THEME,
} from '../components/ui'

const METRICS = ['Precision', 'Recall', 'F1-Score', 'MAP']

const METRIC_HELP = {
  'Precision': 'of the endpoints retrieved, how many were correct',
  'Recall':    'of the correct endpoints, how many were found',
  'F1-Score':  'harmonic mean of precision and recall',
  'MAP':       'ranking quality, independent of threshold',
}

const norm = (s) => s.toLowerCase().replace(/[-\s_]/g, '')

export default function BenchmarksPage() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    analysisApi.benchmark(id)
      .then((res) => alive && setData(res.data))
      .catch(() => {})
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [id])

  const model = useMemo(() => {
    if (!data || data.message) return null
    // The pipeline sends DataFrame.to_dict(): { metric: { model: value } }.
    // Transpose to { model: { metric: value } }; a model-keyed report passes through.
    const raw = data.report || {}
    const metricKeyed = Object.keys(raw).some((k) => METRICS.some((m) => norm(m) === norm(k)))
    const report = {}
    if (metricKeyed) {
      Object.entries(raw).forEach(([metric, byModel]) => {
        Object.entries(byModel || {}).forEach(([m, v]) => { (report[m] ||= {})[metric] = v })
      })
    } else Object.assign(report, raw)
    const models = Object.keys(report)
    const tKey = models.find((k) => /transformer|mpnet/i.test(k)) || models[0]
    const bKey = models.find((k) => /tfidf|tf-idf/i.test(k)) || models[1]

    const score = (key, metric) => {
      const row = key && report[key]
      if (!row) return 0
      const want = norm(metric)
      for (const [k, v] of Object.entries(row)) {
        if (norm(k) === want) return Number(v) || 0
      }
      return 0
    }

    return {
      tKey, bKey,
      transformer: METRICS.map((m) => score(tKey, m)),
      tfidf: METRICS.map((m) => score(bKey, m)),
      perReq: data.per_requirement || [],
      threshold: data.eval_threshold,
    }
  }, [data])

  if (loading) return <PageShell><Loading label="Loading benchmark…" /></PageShell>

  if (!model) return (
    <PageShell>
      <PageHeader title="Evaluation Metrics" subtitle="Transformer vs TF-IDF baseline" />
      <Panel fill>
        <EmptyState
          icon={BarChart3}
          title="No benchmark data"
          description="Metrics need a ground-truth label file (data/ground_truth/traceability_labels.csv) that matches the requirement IDs in your SRS. Re-run the analysis once that is in place."
        />
      </Panel>
    </PageShell>
  )

  const { transformer, tfidf, perReq, threshold } = model

  return (
    <PageShell scroll>
      <PageHeader
        title="Evaluation Metrics"
        subtitle={`Information-retrieval accuracy vs a TF-IDF keyword baseline${threshold ? ` · τ = ${threshold}` : ''}`}
      />

      <StatGrid cols={4}>
        {METRICS.map((metric, i) => {
          const t = transformer[i]
          const delta = t - tfidf[i]
          return (
            <StatCard
              key={metric}
              label={metric}
              value={t.toFixed(3)}
              hint={`${delta >= 0 ? '+' : ''}${delta.toFixed(3)} vs TF-IDF · ${METRIC_HELP[metric]}`}
              tone={delta > 0 ? 'good' : delta < 0 ? 'bad' : 'info'}
            />
          )
        })}
      </StatGrid>

      <Panel
        title="Transformer vs TF-IDF"
        hint="Higher is better. The transformer's advantage comes mostly from recall — it finds relevant endpoints that share no keywords."
        className="shrink-0"
        bodyClass="overflow-hidden"
      >
        <div style={{ height: 320 }} className="w-full overflow-hidden">
          <Plot
            data={[
              {
                type: 'bar', name: 'Transformer (ours)',
                x: METRICS, y: transformer,
                marker: { color: '#22d3ee', line: { width: 0 } },
                text: transformer.map((s) => s.toFixed(3)),
                textposition: 'outside', textfont: { color: '#a5f3fc', size: 11 },
                hovertemplate: '<b>%{x}</b><br>Transformer %{y:.4f}<extra></extra>',
              },
              {
                type: 'bar', name: 'TF-IDF baseline',
                x: METRICS, y: tfidf,
                marker: { color: '#2b2e34', line: { width: 0 } },
                text: tfidf.map((s) => s.toFixed(3)),
                textposition: 'outside', textfont: { color: 'rgba(255,255,255,0.45)', size: 11 },
                hovertemplate: '<b>%{x}</b><br>TF-IDF %{y:.4f}<extra></extra>',
              },
            ]}
            layout={{
              ...PLOT_THEME,
              barmode: 'group',
              bargap: 0.42,
              bargroupgap: 0.12,
              yaxis: {
                range: [0, 1.16], gridcolor: 'rgba(255,255,255,0.05)', zeroline: false,
                tickfont: { size: 10, color: 'rgba(255,255,255,0.4)' },
              },
              xaxis: { showgrid: false, tickfont: { size: 12, color: 'rgba(255,255,255,0.6)' } },
              legend: {
                orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1,
                font: { color: 'rgba(255,255,255,0.5)', size: 11 },
              },
              margin: { l: 44, r: 16, t: 42, b: 38 },
            }}
            config={PLOT_CONFIG}
            style={{ width: '100%', height: '100%' }}
            useResizeHandler
          />
        </div>
      </Panel>

      {perReq.length > 0 && (
        <Panel
          title="Per-requirement breakdown"
          hint="Where the average comes from — a low row is a requirement whose endpoints are hard to retrieve."
          className="shrink-0"
          bodyClass="overflow-x-auto"
        >
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-900/90 backdrop-blur">
              <tr className="border-b border-white/8">
                {Object.keys(perReq[0]).map((col) => (
                  <th key={col} className="text-left py-2.5 px-4 text-[10px] uppercase tracking-[0.12em] text-white/35 font-semibold whitespace-nowrap">
                    {col.replace(/_/g, ' ')}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {perReq.map((row, i) => (
                <tr key={i} className="border-b border-white/5 hover:bg-white/[0.025] transition-colors">
                  {Object.entries(row).map(([k, v]) => (
                    <td key={k} className={`py-2.5 px-4 whitespace-nowrap ${
                      typeof v === 'number'
                        ? `font-mono tnum ${v >= 0.7 ? 'text-compliant' : v >= 0.4 ? 'text-partial' : 'text-noncompliant'}`
                        : 'text-white/70'}`}>
                      {typeof v === 'number' ? v.toFixed(3) : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </PageShell>
  )
}
