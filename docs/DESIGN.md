# Argus web — design system & page spec (source of truth for the frontend)

Principle: **the product is the proof.** Every number on the site comes from a real run (live from the API, or a
recorded artifact labelled with its date). Nothing is invented, nothing animates "fake activity". If data is missing,
say so plainly ("No live run yet — start one").

## Identity
- Name: **Argus** — "the tireless hand in the browser". Wordmark: "Argus" set in the display face, weight 650, with a
  small 8px square "status light" before it (ink when idle, accent when a run is live). No emoji anywhere. No stock
  illustrations, no gradients-as-decoration, no glassmorphism, no purple.
- Voice: short, specific, engineer-to-engineer. Numbers over adjectives. Active verbs. Honest about limits.

## Tokens (premium white; light is the only theme — commit to it, set every color explicitly)
```
--bg:        #FFFFFF    page
--surface:   #F7F8FA    panels, code blocks
--sunken:    #F1F3F6    table headers, input wells
--line:      #E6E8EC    hairlines (1px)
--line-strong:#D5D9E0
--ink:       #0B0D12    headings
--text:      #262A33    body
--muted:     #5C6370    secondary text
--faint:     #8A919E    captions, axis labels
--accent:    #1F4DFF    ONE accent: primary CTA, links, "live" state, focus rings
--accent-ink:#1638CC    accent hover
--accent-wash:#EEF2FF   accent backgrounds (selected rows)
Verdict semantics (never used as decoration):
--pass #0E8A5F  --heal #0A7C86  --intended #2F5BD3  --removed #6B7280  --bug #D63B3B  --review #B7791F  --infra #7A5AB8
each with a 8% wash for pills: e.g. --bug-wash #FCEDED
```
Shadows: only two — `--shadow-1: 0 1px 2px rgba(11,13,18,.06)` for raised cards, `--shadow-pop: 0 12px 32px -12px rgba(11,13,18,.18)` for popovers. Radius: 10px cards, 8px controls, 999px pills. No card-in-card.

## Type (Google Fonts)
- Display: **Schibsted Grotesk** 600–700, letter-spacing -0.02em on ≥32px.
- Body/UI: **Geist** 400/500.
- Data, terminal, code, numbers: **Geist Mono** 400/500, `font-variant-numeric: tabular-nums`.
Scale (px): 64 / 44 / 32 / 24 / 18 / 16 / 14 / 12. Body 16/1.6, max 68ch. Headings `text-wrap: balance`.
Uppercase eyebrow labels: Geist Mono 12px, +0.08em tracking, --muted.

## Layout
- Centered container 1120px max, 24px gutters (16px on phones), 8px spacing grid, sections separated by 120px (72 on phones)
  and a 1px --line rule, never by colored bands.
- A faint 24px dot grid (--line at 60% opacity) ONLY behind the hero product frame.
- Motion: 150–220ms ease-out on hover/focus; the only "moving" things are real events (new step rows sliding in, a live
  dot pulsing while a job runs). Respect prefers-reduced-motion.

## Page 1 — Landing `/`
1. **Nav** (sticky, white, hairline bottom): wordmark · How it works · Results · CLI & MCP · Limits · GitHub ·
   primary button "Watch it run live →" (/live).
2. **Hero** (not full-viewport): eyebrow `AUTONOMOUS UI TESTING`. H1 (64px): "Tests your app like a user.
   Heals when it changes. Knows a bug from a feature." Sub (18px, --muted, 60ch): "Argus compiles each journey once,
   replays it deterministically, and only calls a model when something is genuinely new — so the 10th run costs nothing."
   CTAs: primary "Watch it run live" · secondary text link "How it works". Below: **Live product frame** — a real,
   compact console card fed by `GET /api/overview` + `GET /api/runs/latest`: app + build, time ago, steps, replayed (T0),
   healed, LLM calls, verdict pills, and the first 6 real step rows with tier badges. Label: "Latest real run · <time>".
