import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/*
 * tailwind-merge must know the design-system tokens in index.css. Without this it reads a custom
 * size such as `text-label` as a colour and drops the real colour class next to it.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["hero", "hero-sm", "headline-xl", "headline-xl-sm", "headline-lg", "headline-md", "title", "body-lg", "body", "label", "badge", "code"],
      shadow: ["hard", "hard-sm", "hard-cobalt", "hard-lift", "glass", "glass-sm"],
      radius: ["glass"],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
