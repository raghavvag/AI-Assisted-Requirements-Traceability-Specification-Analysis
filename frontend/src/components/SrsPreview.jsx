// frontend/src/components/SrsPreview.jsx
// ---------------------------------------------------------------------------
// What the SRS parser extracted from an uploaded document, shown BEFORE the
// analysis runs: how many requirements, which strategy found them (tables /
// tagged headings / shall statements / diagrams), what boilerplate was
// removed, which figures were read, any warnings, and every requirement with
// its source location in the document.
// ---------------------------------------------------------------------------

import { useEffect, useMemo, useState } from 'react'
import { motion } from 'motion/react'
import { AlertTriangle, ChevronDown, ChevronRight, FileText, Image as ImageIcon, Scissors, X } from 'lucide-react'

import { apiError, projectsApi } from '../services/api'
import { Loading } from './ui'

const STRATEGY_LABEL = {
  table:           'from requirement tables',
  tagged:          'from tagged headings / paragraphs',
  shall_statement: 'from shall/must statements',
  legacy_markdown: 'from REQ-N blocks',
  diagram:         'read from diagrams',
}

const SKIP_LABEL = {
  toc: 'table-of-contents lines',
  caption: 'captions',
  image: 'unreadable images',
  image_too_small: 'icons / small images',
  image_unreadable: 'unreadable images',
  figure_ignored: 'figures (diagram reading off)',
  page_number: 'page numbers',
  running_header_footer: 'running headers/footers',
  repeated_header_row: 'repeated table headers',
  layout_table_unwrapped: 'layout boxes unwrapped',
  table_continued_across_pages: 'tables joined across pages',
  non_functional_excluded: 'non-functional (excluded)',
}

const FORMAT_LABEL = { docx: 'Word', pdf: 'PDF', markdown: 'Markdown', text: 'Text' }

/** Collapse report.skipped into "sections removed" + "noise items removed". */
function summariseSkipped(skipped = {}) {
  const sections = []
  const items = []
  Object.entries(skipped).forEach(([reason, n]) => {
    if (reason.startsWith('blocklisted_section:')) sections.push(reason.slice('blocklisted_section:'.length))
    else if (SKIP_LABEL[reason]) items.push(`${n} ${SKIP_LABEL[reason]}`)
  })
  return { sections, items }
}

