// frontend/src/components/ui.jsx
// ---------------------------------------------------------------------------
// Shared presentation primitives. Every analysis page composes these so the
// product reads as one system instead of four differently-styled screens.
//
// Layout contract used throughout: a page is
//   <PageShell>            flex column, owns the scroll
//     <PageHeader/>        shrink-0
//     <StatGrid/>          shrink-0
//     <Panel fill>         flex-1 min-h-0   (charts live here)
//
// The `shrink-0` matters: Plotly measures itself once and renders an SVG at
// that size. If its container is a flex child that later shrinks, the SVG
// overflows and paints on top of whatever follows it. Fixed-height chart
// wrappers MUST be shrink-0 + overflow-hidden.
// ---------------------------------------------------------------------------

import { motion } from 'motion/react'

// --- scoring helpers --------------------------------------------------------

export const BANDS = {
  compliant:    { label: 'Compliant',     tone: 'good', hex: '#34d399' },
  partial:      { label: 'Partial',       tone: 'warn', hex: '#fbbf24' },
  noncompliant: { label: 'Not covered',   tone: 'bad',  hex: '#fb7185' },
}

/** Which band a cosine score falls into for a given compliance threshold. */
export function bandOf(score, threshold = 0.55) {
  if (score >= threshold) return 'compliant'
  if (score >= threshold - 0.15) return 'partial'
  return 'noncompliant'
}

const TONE_TEXT = {
  good: 'text-compliant',
  warn: 'text-partial',
  bad:  'text-noncompliant',
  info: 'text-brand-400',
  neutral: 'text-white',
}

const TONE_RING = {
  good: 'ring-compliant/25 bg-compliant/10',
  warn: 'ring-partial/25 bg-partial/10',
  bad:  'ring-noncompliant/25 bg-noncompliant/10',
  info: 'ring-brand-400/25 bg-brand-400/10',
  neutral: 'ring-white/10 bg-white/5',
}

// --- layout -----------------------------------------------------------------

export function PageShell({ children, scroll = false }) {
  return (
    <div className={`flex flex-col h-full p-6 gap-5 ${scroll ? 'overflow-y-auto' : 'overflow-hidden'}`}>
      {children}
    </div>
  )
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <motion.header
      initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
      className="flex items-start justify-between gap-4 shrink-0 flex-wrap"
    >
      <div className="min-w-0">
        <h1 className="text-2xl font-bold gradient-text leading-tight">{title}</h1>
        {subtitle && <p className="text-sm text-white/45 mt-0.5">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2 shrink-0">{children}</div>}
    </motion.header>
  )
}

/** Titled surface. `fill` makes it the flex child that absorbs leftover height. */
export function Panel({ title, hint, actions, children, fill = false, bodyClass = '', className = '' }) {
  return (
    <section
      className={`glass glass-edge rounded-2xl flex flex-col overflow-hidden
        ${fill ? 'flex-1 min-h-0' : 'shrink-0'} ${className}`}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/5 shrink-0">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-semibold text-white truncate">{title}</h2>}
            {hint && <p className="text-[11px] text-white/35 mt-0.5 truncate">{hint}</p>}
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </div>
      )}
      <div className={`flex-1 min-h-0 ${bodyClass}`}>{children}</div>
    </section>
  )
}

// --- stats ------------------------------------------------------------------

// Tailwind scans source text, so dynamic `grid-cols-${n}` never gets generated.
// The variants must appear literally.
const GRID_COLS = {
  2: 'grid-cols-2',
  3: 'grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-2 lg:grid-cols-4',
  5: 'grid-cols-2 lg:grid-cols-5',
}

export function StatGrid({ children, cols = 4 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className={`grid gap-3 shrink-0 ${GRID_COLS[cols] || GRID_COLS[4]}`}
    >
      {children}
    </motion.div>
  )
}

/**
 * A stat that explains itself. `hint` is the difference between "15" and
 * "15 of 120 pairs scored above 0.55" — always give it one.
 */
