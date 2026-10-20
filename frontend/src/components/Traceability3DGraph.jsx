// frontend/src/components/Traceability3DGraph.jsx
// ---------------------------------------------------------------------------
// Interactive 3D requirement -> endpoint graph.
//
// Four things decide whether this reads or turns into a hairball. Each was
// diagnosed by screenshotting the real graph, not by eye:
//
//  1. LINK COUNT. The backend emits an edge for every pair scoring above
//     0.6 x threshold. On the sample data that is 71 edges of which only 15 are
//     at or above the compliance threshold — the other 56 were the spaghetti in
//     the middle. Default is strong links only; weak ones are opt-in.
//
//  2. A BOUNDED LAYOUT. This graph is a set of DISCONNECTED components (one
//     star per requirement) and no default d3 force holds separate components
//     together, so they push each other apart forever. Measured: the bounding
//     radius was still growing 15s in. Every attempt to frame the graph was
//     chasing a target that never stopped moving. `containForce` adds a weak
//     spring to the origin, which pins the layout (radius stable at ~130).
//
//  3. FRAMING THAT DOES NOT RACE. Do not hang the camera fit off onEngineStop —
//     with a never-settling sim it fires late or not at all. Fit on a fixed
//     schedule, computing the bounding box from live node coords each time.
//
//  4. SCALE RELATIVE TO THE LAYOUT. The sim does not settle to a predictable
//     size (spans of 143 to 560 world units were measured for the same 22
//     nodes), so any fixed node/label size is specks in one layout and
//     colliding slabs in another. Everything visual multiplies by
//     span / REFERENCE_SPAN.
//
// Endpoint identity comes from hover, the focus drawer, and the Coverage view —
// not from always-on sprites. Only requirements carry permanent labels.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import { motion, AnimatePresence } from 'motion/react'
import { Crosshair, Filter, Maximize2, Tag, X } from 'lucide-react'

const PALETTE = {
  requirement:         { color: 0x22d3ee, emissive: 0x0e7490, css: '#22d3ee', shape: '●', label: 'Requirement' },
  endpoint_v1:         { color: 0x34d399, emissive: 0x065f46, css: '#34d399', shape: '■', label: 'Covered endpoint' },
  endpoint_v1_drifted: { color: 0xfb7185, emissive: 0x9f1239, css: '#fb7185', shape: '◆', label: 'Drifted endpoint' },
  endpoint_v2_new:     { color: 0xfbbf24, emissive: 0x92400e, css: '#fbbf24', shape: '▲', label: 'New in v2' },
}
const paletteFor = (t) => PALETTE[t] || PALETTE.endpoint_v1

// World-space sizing. Nodes are ~16-20 units across, so a label a little over
// one node tall sits in proportion instead of dwarfing the graph.
const LABEL_WORLD_H = 11
const REQ_RADIUS = 10

// The span the sizes above are tuned against. The force layout does not settle
// to a predictable size — measured spans ranged from 143 to 560 world units for
// the same 22 nodes depending on how the components happened to pack — so fixed
// node and label sizes read as specks in one layout and as colliding slabs in
// another. Everything visual is scaled by (actual span / this) instead.
const REFERENCE_SPAN = 520

function geometryFor(type, s = 1) {
  switch (type) {
    case 'requirement':         return new THREE.SphereGeometry(REQ_RADIUS * s, 20, 20)
    case 'endpoint_v1_drifted': return new THREE.OctahedronGeometry(9 * s)
    case 'endpoint_v2_new':     return new THREE.TetrahedronGeometry(9.5 * s)
    default:                    return new THREE.BoxGeometry(11.5 * s, 11.5 * s, 11.5 * s)
  }
}

const _measure = typeof document !== 'undefined'
  ? document.createElement('canvas').getContext('2d')
  : null

/**
 * Text sprite sized in WORLD units. Rendered at high DPI for crispness, then
 * scaled so its height is exactly `worldH` regardless of string length.
 */
