/** Shared motion tokens. Mirrors --ease-* in index.css so CSS, Motion and GSAP move alike. */

export const ease = {
  expo: [0.16, 1, 0.3, 1] as const,
  carousel: [0.2, 0.9, 0.25, 1] as const,
}

export const gsapEase = {
  expo: "expo.out",
  carousel: "power3.out",
}

/** A section's parts enter one after another: heading, text, then the rest. */
export const stagger = {
  container: { hidden: {}, show: { transition: { staggerChildren: 0.07 } } },
  item: {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: ease.expo } },
  },
}
