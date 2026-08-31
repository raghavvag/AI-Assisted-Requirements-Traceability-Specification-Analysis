// frontend/src/components/landing/NeuralGraph.jsx
// ---------------------------------------------------------------------------
// The hero's 3D background: an abstract semantic network that morphs between
// two states as the page scrolls.
//
//   progress 0.0  →  ORGANIC CLOUD      "neural links" — nodes drift in 3D space
//   progress 1.0  →  TRACEABILITY MATRIX requirements column ↔ endpoint grid
//
// Two deliberate architecture decisions:
//
//  1. The morph and the mouse parallax are driven by *refs*, never React state.
//     GSAP/DOM write to the ref, useFrame reads it at 60fps. Scrolling or moving
//     the mouse therefore never re-renders a single React component.
//
//  2. Node colour comes from one InstancedMesh *per colour group* rather than a
//     per-instance `instanceColor` attribute. In three r165 the fragment shader
//     only declares `vColor` under USE_COLOR (i.e. material.vertexColors), while
//     USE_COLOR additionally multiplies by a geometry `color` attribute that a
//     SphereGeometry does not have — the combination silently renders black.
//     Three draw calls is a negligible cost and removes that whole failure mode.
// ---------------------------------------------------------------------------

import { useMemo, useRef, Suspense } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing'
import * as THREE from 'three'

// ── Graph shape ──────────────────────────────────────────────────────────────
const REQ_COUNT = 12 // requirement nodes
const EP_COUNT = 36 // endpoint nodes
const TOTAL = REQ_COUNT + EP_COUNT
const EP_COLS = 6
const EP_ROWS = 6

// Endpoints (index within the endpoint block) that violate a requirement in v2.
// Mirrors the REQ-04 / REQ-09 drifts in the real synthetic dataset.
const DRIFTED = new Set([7, 22])

/** Deterministic PRNG — the layout must be identical across mounts and HMR. */
function mulberry32(seed) {
  return function () {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Precompute both layouts, the edge list, drift phases and colour grouping. */
function useGraphData() {
  return useMemo(() => {
    const rand = mulberry32(20260831)

    const cloud = new Float32Array(TOTAL * 3)
    const matrix = new Float32Array(TOTAL * 3)
    const phase = new Float32Array(TOTAL)
    const size = new Float32Array(TOTAL)

    const reqIdx = []
    const epIdx = []
    const driftIdx = []

    for (let i = 0; i < TOTAL; i++) {
      const isReq = i < REQ_COUNT

      // ── Layout A: organic cloud (spherical shell, flattened in y and z) ────
      const theta = rand() * Math.PI * 2
      const phi = Math.acos(2 * rand() - 1)
      const r = 6.2 + rand() * 3.4
      cloud[i * 3 + 0] = r * Math.sin(phi) * Math.cos(theta)
      cloud[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.62
      cloud[i * 3 + 2] = r * Math.cos(phi) * 0.75

      // ── Layout B: traceability matrix ─────────────────────────────────────
      if (isReq) {
        matrix[i * 3 + 0] = -7.6
        matrix[i * 3 + 1] = (i / (REQ_COUNT - 1) - 0.5) * 9.4
        matrix[i * 3 + 2] = 0
      } else {
        const j = i - REQ_COUNT
        const col = j % EP_COLS
        const row = Math.floor(j / EP_COLS)
        matrix[i * 3 + 0] = -1.6 + col * 1.9
        matrix[i * 3 + 1] = (row / (EP_ROWS - 1) - 0.5) * 9.4
        matrix[i * 3 + 2] = 0
      }

      phase[i] = rand() * Math.PI * 2
      size[i] = isReq ? 0.2 : 0.13

      if (isReq) reqIdx.push(i)
      else if (DRIFTED.has(i - REQ_COUNT)) driftIdx.push(i)
      else epIdx.push(i)
    }

    // ── Edges: each requirement links to 2–3 endpoints ──────────────────────
    const edges = []
    for (let q = 0; q < REQ_COUNT; q++) {
      const links = 2 + Math.floor(rand() * 2)
      for (let k = 0; k < links; k++) {
        edges.push([q, REQ_COUNT + Math.floor(rand() * EP_COUNT)])
      }
    }

    return { cloud, matrix, phase, size, edges, reqIdx, epIdx, driftIdx }
  }, [])
}

// ── One InstancedMesh per colour ─────────────────────────────────────────────
function NodeCluster({ meshRef, count, color, emissiveBoost = 1 }) {
  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]} frustumCulled={false}>
      <sphereGeometry args={[1, 16, 16]} />
      {/* toneMapped={false} lets the colour exceed 1.0 so Bloom picks it up */}
      <meshBasicMaterial color={color} toneMapped={false} transparent opacity={emissiveBoost} />
    </instancedMesh>
  )
}

