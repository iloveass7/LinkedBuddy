import { useEffect } from "react"
import { ReactLenis, useLenis } from "lenis/react"
import { gsap } from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"
import { useReducedMotion } from "motion/react"

gsap.registerPlugin(ScrollTrigger)

/** The app's one Lenis instance, driven by GSAP's ticker so ScrollTrigger and Lenis share a frame. */
export function SmoothScroll() {
  const reduced = useReducedMotion()
  if (reduced) return null
  return (
    <ReactLenis root options={{ autoRaf: false, lerp: 0.12 }}>
      <TickerBridge />
    </ReactLenis>
  )
}

/*
 * Lenis takes over wheel and trackpad input, so it must get a frame every tick or scrolling
 * stalls. useLenis re-renders once the instance exists; a ref read in the parent's effect can
 * still be empty at that point.
 */
function TickerBridge() {
  const lenis = useLenis(ScrollTrigger.update)

  useEffect(() => {
    if (!lenis) return
    const update = (time: number) => lenis.raf(time * 1000)
    gsap.ticker.add(update)
    gsap.ticker.lagSmoothing(0)
    return () => gsap.ticker.remove(update)
  }, [lenis])

  return null
}