function makeLabelSprite(text, cssColor, worldH = LABEL_WORLD_H) {
  const fontPx = 40
  const padX = 14
  const padY = 8

  _measure.font = `600 ${fontPx}px Inter, system-ui, sans-serif`
  const textW = Math.ceil(_measure.measureText(text).width)

  const canvas = document.createElement('canvas')
  canvas.width = textW + padX * 2
  canvas.height = fontPx + padY * 2
  const ctx = canvas.getContext('2d')

  // Dark plate — white-on-black alone disappears where links cross behind it.
  ctx.fillStyle = 'rgba(4,4,5,0.82)'
  ctx.beginPath()
  if (typeof ctx.roundRect === 'function') ctx.roundRect(0, 0, canvas.width, canvas.height, 12)
  else ctx.rect(0, 0, canvas.width, canvas.height)
  ctx.fill()

  ctx.font = `600 ${fontPx}px Inter, system-ui, sans-serif`
  ctx.fillStyle = cssColor
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 1)

  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texture.needsUpdate = true

  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }),
  )
  // Height is fixed in world units; width follows the aspect ratio.
  const aspect = canvas.width / canvas.height
  sprite.scale.set(worldH * aspect, worldH, 1)
  return sprite
}

/** "POST /api/v1/auth/step-up/challenge" -> "POST /step-up/challenge" */
function shortEndpoint(id, max = 22) {
  const s = String(id).replace(/\/api\/v\d+(\.\d+)?/, '')
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

const idOf = (e) => (typeof e === 'object' && e !== null ? e.id : e)

export default function Traceability3DGraph({ nodes = [], edges = [], threshold = 0.55, onNodeClick }) {
  const fgRef = useRef()
  const wrapRef = useRef(null)
  const [selected, setSelected] = useState(null)
  const [showAllLabels, setShowAllLabels] = useState(false)
  const [showWeakLinks, setShowWeakLinks] = useState(false)
  const [worldScale, setWorldScale] = useState(1)
  const scaleRef = useRef(1)
  const [dims, setDims] = useState({ w: 0, h: 0 })

  // ForceGraph3D falls back to window.innerWidth/Height when width/height are
  // omitted, which inside an overflow-hidden panel pushes the graph's centre
  // out of view. Measure the container and size the canvas explicitly.
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return undefined
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setDims({ w: Math.round(width), h: Math.round(height) })
    })
    ro.observe(el)
    const r = el.getBoundingClientRect()
    setDims({ w: Math.round(r.width), h: Math.round(r.height) })
    return () => ro.disconnect()
  }, [])

  // --- link filtering: the single biggest readability lever -----------------
  // react-force-graph mutates links in place, swapping the string source/target
  // for live node references. Handing those mutated objects back on the next
  // filter change makes it resolve against a stale node set, so rebuild each
  // link as a fresh object with plain ids. Nodes are passed through untouched
  // so their simulated positions survive a toggle.
  const { visibleEdges, weakCount } = useMemo(() => {
    const strong = edges.filter((e) => (e.score ?? 1) >= threshold)
    const chosen = showWeakLinks ? edges : strong
    return {
      visibleEdges: chosen.map((e) => ({ ...e, source: idOf(e.source), target: idOf(e.target) })),
      weakCount: edges.length - strong.length,
    }
  }, [edges, threshold, showWeakLinks])

  // Drop nodes that end up with no visible link so the canvas is not littered
  // with orphans (keep every requirement — an unlinked one is the finding).
  const visibleNodes = useMemo(() => {
    const linked = new Set()
    visibleEdges.forEach((e) => { linked.add(idOf(e.source)); linked.add(idOf(e.target)) })
    return nodes.filter((n) => n.type === 'requirement' || linked.has(n.id))
  }, [nodes, visibleEdges])

  const graphData = useMemo(
    () => ({ nodes: visibleNodes, links: visibleEdges }),
    [visibleNodes, visibleEdges],
  )

  const neighbours = useMemo(() => {
    const map = new Map()
    const add = (a, b) => { if (!map.has(a)) map.set(a, new Set()); map.get(a).add(b) }
    visibleEdges.forEach((e) => {
      const s = idOf(e.source), t = idOf(e.target)
      if (s != null && t != null) { add(s, t); add(t, s) }
    })
    return map
  }, [visibleEdges])

  const focusIds = useMemo(() => {
    if (!selected) return null
    const set = new Set([selected.id])
    neighbours.get(selected.id)?.forEach((n) => set.add(n))
    return set
  }, [selected, neighbours])

  const labelledIds = useMemo(() => {
    if (showAllLabels) return null // null = label everything
    const set = new Set(visibleNodes.filter((n) => n.type === 'requirement').map((n) => n.id))
    focusIds?.forEach((id) => set.add(id))
    return set
  }, [visibleNodes, focusIds, showAllLabels])

  // A d3 force pulling every node gently toward the origin. Scaled by alpha so
  // it fades out in step with the rest of the simulation instead of slowly
  // collapsing the graph once everything else has cooled.
  // The spring is deliberately ANISOTROPIC: a weaker pull on x than on y lets
  // the cloud settle wider than it is tall, matching the panel's aspect. A
  // square cloud in a 16:9 panel has to be zoomed out to fit its height, which
  // is what left the graph small with dead space either side.
  const aspectRef = useRef(1.6)
  aspectRef.current = dims.w && dims.h ? Math.max(1, dims.w / dims.h) : 1.6

  const containForce = useMemo(() => {
    let simNodes = []
    const force = (alpha) => {
      const k = 0.10 * alpha
      // Equilibrium extent against inverse-square repulsion scales as k^(-1/3),
      // so the stiffness ratio has to be cubed to produce the extent ratio we
      // want. sqrt(aspect) only bought ~1.17x; aspect^1.5 lands on ~aspect.
      const s = Math.pow(aspectRef.current, 0.9)
      const kx = k / s
      const ky = k * s
      for (const n of simNodes) {
        n.vx -= n.x * kx
        n.vy -= n.y * ky
        if (typeof n.z === 'number' && typeof n.vz === 'number') n.vz -= n.z * k
      }
    }
    force.initialize = (ns) => { simNodes = ns }
    return force
  }, [])

  // --- layout ---------------------------------------------------------------
  // distanceMax is the important one. Filtering to strong links leaves several
  // requirements with no link at all, and an unlinked node feels nothing but
  // repulsion — with an unbounded range it accelerates away forever, blowing up
  // the bounding box that zoomToFit then has to accommodate. Capping repulsion
  // range lets the centring force win past that radius, so the graph stays a
  // compact cluster whatever the link filter is set to.
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || !visibleNodes.length) return
    fg.d3Force('charge')?.strength(-95).distanceMax(220)
    fg.d3Force('link')
      ?.distance((l) => 55 + (1 - Math.min(1, l.score ?? 0.5)) * 45)
      .strength(0.5)
    // Containment. This graph is a set of DISCONNECTED components (one star per
    // requirement) and nothing in the default force set holds separate
    // components together — they just push each other apart forever. Measured:
    // the bounding radius was still creeping 15s in, so every attempt to frame
    // the graph was chasing a target that never stopped moving. A weak spring
    // toward the origin gives the layout a real equilibrium, which is what makes
    // the framing below reliable rather than a race.
    fg.d3Force('contain', containForce)
    fg.d3ReheatSimulation?.()
  }, [visibleNodes, visibleEdges, containForce])

  const settledRef = useRef(false)

  // Deterministic fit.
  //
  // zoomToFit() kept cropping nodes: this graph is a set of DISCONNECTED
  // components (one star per requirement), and d3 lets disconnected components
  // drift apart indefinitely, so the box it measured was stale the moment it
  // was measured. Instead compute the bounding sphere from the live node
  // coordinates — react-force-graph mutates the node objects in place with
  // x/y/z — and place the camera at the distance that provably contains it.
  const fitView = useCallback((ms = 500) => {
    const fg = fgRef.current
    if (!fg) return
    // In planar mode (numDimensions=2) the simulation never assigns z, so it is
    // undefined rather than 0. Requiring a finite z here made this filter drop
    // every node and silently skip the fit entirely.
    const pts = visibleNodes
      .filter((n) => Number.isFinite(n.x) && Number.isFinite(n.y))
      .map((n) => ({ x: n.x, y: n.y, z: Number.isFinite(n.z) ? n.z : 0 }))
    if (!pts.length || !dims.w || !dims.h) return

    // Bounding BOX, centred on the box midpoint rather than the centroid — a
    // centroid is dragged around by whichever cluster has the most nodes, which
    // pushes the graph off to one side. Fitting per-axis instead of to a
    // bounding sphere matters because this cloud is far from spherical: a sphere
    // fit sized for the tall axis leaves the wide axis mostly empty.
    let minX = Infinity, maxX = -Infinity
    let minY = Infinity, maxY = -Infinity
    let minZ = Infinity, maxZ = -Infinity
    for (const n of pts) {
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y
      if (n.z < minZ) minZ = n.z; if (n.z > maxZ) maxZ = n.z
    }
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    const cz = (minZ + maxZ) / 2

    // Adapt every visual to how large the layout actually came out.
    const span = Math.max(maxX - minX, maxY - minY, 1)
    const nextScale = Math.min(2.2, Math.max(0.4, span / REFERENCE_SPAN))
    if (Math.abs(nextScale - scaleRef.current) > 0.06) {
      scaleRef.current = nextScale
      setWorldScale(nextScale)
    }

    const PAD = 30 * scaleRef.current // node mesh + its label plate
    const halfW = Math.max((maxX - minX) / 2 + PAD, 40)
    const halfH = Math.max((maxY - minY) / 2 + PAD, 40)
    const halfD = (maxZ - minZ) / 2

    // react-force-graph's perspective camera is 50deg vertical.
    const vFov = (50 * Math.PI) / 180
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * (dims.w / dims.h))
    const dist = Math.max(halfH / Math.tan(vFov / 2), halfW / Math.tan(hFov / 2)) + halfD * 0.5 + 20

    fg.cameraPosition({ x: cx, y: cy, z: cz + dist }, { x: cx, y: cy, z: cz }, ms)
  }, [visibleNodes, dims])

  // Do NOT hang framing off onEngineStop. With disconnected components the
  // simulation's alpha decays but never fully settles, so that callback can
  // fire late or not at all — measured here as a bounding radius still creeping
  // (250 -> 256) fifteen seconds in, with the camera left at its default and
  // nodes hanging off the frame. Re-fit on a fixed schedule instead: each pass
  // re-reads live positions, so the last one lands on a graph that has stopped
  // moving in any way that matters.
  const FIT_SCHEDULE = [350, 1100, 2400, 4200]
  useEffect(() => {
    if (!visibleNodes.length || !dims.w || !dims.h) return undefined
    const timers = FIT_SCHEDULE.map((delay, i) =>
      setTimeout(() => { settledRef.current = true; fitView(i === 0 ? 0 : 420) }, delay))
    return () => timers.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleNodes, dims.w, dims.h, showWeakLinks])

  // Fitting before the simulation settles locks the camera onto whatever tiny
  // bounding box the nodes happen to occupy at that instant, which is how the
  // view ended up zoomed into two nodes. Only refit on resize once settled.
  useEffect(() => {
    if (!dims.w || !dims.h || !settledRef.current) return undefined
    const t = setTimeout(() => fitView(300), 120)
    return () => clearTimeout(t)
  }, [dims.w, dims.h, fitView])

  // A filter change relayouts the graph, so allow a fresh fit.
  useEffect(() => { settledRef.current = false }, [showWeakLinks])

  const handleNodeClick = useCallback((node) => {
    const next = node.id === selected?.id ? null : node
    setSelected(next)
    onNodeClick?.(next)
    if (next) {
      const r = Math.hypot(node.x, node.y, node.z) || 1
      const k = 1 + 190 / r
      fgRef.current?.cameraPosition({ x: node.x * k, y: node.y * k, z: node.z * k }, node, 700)
    }
  }, [selected, onNodeClick])

  const nodeThreeObject = useCallback((node) => {
    const pal = paletteFor(node.type)
    const dimmed = focusIds ? !focusIds.has(node.id) : false
    const isReq = node.type === 'requirement'

    const mesh = new THREE.Mesh(
      geometryFor(node.type, worldScale),
      new THREE.MeshLambertMaterial({
        color: pal.color,
        emissive: pal.emissive,
        emissiveIntensity: node.id === selected?.id ? 1.0 : dimmed ? 0.05 : 0.5,
        transparent: dimmed,
        opacity: dimmed ? 0.1 : 1,
      }),
    )

    const wants = labelledIds === null || labelledIds.has(node.id)
    if (wants && !dimmed) {
      // Requirement labels are just the ID — short, high-contrast, few of them.
      // The title lives in the tooltip and the drawer.
      const text = isReq ? node.id : shortEndpoint(node.label || node.id)
      const sprite = makeLabelSprite(
        text,
        isReq ? '#ffffff' : pal.css,
        (isReq ? LABEL_WORLD_H : LABEL_WORLD_H * 0.78) * worldScale,
      )
      sprite.position.set(0, (isReq ? REQ_RADIUS + 11 : 15) * worldScale, 0)
      mesh.add(sprite)
    }
    return mesh
  }, [selected, focusIds, labelledIds, worldScale])

  const linkColor = useCallback((link) => {
    if (!focusIds) {
      return (link.score ?? 0) >= threshold ? 'rgba(52,211,153,0.30)' : 'rgba(255,255,255,0.07)'
    }
    const on = focusIds.has(idOf(link.source)) && focusIds.has(idOf(link.target))
    return on ? '#22d3ee' : 'rgba(255,255,255,0.02)'
  }, [focusIds, threshold])

  const linkWidth = useCallback((link) => {
    if (!focusIds) return (link.score ?? 0) >= threshold ? 0.7 : 0.25
    const on = focusIds.has(idOf(link.source)) && focusIds.has(idOf(link.target))
    return on ? 1.6 : 0.15
  }, [focusIds, threshold])

  const nodeLabel = useCallback((node) => {
    const pal = paletteFor(node.type)
    const sub = node.title && node.title !== node.id ? node.title : ''
    const deg = neighbours.get(node.id)?.size ?? 0
    return `
      <div style="background:#0a0b0d;border:1px solid rgba(255,255,255,.14);border-radius:10px;
        padding:8px 10px;font-family:Inter,system-ui,sans-serif;max-width:290px;
        box-shadow:0 8px 28px rgba(0,0,0,.65)">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">
          <span style="color:${pal.css};font-size:12px">${pal.shape}</span>
          <span style="color:rgba(255,255,255,.42);font-size:10px;text-transform:uppercase;
            letter-spacing:.1em">${pal.label}</span>
        </div>
        <div style="color:#fff;font-size:12.5px;font-weight:600;word-break:break-word">${node.label || node.id}</div>
        ${sub ? `<div style="color:rgba(255,255,255,.5);font-size:11px;margin-top:3px">${sub}</div>` : ''}
        <div style="color:rgba(255,255,255,.3);font-size:10.5px;margin-top:5px">
          ${deg} link${deg === 1 ? '' : 's'} · click to focus
        </div>
      </div>`
  }, [neighbours])

  const counts = useMemo(() => {
    const c = {}
    visibleNodes.forEach((n) => { c[n.type] = (c[n.type] || 0) + 1 })
    return c
  }, [visibleNodes])

  if (!nodes.length) {
    return (
      <div className="flex items-center justify-center h-full text-white/40 text-sm p-6 text-center">
        Run an analysis to generate the traceability graph.
      </div>
    )
  }

  const IconBtn = ({ on, title, onClick, children }) => (
    <button
      onClick={onClick}
      title={title}
      className={`glass rounded-lg p-2 transition-colors ${on ? 'text-brand-400 ring-1 ring-brand-400/30' : 'text-white/40 hover:text-white'}`}
    >
      {children}
    </button>
  )

  return (
    <div ref={wrapRef} className="relative w-full h-full">
      {/* Legend / census */}
      <div className="absolute top-3 left-3 z-10 glass rounded-xl px-3 py-2.5 flex flex-col gap-1.5 pointer-events-none">
        {Object.entries(PALETTE).map(([type, pal]) => (counts[type] ? (
          <div key={type} className="flex items-center gap-2 text-[11px] whitespace-nowrap">
            <span style={{ color: pal.css }} className="text-sm leading-none w-3">{pal.shape}</span>
            <span className="text-white/55">{pal.label}</span>
            <span className="text-white/25 tnum ml-auto pl-2">{counts[type]}</span>
          </div>
        ) : null))}
        <div className="border-t border-white/8 mt-0.5 pt-1.5 text-[10px] text-white/30">
          {visibleEdges.length} link{visibleEdges.length === 1 ? '' : 's'} shown
          {!showWeakLinks && weakCount > 0 && ` · ${weakCount} weak hidden`}
        </div>
      </div>

      {/* Controls */}
      <div className="absolute top-3 right-3 z-10 flex gap-1.5">
        <IconBtn
          on={showWeakLinks}
          title={showWeakLinks ? `Hide ${weakCount} weak links (below ${threshold})` : `Show ${weakCount} weak links`}
          onClick={() => setShowWeakLinks((v) => !v)}
        >
          <Filter size={14} />
        </IconBtn>
        <IconBtn
          on={showAllLabels}
          title={showAllLabels ? 'Label requirements only' : 'Label every node'}
          onClick={() => setShowAllLabels((v) => !v)}
        >
          <Tag size={14} />
        </IconBtn>
        <IconBtn title="Reset view" onClick={() => { setSelected(null); setTimeout(() => fitView(600), 60) }}>
          <Maximize2 size={14} />
        </IconBtn>
      </div>

      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            className="absolute top-3 left-1/2 -translate-x-1/2 z-10 glass-strong rounded-full
                       pl-3 pr-1.5 py-1.5 flex items-center gap-2 text-[11px]"
          >
            <Crosshair size={12} className="text-brand-400" />
            <span className="text-white/70">
              <span className="text-white font-semibold">{selected.id}</span>
              {' · '}{neighbours.get(selected.id)?.size ?? 0} linked
            </span>
            <button onClick={() => setSelected(null)} className="text-white/40 hover:text-white p-1">
              <X size={12} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="absolute bottom-3 left-3 z-10 glass rounded-lg px-2.5 py-1.5 text-[10px] text-white/35 pointer-events-none">
        Drag to orbit · Scroll to zoom · Click a node to focus
      </div>

      {dims.w > 0 && dims.h > 0 && (
        <ForceGraph3D
          ref={fgRef}
          width={dims.w}
          height={dims.h}
          graphData={graphData}
          /* Planar layout. In 3D the z-spread made near nodes render large and
             far ones tiny, and forced the camera back to clear the depth. Coplanar
             nodes read at one consistent size and frame exactly. Still orbitable. */
          numDimensions={2}
          nodeThreeObject={nodeThreeObject}
          nodeThreeObjectExtend={false}
          nodeLabel={nodeLabel}
          onNodeClick={handleNodeClick}
          onBackgroundClick={() => setSelected(null)}
          onEngineStop={() => { settledRef.current = true; fitView(420) }}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkOpacity={0.6}
          linkCurvature={0}
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          warmupTicks={60}
          cooldownTicks={220}
        />
      )}

      <AnimatePresence>
        {selected && (
          <motion.aside
            key="drawer"
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 32 }}
            className="absolute top-0 right-0 h-full w-72 glass-strong border-l border-white/10 p-5 overflow-y-auto z-20"
          >
            <div className="flex items-start justify-between gap-2 mb-4">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <span style={{ color: paletteFor(selected.type).css }}>{paletteFor(selected.type).shape}</span>
                  <span className="text-[10px] uppercase tracking-[0.14em] text-white/40">
                    {paletteFor(selected.type).label}
                  </span>
                </div>
                <h3 className="font-bold text-white text-sm break-words">{selected.label || selected.id}</h3>
              </div>
              <button onClick={() => setSelected(null)} className="text-white/40 hover:text-white shrink-0">
                <X size={16} />
              </button>
            </div>

            {selected.title && selected.title !== selected.id && (
              <p className="text-xs text-white/60 leading-relaxed mb-4">{selected.title}</p>
            )}

            <p className="text-[10px] uppercase tracking-[0.14em] text-white/35 mb-2">
              Linked nodes ({neighbours.get(selected.id)?.size ?? 0})
            </p>
            <ul className="flex flex-col gap-1">
              {[...(neighbours.get(selected.id) || [])].slice(0, 24).map((nid) => {
                const n = visibleNodes.find((x) => x.id === nid)
                return (
                  <li key={nid}>
                    <button
                      onClick={() => n && handleNodeClick(n)}
                      className="w-full text-left text-[11px] font-mono text-white/55 hover:text-white
                                 hover:bg-white/5 rounded-md px-2 py-1.5 transition-colors break-all flex items-start gap-1.5"
                    >
                      <span style={{ color: paletteFor(n?.type).css }} className="shrink-0">
                        {paletteFor(n?.type).shape}
                      </span>
                      {n?.label || nid}
                    </button>
                  </li>
                )
              })}
            </ul>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  )
}