3. **Proof strip** (4 columns, big Geist Mono numbers, each linking to its evidence): Gauntlet heal success / false alarms
   / bug recall; 10th run LLM calls; SauceDemo real-world bugs caught on correct build vs buggy builds; blind exam score
   (show BEFORE→AFTER honestly). Caption under each: what it measured + date.
4. **How it works** — the resolution cascade as a precise horizontal diagram (inline SVG): T0 Replay → T1 Heal →
   T2 Reorder → T4a Complete → T3/T4 LLM → T5 Vision, each with cost ("$0" / "~300 tokens") and its REAL share of steps
   from `/api/overview.tiers` as a thin bar. Beside it, 3 short paragraphs: step contracts, immutable intent vs per-build
   plans, verified-only memory.
5. **Bug or feature?** — two real verdict cards side by side pulled from recorded runs: an INTENDED_CHANGE with its
   verbatim release-note citation, and a BUG with repro steps + evidence thumbnail. Then one line on the jury of models
   and `argus diff` for no-changelog cases.
6. **The 10th run** — real chart from `ten_runs.json` (bars: LLM calls = 0; line: % steps replayed at T0; release
   markers at runs 3 and 6). Caption with the naive-agent token estimate clearly labelled "estimate".
7. **CLI & MCP** — two side-by-side panes: a terminal (real recorded `argus run` output, monospace, dark-on-light
   surface, not a fake mac window) and the MCP tool list + a real `verify_change` JSON response. Link: "Run them live →".
8. **Limits** — "What Argus can't do yet": pulled from the blind exam failure list (honest, 3–5 bullets).
9. **Footer** — one line: built for FlytBase "The Tireless Hand" · GitHub · Live console.

## Page 2 — Live console `/live` (an app, not a page)
Full-height app shell, white.
- **Top bar**: wordmark · environment chip `Azure · <region>` · SkyOps build chip (live from `/api/skyops/state`,
  e.g. `SkyOps v1.1 · chaos off`) · link "Open SkyOps ↗" · LLM mesh chip (JEV / JEV2 / Groq, each with a status dot and
  remaining hourly budget from `/api/llm`).
- **Left rail (280px): Scenarios** — each a button with one-line description and expected cost:
  Replay suite (v1.0) · Deploy design refresh (v1.1) & run · Deploy Mission planner v2 (v1.2) & run ·
  Deploy hidden-bug release (v1.3) & run · Random chaos refactor & run · Differential diff v1.0 ↔ v1.3 ·
  Generate a suite from zero (explore + LLM) · MCP: verify_change · CLI: doctor / models / status.
  Disabled + tooltip while another visitor's job runs; show queue position.
- **Center: Run view** — header (scenario, job id, elapsed timer, state). Live **filmstrip** of real screenshots as they
  are written (click to enlarge). **Step list**: intent, tier badge (T0 Replay / T1 Heal / T2 Reorder / T4a Complete /
  T3–T4 LLM / T5 Vision), score, duration; expanding a healed step shows the top-5 candidate table with score breakdown.
  **Verdict cards** per test as they land (pill, rationale, verbatim citations, bug report).
- **Right rail (300px): Meters** — steps, replayed %, healed, LLM calls by provider, tokens, $ (actual) and list-price $,
  tokens avoided vs naive agent (labelled estimate), elapsed.
- **Bottom drawer tabs**: `Terminal` (the actual CLI stdout, streamed line by line), `MCP` (actual JSON-RPC frames:
  initialize → tools/list → tools/call → result), `Events` (raw SSE JSON).
Empty state: "Pick a scenario. Everything you see here is a real browser driven by Argus on this server."

## Quality bar
Hand-written HTML/CSS/vanilla JS (no framework needed; Chart drawing in SVG by hand or uPlot from cdnjs).
Accessible (focus rings in --accent, aria-live on the step list), keyboard operable, 360px-wide phones readable
(console collapses rails into tabs). Lighthouse performance ≥ 90 on the landing page. No lorem ipsum, no placeholder
numbers — wire to the API or show an honest empty state.