export function StatCard({ label, value, hint, tone = 'neutral', icon: Icon }) {
  return (
    <div className="metric-card !p-4 !gap-0.5">
      <div className="flex items-center gap-1.5">
        {Icon && <Icon size={11} className="text-white/25" />}
        <p className="text-[10px] text-white/35 uppercase tracking-[0.14em]">{label}</p>
      </div>
      <p className={`text-[26px] leading-tight font-bold tnum ${TONE_TEXT[tone]}`}>{value}</p>
      {hint && <p className="text-[11px] text-white/35 leading-snug">{hint}</p>}
    </div>
  )
}

// --- controls ---------------------------------------------------------------

export function Segmented({ value, onChange, options }) {
  return (
    <div className="glass rounded-xl p-1 flex gap-1" role="tablist">
      {options.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          role="tab"
          aria-selected={value === key}
          onClick={() => onChange(key)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all inline-flex items-center gap-1.5
            ${value === key
              ? 'bg-brand-400/15 text-white ring-1 ring-brand-400/30'
              : 'text-white/45 hover:text-white hover:bg-white/5'}`}
        >
          {Icon && <Icon size={13} />}
          {label}
        </button>
      ))}
    </div>
  )
}

// --- score presentation -----------------------------------------------------

export function BandBadge({ score, threshold = 0.55 }) {
  const band = bandOf(score, threshold)
  const { label, tone } = BANDS[band]
  return (
    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 whitespace-nowrap
      ${TONE_RING[tone]} ${TONE_TEXT[tone]}`}>
      {label}
    </span>
  )
}

/** Horizontal score meter with the compliance threshold marked. */
export function ScoreBar({ score = 0, threshold = 0.55, showValue = true }) {
  const band = bandOf(score, threshold)
  const pct = Math.max(0, Math.min(1, score)) * 100
  return (
    <div className="flex items-center gap-2 w-full">
      <div className="relative h-1.5 flex-1 rounded-full bg-white/8 overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: BANDS[band].hex }}
        />
        {/* compliance threshold marker — makes every bar self-explanatory */}
        <span
          className="absolute inset-y-0 w-px bg-white/45"
          style={{ left: `${threshold * 100}%` }}
          title={`Threshold ${threshold}`}
        />
      </div>
      {showValue && (
        <span className={`text-xs font-semibold tnum w-11 text-right ${TONE_TEXT[BANDS[band].tone]}`}>
          {score.toFixed(3)}
        </span>
      )}
    </div>
  )
}

// --- states -----------------------------------------------------------------

export function Loading({ label = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center">
        <span className="spinner mx-auto" style={{ width: 40, height: 40 }} />
        <p className="text-white/40 text-sm mt-4">{label}</p>
      </div>
    </div>
  )
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex items-center justify-center h-full p-8">
      <div className="text-center max-w-sm">
        {Icon && (
          <div className="w-14 h-14 rounded-2xl glass mx-auto mb-4 flex items-center justify-center">
            <Icon size={24} className="text-brand-400/60" />
          </div>
        )}
        <p className="text-base font-semibold text-white mb-1.5">{title}</p>
        {description && <p className="text-sm text-white/40 leading-relaxed mb-4">{description}</p>}
        {action}
      </div>
    </div>
  )
}

// --- plotly ----------------------------------------------------------------

/** Shared dark-theme Plotly layout. Spread it, then override what you need. */
export const PLOT_THEME = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: 'rgba(255,255,255,0.55)', family: 'Inter, system-ui, sans-serif', size: 11 },
  hoverlabel: {
    bgcolor: '#141518',
    bordercolor: 'rgba(255,255,255,0.14)',
    font: { color: '#ffffff', size: 12, family: 'Inter, system-ui, sans-serif' },
  },
  margin: { l: 52, r: 18, t: 16, b: 40 },
  autosize: true,
}

export const PLOT_CONFIG = { responsive: true, displayModeBar: false }
