# Task: Argus premium landing page + live console (frontend)

Read `AGENTS.md`, `docs/DESIGN.md` (design system + page specs - follow it exactly, it is the brief),
`docs/WEB_SPEC.md` (API contract you consume), `README.md` (facts). Own ONLY `argus/web/static/**`.
Never open .env; no git; do not start/stop the SkyOps servers.

Build `argus/web/static/index.html` (landing), `argus/web/static/live.html` (console), shared
`assets/argus.css`, `assets/landing.js`, `assets/live.js`, `assets/logo.svg` (8px status-light square + wordmark).
Hand-written HTML/CSS/vanilla JS; Google Fonts (Schibsted Grotesk, Geist, Geist Mono); charts drawn as inline SVG by
your own code (or uPlot from cdnjs). All data comes from the API in WEB_SPEC; every number on screen must be bound to an
API field - no hard-coded metrics, no fake activity. When the API is unreachable or a field is missing, show an honest
empty state. For development without the backend, create `assets/mock/*.json` in the exact API shapes and load them
only when the URL has `?mock=1` (label the page "MOCK DATA" in that mode).

Craft bar (this is judged by people who build products): pixel-precise spacing on the 8px grid, consistent card edges,
tabular numbers, crisp 1px hairlines, focus-visible rings, reduced-motion support, responsive down to 360px (console
rails become tabs), no layout shift when data arrives (reserve space), fast (no framework, fonts with display=swap).
Copy: use the exact headline/sub from DESIGN.md; write any other copy short, specific and honest.
Verify visually: open both pages in headless Chromium at 1440x900 and 390x844 with `?mock=1`, save screenshots to
`argus/web/static/_review/` and review them yourself for alignment/overflow issues; fix what you see. Report briefly.
