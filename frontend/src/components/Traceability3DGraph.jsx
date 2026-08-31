// frontend/src/components/Traceability3DGraph.jsx
// Interactive 3D force-directed graph using react-force-graph-3d
// Nodes: blue spheres (req), green cubes (compliant EP), red octahedrons (drifted EP)
// Edges: colored by similarity score, thickness ∝ score

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import { motion, AnimatePresence } from 'motion/react'
import { X } from 'lucide-react'

const NODE_MATERIALS = {
  requirement:          new THREE.MeshLambertMaterial({ color: 0x3b82f6, emissive: 0x1e40af, emissiveIntensity: 0.4 }),
  endpoint_v1:          new THREE.MeshLambertMaterial({ color: 0x22c55e, emissive: 0x14532d, emissiveIntensity: 0.3 }),
  endpoint_v1_drifted:  new THREE.MeshLambertMaterial({ color: 0xef4444, emissive: 0x7f1d1d, emissiveIntensity: 0.5 }),
  endpoint_v2_new:      new THREE.MeshLambertMaterial({ color: 0xf59e0b, emissive: 0x78350f, emissiveIntensity: 0.3 }),
}

function getGeometry(type) {
  switch (type) {
    case 'requirement':         return new THREE.SphereGeometry(5, 16, 16)
    case 'endpoint_v1_drifted': return new THREE.OctahedronGeometry(5)
    case 'endpoint_v2_new':     return new THREE.TetrahedronGeometry(5)
    default:                    return new THREE.BoxGeometry(7, 7, 7)
  }
}

export default function Traceability3DGraph({ nodes = [], edges = [], onNodeClick }) {
  const fgRef   = useRef()
  const wrapRef = useRef(null)
  const [selected, setSelected] = useState(null)
  const [tooltip, setTooltip]   = useState(null)
  const [dims, setDims] = useState({ w: 0, h: 0 })

  // ForceGraph3D falls back to window.innerWidth/innerHeight when width/height
  // are not given. Inside this overflow-hidden panel that makes the canvas far
  // larger than its container, pushing the graph's centre outside the visible
  // area -- the panel looks empty. Measure the container and size it explicitly.
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

  // Highlight node and connected edges on click
  const handleNodeClick = useCallback((node) => {
    setSelected(node.id === selected?.id ? null : node)
    onNodeClick?.(node)
    // Aim camera at clicked node
    fgRef.current?.cameraPosition(
      { x: node.x, y: node.y, z: node.z + 120 },
      node,
      800
    )
  }, [selected, onNodeClick])

  const nodeThreeObject = useCallback((node) => {
    const geo = getGeometry(node.type)
    const mat = NODE_MATERIALS[node.type] || NODE_MATERIALS.endpoint_v1
    const mesh = new THREE.Mesh(geo, mat.clone())

    // Glow for selected
    if (selected && node.id === selected.id) {
      mesh.material.emissiveIntensity = 0.9
    }

    // Label sprite
    const canvas = document.createElement('canvas')
    canvas.width  = 256
    canvas.height = 64
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = 'rgba(0,0,0,0)'
    ctx.fillRect(0, 0, 256, 64)
    ctx.font = 'bold 22px Inter, sans-serif'
    ctx.fillStyle = '#ffffff'
    ctx.textAlign = 'center'
    ctx.fillText(node.label?.slice(0, 24) || node.id, 128, 40)
    const texture = new THREE.CanvasTexture(canvas)
    const sprite  = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }))
    sprite.scale.set(60, 15, 1)
    sprite.position.set(0, 14, 0)
    mesh.add(sprite)

    return mesh
  }, [selected])

  const linkColor = useCallback((link) => {
    if (selected) {
      const isConnected = link.source?.id === selected.id || link.target?.id === selected.id
      return isConnected ? (link.color || '#3b82f6') : 'rgba(255,255,255,0.05)'
    }
    return link.color || '#3b82f6'
  }, [selected])

  const linkWidth = useCallback((link) => link.width ?? 1, [])
  // NOTE: react-force-graph's linkOpacity accepts a NUMBER only, never an
  // accessor. Per-link alpha is carried by linkColor's rgba value instead.
  const linkOpacity = selected ? 0.9 : 0.6

  if (!nodes.length) {
    return (
      <div className="flex items-center justify-center h-full text-slate-500 text-sm">
        <div className="text-center">
          <div className="text-4xl mb-3">🕸️</div>
          <p>Run analysis to generate the 3D traceability graph.</p>
        </div>
      </div>
    )
  }

  return (
    <div ref={wrapRef} className="relative w-full h-full">
      {/* Legend */}
      <div className="absolute top-4 left-4 z-10 glass rounded-xl p-3 flex flex-col gap-2 text-xs">
        {[
          { color: '#3b82f6', label: 'Requirement',     shape: '●' },
          { color: '#22c55e', label: 'Compliant EP',    shape: '■' },
          { color: '#ef4444', label: 'Drifted EP',      shape: '◆' },
          { color: '#f59e0b', label: 'New/Unknown EP',  shape: '▲' },
        ].map(({ color, label, shape }) => (
          <div key={label} className="flex items-center gap-2">
            <span style={{ color }} className="text-base leading-none">{shape}</span>
            <span className="text-slate-400">{label}</span>
          </div>
        ))}
      </div>

      {/* Controls hint */}
      <div className="absolute bottom-4 left-4 z-10 glass rounded-xl px-3 py-2 text-[10px] text-slate-500">
        Click node to focus · Drag to orbit · Scroll to zoom
      </div>

      {dims.w > 0 && dims.h > 0 && (
      <ForceGraph3D
        ref={fgRef}
        width={dims.w}
        height={dims.h}
        graphData={{ nodes, links: edges }}
        nodeThreeObject={nodeThreeObject}
        nodeThreeObjectExtend={false}
        onNodeClick={handleNodeClick}
        onNodeHover={(node) => setTooltip(node ? { id: node.id, label: node.label } : null)}
        linkColor={linkColor}
        linkWidth={linkWidth}
        linkOpacity={linkOpacity}
        linkCurvature={0.2}
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
      />
      )}

      {/* Node detail drawer */}
      <AnimatePresence>
        {selected && (
          <motion.div
            key="drawer"
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="absolute top-0 right-0 h-full w-72 glass-strong border-l border-white/10 p-5 overflow-y-auto"
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <span className={`badge-${selected.type === 'requirement' ? 'compliant' : 'partial'} mb-1 inline-block`}>
                  {selected.type?.replace(/_/g, ' ')}
                </span>
                <h3 className="font-bold text-white text-sm">{selected.id}</h3>
              </div>
              <button onClick={() => setSelected(null)} className="text-slate-500 hover:text-white">
                <X size={16} />
              </button>
            </div>
            {selected.title && <p className="text-xs text-slate-300 mb-3">{selected.title}</p>}
            <div className="text-[10px] text-slate-500 font-mono break-all">
              ID: {selected.id}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
