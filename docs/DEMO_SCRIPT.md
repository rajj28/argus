# Argus: 5-minute demo script (FlytBase "Tireless Hand" judging)

**Setup before going on stage:** SkyOps is running (`python -m demo_app.server --port 8000`),
`argus doctor` is all green, and `argus dashboard` is open in a second tab. Terminal font size: large.

## 0:00 Hook (20 s)
"Every AI tester you've seen today calls a model on every click. That's slow, expensive, and it drifts
on the 10th run. Argus treats the LLM like a compiler: it's used once to understand your app, and after
that every run is plain deterministic replay. The model only comes back when something is genuinely new,
and whatever it learns goes into memory."

## 0:20 Act 1: day zero (40 s)  `argus demo --pause`
* SkyOps is a drone-ops console (yes, we built it for you). There are no tests yet.
* Argus authors a suite, including **negative tests from business rules** (altitude ≤ 120 m DGCA,
  battery ≥ 30%), and freezes each step's contract: API calls, navigation, screen.
* Replay: **32/32 steps at tier 0, 0 LLM calls.**

## 1:00 Act 2: design-system refresh (50 s) → **Reliability & self-healing**
* Every id renamed, classes hashed, test-ids removed, nav moved to a sidebar, "Next" is now "Continue".
* Result: all tests **COSMETIC_DRIFT, auto-healed, 0 LLM calls**. Show one heal's evidence in the dashboard:
  the candidate table with the score breakdown (the semantic view vs. the structural view).
* Run it again: pure replay. **Memory learned** that ids are unstable here (show `/memory` stability bars).

## 1:50 Act 3: mission planner v2 (70 s) → **Context & decision making**
* The wizard is reordered, there's a new required field "Pilot in command", and Flight logs is retired.
* Argus executes steps **out of order** (0 LLM), one small replan fills the new field, and the judge
  **cites the release note verbatim**. Verdict: INTENDED_CHANGE, test updated to v3 (show the diff).
* Flight logs: FEATURE_REMOVED, test **retired** (not a false alarm).
* "It will never accept a behaviour change it can't justify from your release notes."

## 3:00 Act 4: "performance improvements" (60 s) → **the money shot**
* The release notes say nothing else, but five regressions shipped.
* Argus: altitude 500 m accepted (**R1 violated**), low-battery drone assignable (**R2**), launched
  mission missing from the list (**R4**), settings API 500, logout crashes (uncaught TypeError).
* Every one comes with a bug report: repro steps, expected vs. actual, screenshots. **Decided by rules, 0 LLM calls.**

## 4:00 Numbers (40 s) → **Cost** + **Gauntlet**
* The scoreboard at the end of `argus demo`, plus `/gauntlet`: random refactor mutations (heal rate,
  false-alarm rate) and injected bugs (recall).
* Cost: "tokens avoided vs. an LLM-per-action agent: about 95–100%". The whole thing runs on **free tiers**, with
  JEV/OpenRouter as layer 1 and Gemini/Groq/Cerebras/Ollama as layer 2 (`argus models`).

## 4:40 Close (20 s) → **Software factory**
* `claude mcp add argus -- argus mcp`: your coding agent writes a feature, calls `verify_change`, and gets
  BUG / INTENDED_CHANGE back with evidence. It exports to standard Playwright, and its memory lives in git.
* "The tireless hand in the browser, and it knows when to call the brain."

## Likely judge questions
* **What if the changelog is missing?** Unexplained behaviour changes become NEEDS_REVIEW. A human
  answers once with `argus approve`, and the decision is remembered and never asked again.
* **Canvas / maps (FlytBase)?** The vision tier (VLM) uses the screenshot as a fallback. The architecture
  is tiered, so it only pays for vision where the DOM can't help.
* **Why not Playwright's healer agent?** It re-asks an LLM to rewrite code whenever something breaks.
  Argus heals ~all refactors deterministically at run time, tells bugs from features, and learns.