function Graph({ progressRef, mouseRef, reduced }) {
  const { cloud, matrix, phase, size, edges, reqIdx, epIdx, driftIdx } = useGraphData()

  const groupRef = useRef()
  const reqRef = useRef()
  const epRef = useRef()
  const driftRef = useRef()
  const lineRef = useRef()

  // Scratch — allocating inside useFrame would thrash GC at 60fps
  const dummy = useMemo(() => new THREE.Object3D(), [])
  const live = useMemo(() => new Float32Array(TOTAL * 3), [])
  const edgePos = useMemo(() => new Float32Array(edges.length * 6), [edges])

  const lineGeom = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(edgePos, 3))
    return g
  }, [edgePos])

  /** Write matrices for one colour group from the shared `live` positions. */
  const writeCluster = (ref, indices, t, pulseAmp) => {
    const mesh = ref.current
    if (!mesh) return
    for (let n = 0; n < indices.length; n++) {
      const i = indices[n]
      const pulse = 1 + Math.sin(t * 1.5 + phase[i]) * pulseAmp
      dummy.position.set(live[i * 3], live[i * 3 + 1], live[i * 3 + 2])
      dummy.scale.setScalar(size[i] * pulse)
      dummy.updateMatrix()
      mesh.setMatrixAt(n, dummy.matrix)
    }
    mesh.instanceMatrix.needsUpdate = true
  }

  useFrame((state) => {
    const t = state.clock.elapsedTime
    // smoothstep the raw scroll value so the morph eases in and out
    const raw = THREE.MathUtils.clamp(progressRef.current ?? 0, 0, 1)
    const p = raw * raw * (3 - 2 * raw)

    // ── Positions: lerp cloud → matrix, plus drift that fades as it resolves ─
    const driftAmp = reduced ? 0 : 1 - p
    for (let i = 0; i < TOTAL; i++) {
      const i3 = i * 3
      const ph = phase[i]
      live[i3 + 0] = THREE.MathUtils.lerp(cloud[i3], matrix[i3], p) + Math.sin(t * 0.42 + ph) * 0.34 * driftAmp
      live[i3 + 1] = THREE.MathUtils.lerp(cloud[i3 + 1], matrix[i3 + 1], p) + Math.cos(t * 0.36 + ph * 1.3) * 0.34 * driftAmp
      live[i3 + 2] = THREE.MathUtils.lerp(cloud[i3 + 2], matrix[i3 + 2], p) + Math.sin(t * 0.3 + ph * 0.7) * 0.28 * driftAmp
    }

    writeCluster(reqRef, reqIdx, t, 0.14)
    writeCluster(epRef, epIdx, t, 0.08)
    writeCluster(driftRef, driftIdx, t, 0.3) // drifted nodes throb harder

    // ── Edges follow the nodes ──────────────────────────────────────────────
    for (let e = 0; e < edges.length; e++) {
      const [a, b] = edges[e]
      const o = e * 6
      edgePos[o + 0] = live[a * 3]
      edgePos[o + 1] = live[a * 3 + 1]
      edgePos[o + 2] = live[a * 3 + 2]
      edgePos[o + 3] = live[b * 3]
      edgePos[o + 4] = live[b * 3 + 1]
      edgePos[o + 5] = live[b * 3 + 2]
    }
    lineGeom.attributes.position.needsUpdate = true
    // Links read as ambient noise in cloud mode, as structure in matrix mode
    if (lineRef.current) lineRef.current.material.opacity = 0.1 + p * 0.3

    // ── Mouse parallax + settle-to-flat as the matrix forms ─────────────────
    const g = groupRef.current
    if (!g) return
    const mx = mouseRef?.current?.x ?? 0
    const my = mouseRef?.current?.y ?? 0
    const idleSpin = reduced ? 0 : Math.sin(t * 0.14) * 0.22 * (1 - p)
    const targetY = mx * 0.34 * (1 - p * 0.72) + idleSpin
    const targetX = -my * 0.22 * (1 - p * 0.72)
    g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, targetY, 0.045)
    g.rotation.x = THREE.MathUtils.lerp(g.rotation.x, targetX, 0.045)
    // Pull back a touch so the full grid fits the frame
    g.position.z = THREE.MathUtils.lerp(g.position.z, THREE.MathUtils.lerp(0, -1.8, p), 0.05)
  })

  return (
    <group ref={groupRef}>
      <NodeCluster meshRef={reqRef} count={reqIdx.length} color="#22d3ee" />
      <NodeCluster meshRef={epRef} count={epIdx.length} color="#34d399" />
      <NodeCluster meshRef={driftRef} count={driftIdx.length} color="#fb7185" />

      <lineSegments ref={lineRef} geometry={lineGeom} frustumCulled={false}>
        <lineBasicMaterial
          color="#2ee6d6"
          transparent
          opacity={0.16}
          toneMapped={false}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </lineSegments>
    </group>
  )
}

