// frontend/src/components/live.jsx
// Shared by the PR Bot "Live reviews" tab and the dashboard's PR activity widget.
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, MinusCircle, XCircle } from 'lucide-react'

// PASS / WARN / BLOCK come from the engine; SKIPPED / ERROR from the GitHub App flow.
export const VERDICTS = {
  PASS:    { label: 'Pass',    icon: CheckCircle2,  text: 'text-compliant',    ring: 'bg-compliant/12 ring-compliant/25' },
  WARN:    { label: 'Warn',    icon: AlertTriangle, text: 'text-partial',      ring: 'bg-partial/12 ring-partial/25' },
  BLOCK:   { label: 'Block',   icon: XCircle,       text: 'text-noncompliant', ring: 'bg-noncompliant/12 ring-noncompliant/25' },
  SKIPPED: { label: 'Skipped', icon: MinusCircle,   text: 'text-white/50',     ring: 'bg-white/5 ring-white/10' },
  ERROR:   { label: 'Error',   icon: AlertTriangle, text: 'text-noncompliant', ring: 'bg-noncompliant/12 ring-noncompliant/25' },
}

export function VerdictBadge({ verdict }) {
  const v = VERDICTS[verdict] || VERDICTS.SKIPPED
  return (
    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 w-[68px] text-center shrink-0 ${v.ring} ${v.text}`}>
      {v.label}
    </span>
  )
}

/** Re-render every `ms` so relative times stay current. */
export function useNow(ms) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

export function ago(iso, now = Date.now()) {
  if (!iso) return ''
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000)
  if (s < 10) return 'just now'
  if (s < 60) return `${Math.floor(s)} s ago`
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}