function Chip({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'bg-white/5 text-white/60 ring-white/10',
    info:    'bg-brand-400/10 text-brand-300 ring-brand-400/25',
    warn:    'bg-partial/10 text-partial ring-partial/25',
    good:    'bg-compliant/10 text-compliant ring-compliant/25',
  }
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full ring-1 whitespace-nowrap ${tones[tone]}`}>
      {children}
    </span>
  )
}

function RequirementRow({ req }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-white/5 last:border-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-3 py-2.5 flex items-start gap-3 hover:bg-white/[0.03] transition-colors"
      >
        <span className="text-white/30 mt-0.5 shrink-0">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
        <span className="font-mono text-[12px] text-brand-300 w-20 shrink-0 truncate" title={req.raw_id || req.id}>
          {req.id}
        </span>
        <span className="text-[13px] text-white/85 flex-1 min-w-0 truncate">{req.title}</span>
        <span className="flex items-center gap-1.5 shrink-0">
          {req.type === 'non_functional' && <Chip>NFR</Chip>}
          {req.priority && <Chip tone="info">{req.priority}</Chip>}
          {req.strategy === 'diagram' && <Chip tone="info">diagram</Chip>}
          {req.id_source === 'generated' && <Chip tone="warn">generated ID</Chip>}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 pl-[2.6rem] space-y-2">
          <p className="text-[13px] text-white/65 leading-relaxed">{req.text}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-white/35">
            {req.source?.locator && <span>Location: <span className="text-white/55">{req.source.locator}</span></span>}
            {req.section && <span>Section: <span className="text-white/55">{req.section}</span></span>}
            {req.strategy && <span>Found by: <span className="text-white/55">{req.strategy.replace('_', ' ')}</span></span>}
          </div>
          {req.figures?.length > 0 && (
            <p className="text-[11px] text-white/35">
              {req.strategy === 'diagram' ? 'Read from ' : 'Illustrated by '}
              {req.figures.map((f) => `${f.figure}${f.caption ? ` (${f.caption})` : ''}`).join(', ')}
            </p>
          )}
          {req.rationale && (
            <p className="text-[11px] text-white/35">Rationale (not used for matching): {req.rationale}</p>
          )}
        </div>
      )}
    </li>
  )
}

/** Figures the diagram reader interpreted, and what each one contributed. */
function FigureList({ figures }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="glass rounded-xl px-3 py-2.5 text-[12px] text-white/50">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-2 text-left">
        <ImageIcon size={13} className="text-white/30 shrink-0" />
        <span className="flex-1">
          Read <span className="text-white/75 font-medium tnum">{figures.length}</span> diagram{figures.length === 1 ? '' : 's'}
        </span>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      {open && (
        <ul className="mt-2 space-y-2">
          {figures.map((f) => (
            <li key={`${f.figure}-${f.locator}`} className="border-t border-white/5 pt-2">
              <p className="text-white/75">
                <span className="font-mono text-brand-300">{f.figure}</span>
                {f.caption && <span> · {f.caption}</span>}
              </p>
              {f.summary && <p className="mt-0.5">{f.summary}</p>}
              <p className="mt-0.5 text-white/35">
                {f.diagram_type && f.diagram_type !== 'other' && <span>{f.diagram_type.replace('_', ' ')} · </span>}
                read by {f.engine}
                {f.requirements?.length > 0 && <span> · new: {f.requirements.join(', ')}</span>}
                {f.illustrates?.length > 0 && <span> · illustrates: {f.illustrates.join(', ')}</span>}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Presentational preview. `data` is the /srs/requirements payload:
 * { filename, is_default, requirements[], report{} }.
 */
export default function SrsPreview({ data, maxListHeight = 'max-h-[46vh]' }) {
  const [showAllWarnings, setShowAllWarnings] = useState(false)
  const report = data?.report || {}
  const reqs = data?.requirements || []
  const { sections, items } = useMemo(() => summariseSkipped(report.skipped), [report.skipped])
  const warnings = report.warnings || []
  const shownWarnings = showAllWarnings ? warnings : warnings.slice(0, 3)

  if (!data) return null
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-compliant/15 text-compliant flex items-center justify-center shrink-0">
          <FileText size={17} />
        </div>
        <div className="min-w-0">
          <p className="text-sm text-white">
            Extracted <span className="font-bold tnum">{reqs.length}</span> requirement{reqs.length === 1 ? '' : 's'}
            <span className="text-white/45"> from </span>
            <span className="font-medium break-all">{data.filename}</span>
          </p>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {report.format && <Chip>{FORMAT_LABEL[report.format] || report.format}</Chip>}
            {Object.entries(report.by_strategy || {}).map(([k, n]) => (
              <Chip key={k} tone="good">{n} {STRATEGY_LABEL[k] || k}</Chip>
            ))}
            {data.is_default && <Chip tone="warn">built-in demo SRS: nothing uploaded</Chip>}
          </div>
        </div>
      </div>

      {(sections.length > 0 || items.length > 0) && (
        <div className="glass rounded-xl px-3 py-2.5 text-[12px] text-white/50 flex gap-2">
          <Scissors size={13} className="text-white/30 mt-0.5 shrink-0" />
          <div className="space-y-0.5 min-w-0">
            {sections.length > 0 && (
              <p>Boilerplate sections removed: <span className="text-white/70">{sections.join(' · ')}</span></p>
            )}
            {items.length > 0 && <p>Noise removed: <span className="text-white/70">{items.join(' · ')}</span></p>}
          </div>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="rounded-xl px-3 py-2.5 bg-partial/[0.07] ring-1 ring-partial/20 text-[12px] space-y-1">
          {shownWarnings.map((w) => (
            <p key={w} className="flex gap-2 text-partial/90">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>{w}</span>
            </p>
          ))}
          {warnings.length > 3 && (
            <button className="text-[11px] text-white/45 hover:text-white" onClick={() => setShowAllWarnings((s) => !s)}>
              {showAllWarnings ? 'Show fewer' : `Show all ${warnings.length} warnings`}
            </button>
          )}
        </div>
      )}

      {report.figures?.length > 0 && <FigureList figures={report.figures} />}

      <ul className={`glass rounded-xl overflow-y-auto ${maxListHeight}`}>
        {reqs.map((r) => <RequirementRow key={r.id} req={r} />)}
      </ul>
    </div>
  )
}

/** Self-fetching slide-over used on the project pages. */
export function SrsDrawer({ projectId, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })

  useEffect(() => {
    let alive = true
    projectsApi.srsRequirements(projectId)
      .then((res) => alive && setState({ loading: false, data: res.data, error: '' }))
      .catch((e) => alive && setState({ loading: false, data: null, error: apiError(e, 'Could not load the SRS.') }))
    return () => { alive = false }
  }, [projectId])

  return (
    <motion.aside
      initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
      transition={{ type: 'spring', stiffness: 320, damping: 34 }}
      className="fixed top-0 right-0 h-full w-[560px] max-w-[94vw] z-50 glass-strong
                 border-l border-white/10 p-6 overflow-y-auto"
    >
      <div className="flex items-start justify-between mb-5">
        <div>
          <p className="text-[10px] uppercase tracking-[0.14em] text-white/35 mb-1">Requirements baseline</p>
          <h3 className="font-bold text-white">Extracted from the SRS</h3>
        </div>
        <button onClick={onClose} className="text-white/40 hover:text-white" aria-label="Close"><X size={18} /></button>
      </div>
      {state.loading && <div className="h-40"><Loading label="Reading the SRS…" /></div>}
      {state.error && <p className="text-sm text-noncompliant">{state.error}</p>}
      {state.data && <SrsPreview data={state.data} maxListHeight="max-h-[62vh]" />}
    </motion.aside>
  )
}