// ── Far-field starfield: gives the black actual depth ────────────────────────
function Starfield({ reduced }) {
  const ref = useRef()
  const positions = useMemo(() => {
    const rand = mulberry32(7)
    const n = 420
    const arr = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      arr[i * 3 + 0] = (rand() - 0.5) * 62
      arr[i * 3 + 1] = (rand() - 0.5) * 42
      arr[i * 3 + 2] = -14 - rand() * 30
    }
    return arr
  }, [])

  useFrame((state) => {
    if (reduced || !ref.current) return
    ref.current.rotation.z = state.clock.elapsedTime * 0.006
  })

  return (
    <points ref={ref} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.035}
        color="#7dd3fc"
        transparent
        opacity={0.45}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  )
}

/**
 * Full-bleed WebGL background.
 *
 * @param {object} progressRef  ref, 0→1. Written by GSAP ScrollTrigger, read in useFrame.
 * @param {object} mouseRef     ref, {x,y} in -1..1. Written by a window listener in the
 *                              parent — the canvas itself is pointer-events:none so
 *                              R3F's own `pointer` would never update.
 * @param {boolean} reduced     prefers-reduced-motion — disables drift, spin and bloom.
 */
export default function NeuralGraph({ progressRef, mouseRef, reduced = false }) {
  return (
    <Canvas
      dpr={[1, 1.75]}
      gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      camera={{ position: [0, 0, 17], fov: 46 }}
      style={{ pointerEvents: 'none' }}
    >
      <Suspense fallback={null}>
        <Starfield reduced={reduced} />
        <Graph progressRef={progressRef} mouseRef={mouseRef} reduced={reduced} />
        {!reduced && (
          <EffectComposer disableNormalPass multisampling={0}>
            <Bloom mipmapBlur intensity={1.1} luminanceThreshold={0.2} luminanceSmoothing={0.3} radius={0.7} />
            <Vignette eskil={false} offset={0.25} darkness={0.85} />
          </EffectComposer>
        )}
      </Suspense>
    </Canvas>
  )
}
