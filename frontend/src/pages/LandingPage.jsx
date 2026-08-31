// frontend/src/pages/LandingPage.jsx
// ---------------------------------------------------------------------------
// The public landing page.
//
// Composition
//   • A single fixed WebGL canvas sits behind everything (NeuralGraph).
//   • Lenis provides inertia scrolling; GSAP ScrollTrigger reads from it.
//   • One master ScrollTrigger converts scroll position into a 0→1 value that
//     morphs the 3D graph from an organic cloud into a traceability matrix.
//   • Section-level triggers handle text reveals and the PR→diagnosis morph.
//
// Everything animated here writes to refs or to DOM nodes directly. Scrolling
// this page does not re-render a single React component.
// ---------------------------------------------------------------------------

import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useInView, animate } from 'motion/react'
import Tilt from 'react-parallax-tilt'
import {
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Check,
  ChevronDown,
  GitPullRequest,
  Github,
  Layers,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react'

import NeuralGraph from '../components/landing/NeuralGraph'
import { useSmoothScroll, usePrefersReducedMotion, gsap, ScrollTrigger } from '../hooks/useSmoothScroll'
import { useAuth } from '../context/AuthContext'
import { cn } from '../lib/utils'

/* ===========================================================================
   PRIMITIVES
   =========================================================================== */

/** Small uppercase eyebrow with a leading hairline. */
function Kicker({ children, className }) {
  return (
    <div className={cn('kicker', className)}>
      <span className="h-px w-8 bg-linear-to-r from-neon-cyan/70 to-transparent" />
      {children}
    </div>
  )
}

/**
 * One-way "has this entered the viewport yet" flag.
 *
 * IntersectionObserver alone is not enough: an instant jump (anchor link, End
 * key, restored scroll position) can skip an element's intersection window
 * entirely, leaving a once:true reveal stuck hidden forever. The scroll guard
 * also reveals anything already at or above the fold. It detaches itself the
 * moment the element is revealed, so the listener cost is bounded.
 */
function useRevealed(ref, margin = '-8%') {
  const inView = useInView(ref, { once: true, margin })
  const [passed, setPassed] = useState(false)

  useEffect(() => {
    if (inView || passed) return undefined
    const check = () => {
      const el = ref.current
      if (!el) return
      if (el.getBoundingClientRect().top < window.innerHeight * 0.92) setPassed(true)
    }
    check()
    window.addEventListener('scroll', check, { passive: true })
    window.addEventListener('resize', check, { passive: true })
    return () => {
      window.removeEventListener('scroll', check)
      window.removeEventListener('resize', check)
    }
  }, [inView, passed, ref])

  return inView || passed
}

/**
 * Word-by-word mask reveal. The classic "expensive typography" move: each word
 * lives in an overflow-hidden box and slides up from below its own baseline.
 *
 * The observer MUST sit on the outer (unclipped) wrapper, never on the moving
 * span. IntersectionObserver accounts for clipping by ancestor overflow, so a
 * span translated 115% below an overflow-hidden parent is reported as *not*
 * intersecting - whileInView would never fire and the word would stay hidden
 * forever. Observing the wrapper breaks that chicken-and-egg.
 */
function RevealText({ text, className, delay = 0, as: Tag = 'span' }) {
  const ref = useRef(null)
  const inView = useRevealed(ref)
  const words = text.split(' ')

  return (
    <Tag ref={ref} className={cn('inline-block', className)}>
      {words.map((w, i) => (
        <Fragment key={`${w}-${i}`}>
          {/* The pb/-mb pair enlarges the clip box so descenders (p, g, y) are
              not sliced off when line-height is tighter than the font size. */}
          <span className="inline-block overflow-hidden align-bottom pb-[0.14em] -mb-[0.14em]">
            <motion.span
              className="inline-block"
              initial={{ y: '115%' }}
              animate={inView ? { y: '0%' } : { y: '115%' }}
              transition={{ duration: 0.78, delay: delay + i * 0.055, ease: [0.16, 1, 0.3, 1] }}
            >
              {w}
            </motion.span>
          </span>
          {/* A real space text node, so the headline still wraps naturally. */}
          {i < words.length - 1 ? ' ' : null}
        </Fragment>
      ))}
    </Tag>
  )
}

/** Generic fade-and-rise, using the same jump-proof reveal flag. */
function Rise({ children, delay = 0, y = 22, className }) {
  const ref = useRef(null)
  const shown = useRevealed(ref, '-10%')
  return (
    <motion.div
      ref={ref}
      className={className}
      initial={{ opacity: 0, y }}
      animate={shown ? { opacity: 1, y: 0 } : { opacity: 0, y }}
      transition={{ duration: 0.7, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  )
}

/** Animated number. Uses tabular figures so the layout cannot jitter. */
function CountUp({ to, decimals = 0, suffix = '', prefix = '', duration = 1.6 }) {
  const ref = useRef(null)
  const inView = useRevealed(ref, '-20%')

  useEffect(() => {
    if (!inView || !ref.current) return undefined
    const node = ref.current
    const controls = animate(0, to, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        node.textContent = `${prefix}${v.toFixed(decimals)}${suffix}`
      },
    })
    return () => controls.stop()
  }, [inView, to, decimals, suffix, prefix, duration])

  return (
    <span ref={ref} className="tnum">
      {prefix}
      {(0).toFixed(decimals)}
      {suffix}
    </span>
  )
}

/**
 * Card whose border lights up under the cursor. A radial gradient tracks the
 * pointer via CSS custom properties, so hovering costs no React renders.
 */
function SpotlightCard({ children, className, tint = '34,211,238' }) {
  const ref = useRef(null)

  const onMove = useCallback((e) => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${e.clientX - r.left}px`)
    el.style.setProperty('--my', `${e.clientY - r.top}px`)
  }, [])

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      style={{ '--tint': tint }}
      className={cn(
        'group relative overflow-hidden rounded-3xl glass glass-edge transition-colors duration-300',
        'hover:border-white/15',
        className,
      )}
    >
      {/* Pointer-tracking spotlight */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          background:
            'radial-gradient(360px circle at var(--mx, 50%) var(--my, 50%), rgba(var(--tint), 0.10), transparent 62%)',
        }}
      />
      <div className="relative">{children}</div>
    </div>
  )
}

/** Infinite logo/tech marquee. Duplicated once so the loop is seamless. */
function Marquee({ items }) {
  return (
    <div className="relative overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_12%,#000_88%,transparent)]">
      <div className="flex w-max animate-marquee gap-14 py-1">
        {[...items, ...items].map((item, i) => (
          <span
            key={i}
            className="shrink-0 font-mono text-xs uppercase tracking-[0.22em] text-white/28"
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

/* ===========================================================================
   NAVIGATION
   =========================================================================== */

function Nav({ onJump }) {
  const { isAuthenticated } = useAuth()
  const [solid, setSolid] = useState(false)

  useEffect(() => {
    const onScroll = () => setSolid(window.scrollY > 40)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const links = [
    ['Problem', '#problem'],
    ['How it works', '#how'],
    ['Results', '#results'],
  ]

  return (
    <motion.header
      initial={{ y: -24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: 0.15 }}
      className="fixed inset-x-0 top-0 z-50 flex justify-center px-4 pt-4"
    >
      <nav
        className={cn(
          'flex w-full max-w-5xl items-center gap-2 rounded-2xl px-3 py-2 transition-all duration-500',
          solid ? 'glass-strong shadow-[0_8px_40px_-12px_rgba(0,0,0,0.9)]' : 'border border-transparent',
        )}
      >
        <a href="#top" onClick={(e) => onJump(e, '#top')} className="flex items-center gap-2.5 pl-1 pr-3">
          <span className="relative grid h-7 w-7 place-items-center rounded-lg bg-white/8 ring-1 ring-white/12">
            <span className="absolute h-1.5 w-1.5 -translate-x-1 -translate-y-1 rounded-full bg-neon-cyan" />
            <span className="absolute h-1.5 w-1.5 translate-x-1 translate-y-1 rounded-full bg-neon-emerald" />
          </span>
          <span className="text-[13px] font-semibold tracking-tight">Traceability</span>
        </a>

        <div className="mx-auto hidden items-center gap-1 md:flex">
          {links.map(([label, href]) => (
            <a
              key={href}
              href={href}
              onClick={(e) => onJump(e, href)}
              className="rounded-lg px-3 py-1.5 text-[13px] font-medium text-white/50 transition-colors hover:bg-white/5 hover:text-white"
            >
              {label}
            </a>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <Link
            to={isAuthenticated ? '/dashboard' : '/login'}
            className="rounded-xl bg-white px-4 py-1.5 text-[13px] font-semibold text-surface-950 transition-transform duration-200 hover:-translate-y-px"
          >
            {isAuthenticated ? 'Dashboard' : 'Sign in'}
          </Link>
        </div>
      </nav>
    </motion.header>
  )
}

/* ===========================================================================
   HERO
   =========================================================================== */

function Hero({ onJump }) {
  const { isAuthenticated } = useAuth()

  return (
    <section id="top" className="relative flex min-h-[100svh] flex-col items-center justify-center px-6 pt-28">
      {/* Legibility scrim. The glow behind the hero is beautiful and completely
          unreadable under 15px type, so darken exactly where the copy sits and
          leave the graph bright everywhere else.
          -z-10 is load-bearing: once Motion settles a revealed word to
          `transform: none` it stops being a stacking context and would paint
          BEHIND this absolutely-positioned scrim, dimming the headline. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse 52% 46% at 50% 54%, rgba(0,0,0,0.86) 0%, rgba(0,0,0,0.62) 42%, transparent 78%)',
        }}
      />

      <Rise delay={0.1}>
        <div className="mb-8 inline-flex items-center gap-2 rounded-full glass px-3.5 py-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-neon-emerald opacity-70" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-neon-emerald" />
          </span>
          <span className="text-[11px] font-medium tracking-wide text-white/60">
            Semantic compliance for API-first teams
          </span>
        </div>
      </Rise>

      <h1 className="max-w-5xl text-center text-[clamp(2.6rem,8.2vw,7.5rem)] font-semibold leading-[0.92] tracking-[-0.045em]">
        <RevealText text="Specification drift," className="block" />
        <RevealText text="made visible." className="gradient-text block" delay={0.18} />
      </h1>

      <Rise delay={0.55} className="mt-8 max-w-2xl">
        <p className="text-center text-[15px] leading-relaxed text-white/55 sm:text-lg">
          A semantic traceability engine that maps every business requirement to the API
          endpoints implementing it — and blocks the pull request the moment they diverge.
        </p>
      </Rise>

      <Rise delay={0.68} className="mt-10">
        <div className="flex flex-col items-center gap-3 sm:flex-row">
          <Link to={isAuthenticated ? '/dashboard' : '/login'} className="btn-primary group px-6 py-3">
            {isAuthenticated ? 'Open the dashboard' : 'Launch the engine'}
            <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" />
          </Link>
          <a href="#how" onClick={(e) => onJump(e, '#how')} className="btn-secondary px-6 py-3">
            See how it works
          </a>
        </div>
      </Rise>

      {/* Measured facts, not marketing adjectives */}
      <Rise delay={0.82} className="mt-14 w-full max-w-3xl">
        <div className="grid grid-cols-3 divide-x divide-white/8 rounded-2xl glass glass-edge py-5">
          {[
            ['768', 'dim embeddings'],
            ['0.71', 'F1 vs 0.34 TF-IDF'],
            ['1.00', 'recall on ground truth'],
          ].map(([v, l]) => (
            <div key={l} className="px-4 text-center">
              <div className="tnum text-2xl font-semibold tracking-tight sm:text-3xl">{v}</div>
              <div className="mt-1 text-[11px] uppercase tracking-[0.14em] text-white/35">{l}</div>
            </div>
          ))}
        </div>
      </Rise>

      <motion.a
        href="#problem"
        onClick={(e) => onJump(e, '#problem')}
        aria-label="Scroll to the problem section"
        className="absolute bottom-8 text-white/25 transition-colors hover:text-white/60"
        animate={{ y: [0, 7, 0] }}
        transition={{ repeat: Infinity, duration: 2.4, ease: 'easeInOut' }}
      >
        <ChevronDown size={22} />
      </motion.a>
    </section>
  )
}

/* ===========================================================================
   PROBLEM — three massive statement cards
   =========================================================================== */

const PROBLEMS = [
  {
    n: '01',
    word: 'Silent',
    tint: '251,113,133',
    icon: ShieldAlert,
    body: 'A required field disappears from a refund payload. No test fails. No linter fires. The suite is green and the audit trail is already broken.',
  },
  {
    n: '02',
    word: 'Cumulative',
    tint: '251,191,36',
    icon: Layers,
    body: 'Each sprint moves the contract a few degrees. Fifty sprints later the implementation and the requirement share a domain and nothing else.',
  },
  {
    n: '03',
    word: 'Asymmetric',
    tint: '34,211,238',
    icon: Boxes,
    body: 'Analysts write "idempotent processing for financial audit." Engineers write transaction_id. No keyword search will ever connect the two.',
  },
]

function ProblemSection() {
  return (
    <section id="problem" className="relative scroll-mt-24 px-6 py-32 sm:py-44">
      <div className="mx-auto max-w-6xl">
        <Rise>
          <Kicker>The problem</Kicker>
        </Rise>

        <h2 className="mt-6 max-w-4xl text-[clamp(2rem,5.2vw,4rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
          <RevealText text="Every team already has traceability." className="block text-white" />
          <RevealText text="It is stored in people's heads." className="block text-white/35" delay={0.1} />
        </h2>

        <Rise delay={0.15}>
          <p className="mt-7 max-w-xl text-[15px] leading-relaxed text-white/45">
            Link-based tools track that a requirement <em className="not-italic text-white/70">was</em> implemented.
            None of them verify that it <em className="not-italic text-white/70">still is</em>.
          </p>
        </Rise>

        <div className="mt-16 grid gap-5 md:grid-cols-3">
          {PROBLEMS.map((p, i) => (
            <Rise key={p.n} delay={i * 0.1}>
              <SpotlightCard tint={p.tint} className="h-full p-7">
                <div className="flex items-start justify-between">
                  <span className="font-mono text-[11px] tracking-[0.2em] text-white/25">{p.n}</span>
                  <p.icon size={17} style={{ color: `rgb(${p.tint})` }} className="opacity-70" />
                </div>
                <h3
                  className="mt-8 text-[2rem] font-semibold tracking-[-0.03em]"
                  style={{ color: `rgb(${p.tint})` }}
                >
                  {p.word}
                </h3>
                <p className="mt-3 text-[14px] leading-relaxed text-white/50">{p.body}</p>
              </SpotlightCard>
            </Rise>
          ))}
        </div>
      </div>
    </section>
  )
}

/* ===========================================================================
   HOW IT WORKS — pipeline + the PR→diagnosis morph
   =========================================================================== */

const STAGES = [
  ['Ingest', 'SRS prose and OpenAPI specs are parsed into requirement and endpoint units.'],
  ['Embed', 'Both sides are encoded into one 768-dimensional space by all-mpnet-base-v2.'],
  ['Align', 'Cosine similarity builds an N×M matrix. Coverage gaps surface immediately.'],
  ['Gate', 'A version delta below threshold blocks the PR and writes the diagnosis.'],
]

function PipelineSection() {
  return (
    <section id="how" className="relative scroll-mt-24 px-6 py-32 sm:py-40">
      <div className="mx-auto max-w-6xl">
        <Rise>
          <Kicker>How it works</Kicker>
        </Rise>

        <h2 className="mt-6 max-w-3xl text-[clamp(2rem,5.2vw,4rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
          <RevealText text="Four stages." className="block" />
          <RevealText text="No manual linking." className="gradient-text block" delay={0.1} />
        </h2>

        <div className="mt-16 grid gap-px overflow-hidden rounded-3xl bg-white/8 md:grid-cols-4">
          {STAGES.map(([title, body], i) => (
            <Rise key={title} delay={i * 0.08} className="h-full">
              <div className="group h-full bg-surface-950 p-7 transition-colors duration-300 hover:bg-surface-900">
                <div className="flex items-center gap-2.5">
                  <span className="grid h-6 w-6 place-items-center rounded-md bg-white/6 font-mono text-[10px] text-white/50 ring-1 ring-white/10">
                    {i + 1}
                  </span>
                  <h3 className="text-[15px] font-semibold tracking-tight">{title}</h3>
                </div>
                <p className="mt-4 text-[13.5px] leading-relaxed text-white/45">{body}</p>
                <div className="mt-6 h-px w-full bg-linear-to-r from-neon-cyan/40 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
              </div>
            </Rise>
          ))}
        </div>
      </div>
    </section>
  )
}

/**
 * Scroll-driven teaser: a failing PR check morphs into the AI diagnosis card.
 * Driven by a scrubbed GSAP timeline registered in the parent.
 */
function DemoSection() {
  return (
    <section id="demo" className="relative scroll-mt-24 px-6 py-20 sm:py-24">
      <div className="mx-auto max-w-5xl">
        <Rise>
          <Kicker>In the pull request</Kicker>
        </Rise>
        <h2 className="mt-6 max-w-3xl text-[clamp(1.8rem,4.4vw,3.2rem)] font-semibold leading-[1.05] tracking-[-0.04em]">
          <RevealText text="The check fails before the reviewer opens it." />
        </h2>

        {/* Stage: both cards overlap; GSAP scrubs one out and the other in */}
        <div data-demo-stage className="relative mt-12 min-h-[23rem] sm:min-h-[20rem]">
          {/* ── Card A: the failing check ───────────────────────────────── */}
          <div data-demo-pr className="absolute inset-x-0 top-0">
            <div className="overflow-hidden rounded-2xl glass glass-edge">
              <div className="flex items-center gap-3 border-b border-white/8 px-5 py-3.5">
                <GitPullRequest size={15} className="text-noncompliant" />
                <span className="text-[13px] font-medium">
                  Replace client transaction_id with server refund_reference
                </span>
                <span className="ml-auto font-mono text-[11px] text-white/30">#42</span>
              </div>

              <div className="divide-y divide-white/6">
                <div className="flex items-center gap-3 px-5 py-3">
                  <Check size={14} className="shrink-0 text-compliant" />
                  <span className="text-[13px] text-white/60">unit tests</span>
                  <span className="ml-auto font-mono text-[11px] text-compliant">142 passed</span>
                </div>
                <div className="flex items-center gap-3 px-5 py-3">
                  <Check size={14} className="shrink-0 text-compliant" />
                  <span className="text-[13px] text-white/60">openapi lint</span>
                  <span className="ml-auto font-mono text-[11px] text-compliant">no errors</span>
                </div>
                <div className="flex items-center gap-3 bg-noncompliant/6 px-5 py-3">
                  <X size={14} className="shrink-0 text-noncompliant" />
                  <span className="text-[13px] font-medium text-white">semantic compliance</span>
                  <span className="ml-auto font-mono text-[11px] text-noncompliant">REQ-04 regressed</span>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-white/8 px-5 py-3.5 font-mono text-[11px]">
                <span className="text-white/35">
                  v1 <span className="text-compliant">0.810</span>
                </span>
                <span className="text-white/35">
                  v2 <span className="text-noncompliant">0.749</span>
                </span>
                <span className="text-white/35">
                  Δ <span className="text-noncompliant">−0.061</span>
                </span>
                <span className="badge-noncompliant ml-auto">Merge blocked</span>
              </div>
            </div>
          </div>

          {/* ── Card B: the AI diagnosis ────────────────────────────────── */}
          <div data-demo-ai className="absolute inset-x-0 top-0 opacity-0">
            <Tilt
              tiltMaxAngleX={4}
              tiltMaxAngleY={4}
              perspective={1400}
              transitionSpeed={1600}
              scale={1.01}
              glareEnable
              glareMaxOpacity={0.08}
              glareColor="#22d3ee"
              glarePosition="all"
              glareBorderRadius="16px"
              className="rounded-2xl"
            >
              <div className="overflow-hidden rounded-2xl glass-strong glass-edge">
                <div className="flex items-center gap-2.5 border-b border-white/8 px-5 py-3.5">
                  <Sparkles size={14} className="text-neon-cyan" />
                  <span className="text-[13px] font-medium">AI root-cause analysis</span>
                  <span className="ml-auto font-mono text-[10px] text-white/25">REQ-04 · gpt-4o-mini</span>
                </div>

                <div className="space-y-5 p-6">
                  {[
                    [
                      'What changed',
                      'The required transaction_id field was removed from the POST /refund request body and replaced with a server-generated refund_reference.',
                    ],
                    [
                      'Why it violates the requirement',
                      'REQ-04 mandates a client-provided identifier for idempotent processing. A server-generated reference cannot deduplicate a retried request, so the audit trail breaks.',
                    ],
                    [
                      'Remediation',
                      'Restore transaction_id as a required string on the request body and echo it in the refund receipt response.',
                    ],
                  ].map(([label, body], i) => (
                    <div key={label} className="flex gap-4">
                      <span className="mt-0.5 font-mono text-[11px] text-neon-cyan/70">{`0${i + 1}`}</span>
                      <div>
                        <div className="text-[12px] font-semibold uppercase tracking-[0.11em] text-white/45">
                          {label}
                        </div>
                        <p className="mt-1.5 text-[13.5px] leading-relaxed text-white/70">{body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Tilt>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ===========================================================================
   RESULTS
   =========================================================================== */

const METRICS = [
  { to: 0.71, decimals: 2, label: 'F1 score', note: 'TF-IDF baseline: 0.34' },
  { to: 1.0, decimals: 2, label: 'Recall', note: 'every ground-truth link found' },
  { to: 1.5, decimals: 1, suffix: 's', label: 'Matrix generation', note: '10 × 20, warm model, CPU' },
  { to: 100, decimals: 0, suffix: '%', label: 'Explainability', note: 'every flag carries a diagnosis' },
]

function ResultsSection() {
  return (
    <section id="results" className="relative scroll-mt-24 px-6 py-32 sm:py-40">
      <div className="mx-auto max-w-6xl">
        <Rise>
          <Kicker>Measured, not claimed</Kicker>
        </Rise>

        <h2 className="mt-6 max-w-3xl text-[clamp(2rem,5.2vw,4rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
          <RevealText text="Benchmarked against a" className="block" />
          <RevealText text="lexical baseline." className="block text-white/35" delay={0.1} />
        </h2>

        <div className="mt-16 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {METRICS.map((m, i) => (
            <Rise key={m.label} delay={i * 0.08}>
              <SpotlightCard className="h-full p-7">
                <div className="text-[2.6rem] font-semibold leading-none tracking-[-0.04em] text-white">
                  <CountUp to={m.to} decimals={m.decimals} suffix={m.suffix ?? ''} />
                </div>
                <div className="mt-4 text-[13px] font-medium text-white/75">{m.label}</div>
                <div className="mt-1 text-[12px] text-white/35">{m.note}</div>
              </SpotlightCard>
            </Rise>
          ))}
        </div>

        <Rise delay={0.2}>
          <p className="mt-8 max-w-2xl text-[13px] leading-relaxed text-white/30">
            Synthetic e-commerce corpus: 10 requirements, 20 endpoints, 200 hand-annotated
            relevance labels. Retrieval threshold τ = 0.40 applied identically to both methods.
          </p>
        </Rise>
      </div>
    </section>
  )
}

/* ===========================================================================
   CTA + FOOTER
   =========================================================================== */

function CTASection() {
  const { isAuthenticated } = useAuth()
  return (
    <section className="relative px-6 py-32">
      <div className="mx-auto max-w-4xl">
        <div className="relative overflow-hidden rounded-[2rem] glass-strong glass-edge noise px-8 py-20 text-center sm:px-16">
          <div
            aria-hidden
            className="pointer-events-none absolute -top-1/2 left-1/2 h-[36rem] w-[36rem] -translate-x-1/2 rounded-full opacity-50 blur-3xl animate-aurora"
            style={{
              background:
                'radial-gradient(circle, rgba(34,211,238,0.16), rgba(52,211,153,0.08) 45%, transparent 70%)',
            }}
          />
          <div className="relative">
            <h2 className="text-[clamp(1.9rem,4.6vw,3.4rem)] font-semibold leading-[1.04] tracking-[-0.04em]">
              <RevealText text="Find the drift" className="block" />
              <RevealText text="before the auditor does." className="gradient-text block" delay={0.1} />
            </h2>
            <Rise delay={0.25}>
              <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <Link to={isAuthenticated ? '/dashboard' : '/login'} className="btn-primary group px-7 py-3">
                  {isAuthenticated ? 'Open the dashboard' : 'Launch the engine'}
                  <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" />
                </Link>
                <a
                  href="http://localhost:8000/api/docs"
                  target="_blank"
                  rel="noreferrer"
                  className="btn-secondary px-7 py-3"
                >
                  API reference
                  <ArrowUpRight size={15} />
                </a>
              </div>
            </Rise>
          </div>
        </div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="border-t border-white/6 px-6 py-10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 sm:flex-row">
        <div className="flex items-center gap-2.5">
          <span className="relative grid h-6 w-6 place-items-center rounded-md bg-white/8 ring-1 ring-white/10">
            <span className="absolute h-1 w-1 -translate-x-0.5 -translate-y-0.5 rounded-full bg-neon-cyan" />
            <span className="absolute h-1 w-1 translate-x-0.5 translate-y-0.5 rounded-full bg-neon-emerald" />
          </span>
          <span className="text-[12px] text-white/40">
            AI-Assisted Requirements Traceability — B.Tech Capstone
          </span>
        </div>
        <div className="flex items-center gap-5 text-[12px] text-white/30">
          <span className="font-mono">all-mpnet-base-v2</span>
          <span className="font-mono">Qdrant</span>
          <span className="font-mono">FastAPI</span>
          <Github size={14} className="transition-colors hover:text-white/60" />
        </div>
      </div>
    </footer>
  )
}

/* ===========================================================================
   PAGE
   =========================================================================== */

export default function LandingPage() {
  const reduced = usePrefersReducedMotion()
  const lenis = useSmoothScroll(!reduced)

  const rootRef = useRef(null)
  const canvasWrapRef = useRef(null)

  // Shared with the WebGL scene — written here, read inside useFrame.
  const progressRef = useRef(0)
  const mouseRef = useRef({ x: 0, y: 0 })

  // Only mount WebGL once the browser is idle; the hero text should never wait
  // on a 280 KB three.js chunk.
  const [showCanvas, setShowCanvas] = useState(false)
  useEffect(() => {
    const id = window.requestIdleCallback
      ? window.requestIdleCallback(() => setShowCanvas(true), { timeout: 1200 })
      : window.setTimeout(() => setShowCanvas(true), 380)
    return () => (window.cancelIdleCallback ? window.cancelIdleCallback(id) : clearTimeout(id))
  }, [])

  // Mouse parallax. The canvas is pointer-events:none so R3F's own `pointer`
  // never updates — we track it on window instead and hand over a ref.
  useEffect(() => {
    if (reduced) return undefined
    const onMove = (e) => {
      mouseRef.current.x = (e.clientX / window.innerWidth) * 2 - 1
      mouseRef.current.y = (e.clientY / window.innerHeight) * 2 - 1
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => window.removeEventListener('pointermove', onMove)
  }, [reduced])

  // Anchor navigation routed through Lenis so it inherits the same easing.
  const jump = useCallback(
    (e, href) => {
      e.preventDefault()
      const el = document.querySelector(href)
      if (!el) return
      if (lenis) lenis.scrollTo(el, { offset: -88, duration: 1.15 })
      else el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' })
    },
    [lenis, reduced],
  )

  // ── GSAP scroll choreography ───────────────────────────────────────────────
  useLayoutEffect(() => {
    const ctx = gsap.context(() => {
      // 1. Master morph: cloud → matrix across the first ~1.4 viewports.
      //    Writes to a ref; the 3D scene samples it every frame.
      ScrollTrigger.create({
        trigger: '#top',
        start: 'top top',
        end: () => `+=${window.innerHeight * 1.4}`,
        scrub: true,
        onUpdate: (self) => {
          progressRef.current = self.progress
        },
      })

      // 2. Fade the canvas back once the reader reaches real content, so text
      //    always wins the contrast fight.
      if (canvasWrapRef.current) {
        gsap.fromTo(
          canvasWrapRef.current,
          { opacity: 1 },
          {
            opacity: 0.16,
            ease: 'none',
            scrollTrigger: {
              trigger: '#problem',
              start: 'top 85%',
              end: 'top 25%',
              scrub: true,
            },
          },
        )
      }

      // 3. The PR → diagnosis morph, scrubbed by scroll.
      const stage = document.querySelector('[data-demo-stage]')
      if (stage) {
        const tl = gsap.timeline({
          scrollTrigger: {
            trigger: stage,
            start: 'top 68%',
            end: 'top 18%',
            scrub: 0.6,
          },
        })
        tl.to('[data-demo-pr]', {
          opacity: 0,
          y: -26,
          scale: 0.965,
          filter: 'blur(7px)',
          ease: 'power2.in',
        }).fromTo(
          '[data-demo-ai]',
          { opacity: 0, y: 34, scale: 0.965, filter: 'blur(7px)' },
          { opacity: 1, y: 0, scale: 1, filter: 'blur(0px)', ease: 'power2.out' },
          '<0.12',
        )
      }
    }, rootRef)

    // Fonts and the 3D chunk both change layout height after first paint
    const refresh = () => ScrollTrigger.refresh()
    const t = setTimeout(refresh, 600)
    window.addEventListener('load', refresh)

    return () => {
      clearTimeout(t)
      window.removeEventListener('load', refresh)
      ctx.revert()
    }
  }, [showCanvas])

  return (
    <div ref={rootRef} className="relative min-h-screen overflow-clip bg-surface-950 noise">
      {/* ── Fixed WebGL backdrop ─────────────────────────────────────────── */}
      <div ref={canvasWrapRef} aria-hidden className="pointer-events-none fixed inset-0 z-0">
        {showCanvas && <NeuralGraph progressRef={progressRef} mouseRef={mouseRef} reduced={reduced} />}
      </div>

      {/* Engineering grid + horizon glow, dissolved at the edges */}
      <div aria-hidden className="pointer-events-none fixed inset-0 z-0 grid-bg mask-fade opacity-45" />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 z-0 h-[70vh]"
        style={{
          background:
            'radial-gradient(ellipse 70% 55% at 50% 0%, rgba(34,211,238,0.10), transparent 70%)',
        }}
      />

      {/* ── Content ──────────────────────────────────────────────────────── */}
      <div className="relative z-10">
        <Nav onJump={jump} />
        <main>
          <Hero onJump={jump} />

          <div className="border-y border-white/6 bg-surface-950/60 py-5 backdrop-blur-sm">
            <Marquee
              items={[
                'sentence-transformers',
                'OpenAPI 3.0',
                'Qdrant',
                'FastAPI',
                'cosine similarity',
                'GitHub Actions',
                'Precision · Recall · F1 · MAP',
                'React Three Fiber',
              ]}
            />
          </div>

          <ProblemSection />
          <PipelineSection />
          <DemoSection />
          <ResultsSection />
          <CTASection />
        </main>
        <Footer />
      </div>
    </div>
  )
}
