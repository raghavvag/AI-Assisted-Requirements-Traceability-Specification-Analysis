// frontend/src/pages/PRBotPage.jsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { GitPullRequest, Play, CheckCircle, XCircle } from 'lucide-react'
import { analysisApi } from '../services/api'
import toast from 'react-hot-toast'

const SCENARIOS = [
  { key: 'add_endpoint',         label: 'Add optional discount_code field',  desc: 'PR #41 — non-breaking additive change to cart. Expected: PASS ✅' },
  { key: 'remove_idempotency',   label: 'Remove refund idempotency key',     desc: 'PR #42 — drops client transaction_id from refund. Expected: BLOCK 🚫 (REQ-04)' },
  { key: 'drop_2fa_requirement', label: 'Drop 2FA enforcement on checkout',  desc: 'PR #43 — removes step-up auth for high-value orders. Expected: BLOCK 🚫 (REQ-09)' },
]

export default function PRBotPage() {
  const { id } = useParams()
  const [scenario, setScenario] = useState(SCENARIOS[0].key)
  const [result,   setResult]   = useState(null)
  const [running,  setRunning]  = useState(false)

  async function run() {
    setRunning(true)
    const toastId = toast.loading('Simulating PR review…')
    try {
      const res = await analysisApi.prReview(id, scenario)
      setResult(res.data)
      toast.success(res.data.approved ? 'PR Approved ✅' : 'PR Blocked 🚫', { id: toastId })
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Review failed.', { id: toastId })
    } finally { setRunning(false) }
  }

  return (
    <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
      <motion.div initial={{ opacity:0 }} animate={{ opacity:1 }}>
        <h1 className="text-2xl font-bold gradient-text">PR Compliance Bot</h1>
        <p className="text-sm text-slate-400">Simulated CI/CD compliance review against requirement baseline</p>
      </motion.div>

      {/* Scenario picker */}
      <div className="glass rounded-2xl p-5">
        <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <GitPullRequest size={15} className="text-brand-400" /> Select PR Scenario
        </h2>
        <div className="flex flex-col gap-2 mb-4">
          {SCENARIOS.map(s => (
            <div key={s.key}
              onClick={() => setScenario(s.key)}
              className={`p-4 rounded-xl border cursor-pointer transition-all
                ${scenario === s.key
                  ? 'border-brand-500/50 bg-brand-600/10'
                  : 'border-white/5 hover:border-white/10'}`}
            >
              <div className="flex items-start gap-3">
                <div className={`w-4 h-4 rounded-full border-2 mt-0.5 flex items-center justify-center shrink-0
                  ${scenario === s.key ? 'border-brand-500' : 'border-slate-600'}`}>
                  {scenario === s.key && <div className="w-2 h-2 rounded-full bg-brand-500" />}
                </div>
                <div>
                  <p className="text-sm font-medium text-white">{s.label}</p>
                  <p className="text-xs text-slate-500">{s.desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
        <button className="btn-primary" onClick={run} disabled={running}>
          {running ? <span className="spinner" /> : <Play size={14} />}
          {running ? 'Running Review…' : 'Run PR Review'}
        </button>
      </div>

      {/* Result */}
      {result && (
        <motion.div initial={{ opacity:0, y:10 }} animate={{ opacity:1, y:0 }}>
          {/* Status banner */}
          <div className={`flex items-center gap-3 p-4 rounded-xl mb-4 border
            ${result.approved
              ? 'bg-green-500/10 border-green-500/30 text-green-400'
              : 'bg-red-500/10   border-red-500/30   text-red-400'}`}
          >
            {result.approved ? <CheckCircle size={20}/> : <XCircle size={20}/>}
            <div>
              <p className="font-bold">{result.approved ? '✅ PR Approved' : '🚫 PR Blocked'}</p>
              <p className="text-xs opacity-80">
                {result.approved
                  ? 'No compliance violations detected.'
                  : `${result.violations?.length} violation(s) detected — merge blocked.`}
              </p>
            </div>
          </div>

          {/* Violations */}
          {result.violations?.length > 0 && (
            <div className="flex flex-col gap-3 mb-4">
              {result.violations.map((v, i) => (
                <div key={i} className="glass rounded-xl p-4 border border-red-500/20">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="badge-noncompliant">{v.severity}</span>
                    <span className="text-xs text-slate-500 font-mono">{v.requirement_id}</span>
                  </div>
                  <p className="text-sm text-white font-medium mb-1">{v.endpoint_id}</p>
                  <div className="flex gap-4 text-xs text-slate-400 mb-2">
                    <span>v1.0: <b className="text-green-400">{v.score_v1?.toFixed(3)}</b></span>
                    <span>v2.0: <b className="text-red-400">{v.score_v2?.toFixed(3)}</b></span>
                    <span>Δ: <b className="text-red-400">{v.delta?.toFixed(3)}</b></span>
                  </div>
                  {v.explanation && (
                    <p className="text-xs text-slate-400 leading-relaxed">{v.explanation}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* GitHub markdown review */}
          <div className="glass rounded-xl p-4 border border-white/5">
            <p className="text-xs text-slate-500 mb-2 font-mono">📋 Generated GitHub Review Comment</p>
            <pre className="code-block text-xs whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
              {result.review_comment}
            </pre>
          </div>
        </motion.div>
      )}
    </div>
  )
}
