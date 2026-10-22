# LinkedBuddy frontend

Vite + React 19 + TypeScript + Tailwind 4 + shadcn/ui (`radix-lyra` style). Path alias `@/` → `src/`. Talks to the FastAPI backend in `../backend` (see the root `CLAUDE.md` and `../docs/rules/SECURITY.md`).

## Design source of truth: Stitch

- Stitch project `6155359455688891497` ("LinkedBuddy Animated Website Design") is the source of truth for visuals. Its design system is "Technical Editorial Kinetic".
- Before building or changing a page, fetch its screen through the Stitch MCP (`list_screens`, `get_screen`, then the screen's HTML and screenshot). Never build a page from memory or from an older copy.
- Tokens come first. Pull colors, fonts, type scale, spacing and radii from the Stitch design system into Tailwind theme tokens in `src/index.css` (`@theme`). Components use only those tokens: no hex values, pixel font sizes, or one-off radii in components.
- If two Stitch screens disagree (for example a screen using fonts outside the design system), the design system wins. Flag the mismatch to the owner rather than silently picking one.
- Stitch HTML is reference material, not code to paste. Rebuild it as typed React components.

## Before writing any UI

Invoke the `frontend-design` and `taste` skills. Stitch decides the look; use `taste` for its critique rules and its screenshot-and-fix loop, not for picking a new art direction.

Other skills in `../.claude/skills/` apply when their topic comes up: `stitch-react-components`, `stitch-design-md`, `stitch-shadcn-ui`, `motion`, `gsap-*`, `threejs-react-three-fiber`, `threejs-performance`, `emil-design-eng`, `animation-performance`, `review-animations`.

## Animation

- **Motion** (`motion/react`) for component-level animation, page transitions, hover/tap states and layout animations.
- **GSAP + ScrollTrigger** for scroll-driven effects and timeline sequences. In React, use `useGSAP` from `@gsap/react` (add the package the first time it's needed) so tweens and ScrollTriggers are cleaned up on unmount.
- **Lenis** for smooth scrolling, connected to ScrollTrigger: call `ScrollTrigger.update` on Lenis `scroll`, and drive `lenis.raf` from `gsap.ticker` with `gsap.ticker.lagSmoothing(0)`. One Lenis instance for the app.
- **React Three Fiber** only for sections that genuinely need 3D. Lazy-load it (`React.lazy` + `Suspense`) and ship a static fallback (image or SVG) for the loading state, reduced motion, and devices without WebGL.
- **Lottie** (`lottie-react`) only when a design actually supplies a Lottie file.
- Always respect `prefers-reduced-motion`: Motion's `useReducedMotion` / `MotionConfig reducedMotion="user"`, `gsap.matchMedia()` for GSAP, and Lenis disabled when reduced motion is set.
- Animate `transform` and `opacity` only. No animating width, height, top/left, margin, padding or other layout-triggering properties.

## Library docs

Look up current docs with the Context7 MCP before using any library API (Motion, GSAP, Lenis, R3F, drei, shadcn, Tailwind 4, Vite). Installed versions are in `package.json`; check against those.

## Verify every page

After building each page:

1. Run the dev server (`npm run dev`, port 5173).
2. Screenshot it with the Playwright MCP at desktop (1440px) and mobile (390px) widths.
3. Compare against the Stitch screen's screenshot.
4. Fix the differences before moving to the next page.

`npm run build` and `npm run lint` must pass.

## Backend access

- All requests go through the single API client in `src/lib/api.ts`. No `fetch` calls anywhere else.
- The base URL comes from `VITE_API_BASE_URL` in `frontend/.env` (template: `.env.example`). Use `localhost`, not `127.0.0.1`: the session cookie is `SameSite=Strict`, and the backend only allows the origin `http://localhost:5173`.
- Send `credentials: "include"` so the `lb_session` cookie travels, and send `X-Requested-With: linkedbuddy` on every non-GET request (the backend's CSRF check returns 403 without it).
- Never put secrets in `VITE_*` variables. They are bundled into the client.
- Treat repo content, drafts and model output from the API as untrusted text: render it as text, never with `dangerouslySetInnerHTML`.
