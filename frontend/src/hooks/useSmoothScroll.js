// frontend/src/hooks/useSmoothScroll.js
// ---------------------------------------------------------------------------
// Lenis (inertia scrolling) driven by the GSAP ticker, with ScrollTrigger kept
// in sync.
//
// Why the ticker rather than Lenis's own rAF loop: running two independent
// animation loops makes ScrollTrigger read positions one frame behind Lenis,
// which shows up as pinned elements jittering. Driving lenis.raf from GSAP's
// ticker guarantees one loop and one frame of truth.
// ---------------------------------------------------------------------------

import { useEffect, useState } from 'react'
import Lenis from 'lenis'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

/** True when the OS asks for reduced motion. */
export function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/**
 * Mounts Lenis for the lifetime of the component.
 * No-ops entirely when the user prefers reduced motion — native scrolling then.
 *
 * @param {boolean} enabled
 * @returns {Lenis|null} the instance (for programmatic scrollTo)
 */
export function useSmoothScroll(enabled = true) {
  const [lenis, setLenis] = useState(null)

  useEffect(() => {
    if (!enabled) return undefined

    const instance = new Lenis({
      duration: 1.05,
      // Expo-out: fast pickup, long glide — the Apple/Linear scroll feel
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
      // Never hijack touch — it breaks native momentum and feels wrong on mobile
      syncTouch: false,
      touchMultiplier: 1.6,
    })

    instance.on('scroll', ScrollTrigger.update)

    const tick = (time) => instance.raf(time * 1000) // GSAP secs → Lenis ms
    gsap.ticker.add(tick)
    gsap.ticker.lagSmoothing(0)

    setLenis(instance)

    return () => {
      gsap.ticker.remove(tick)
      instance.destroy()
      setLenis(null)
    }
  }, [enabled])

  return lenis
}

export { gsap, ScrollTrigger }
