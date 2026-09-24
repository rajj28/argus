# Argus — the tireless hand in the browser

**Autonomous end-to-end UI testing that heals itself, remembers your app, knows a bug from a feature,
and costs almost nothing to run.**

> The LLM compiles. The runtime replays. Models are called only on *novelty*.

Most "AI testers" put a model in front of every click, so they're slow and expensive, and they drift on
the 10th run. Argus uses the model the way a compiler is used: once, to understand the app and write the
test. After that, every run is deterministic Playwright replay. The model comes back only when something
is genuinely new, and whatever it learns is written to memory, so the same situation costs **$0** next time.

---

## What it does (mapped to the judging criteria)

| Criterion | How Argus does it |
|---|---|
| **Reliability & self-healing** | Every target is a Similo-style *multi-attribute fingerprint* (role, accessible name, label, context row/card, neighbours, test-id, id, classes, xpath, position…). A **dual-view score** (what a script sees vs. what a human sees) re-identifies elements after refactors. Healing runs as a tiered cascade (below). Reordered flows are handled by **out-of-order execution** (0 LLM calls). |
| **Context & decision making** | A **triage engine** grounded in the product's business rules and release notes. It uses the NL-intent-as-oracle idea from *Testora* (ICSE 2026), adapted to UI testing. Deterministic rules come first; an LLM judge handles ambiguous cases, and its changelog citations are **verified verbatim** (anti-hallucination). The risk policy is asymmetric: without a citation, a change is never silently accepted. |
| **Cost & efficiency** | Steady-state runs make **0 LLM calls**. Each call is tiered (cheap → strong), cached on disk, budgeted per run and logged in a cost ledger. A **free-tier LLM mesh** puts JEV/OpenRouter first, then Gemini / Groq / Cerebras / local Ollama. Every report shows *tokens avoided vs. an LLM-per-action agent*. |
| **State memory & self-improvement** | `.argus/` is git-friendly JSON: versioned tests, app atlas, learned **attribute-stability weights** (e.g. "ids are unstable in this app" lowers their weight automatically), remembered human decisions, and an LLM cache. **Verified-only memory:** nothing is learned from a failed run. |
| **Test generation & maintenance** | `explore` crawls the app deterministically into an atlas. `generate` turns atlas + product context into journeys **and business-rule negative tests** in 1–3 LLM calls, then compiles and baselines them. Tests are auto-healed, auto-updated on intended changes (with a diff) and auto-retired when a feature is removed. They export as standard Playwright `.spec.ts` files. |

## Architecture

**Immutable intent vs per-build compiled plans.** A test's *intent* — name, goal, business oracles,
tags — is frozen the first time the test is saved (`.argus/tests/_intent/`) and can never be rewritten
by healing or adaptation. What the runtime mutates is the *compiled plan* (locators, step order,
expected effects), versioned per build under `.argus/tests/_plans/<build>/`. Evidence from a new build
is therefore always compared against a trusted baseline instead of a plan the previous release already
rewrote.

**The resolution cascade.** Each tier runs in order and stops as soon as it succeeds; only T3–T5 spend
tokens:

| Tier | What it does | Cost |
|---|---|---|
| T0 replay | the cached fingerprint still matches (score ≥ 0.90, same path) | $0 |
| T1 similarity | multi-attribute Similo-style score, clear winner (score/margin, identity) | $0 |
| T2 out-of-order | the flow was reordered: a later step's target is here → execute it now — never **across a commit point** (a step that wrote data on the baseline) | $0 |
| T4a required-field completion | a new required field blocks the flow → fill it with deterministic heuristics, press the progress button | $0 |
| T3 LLM heal | ambiguous: a small model picks among the top-5 candidates only | ~300 tokens |
| T4 replan | target gone (new required field / new screen): bounded LLM replan | ~2k tokens |
| T5 vision | VisualMark on canvas/map: **cached coords → template match → multiscale match → the VLM picks a numbered mark id** | VLM only |

Each step also carries a **falsifiable contract** captured automatically from the green run: the API
calls it makes (method, path, status class), where it navigates, and which screen appears. A mismatch
becomes evidence, with no model involved.

**Triage is rules-first.** Deterministic business rules and step contracts decide verdicts on their
own; the LLM judge only sees what's left, and every changelog citation it produces is **verified
verbatim** against the release notes before it is trusted. Without a citation a change is never
silently accepted. When there is **no changelog at all**, `argus diff` runs the same immutable intents
against two live instances and compares canonical behaviour traces — regressions vs refactor-only
drift. A test that cannot establish its starting state (session, data) is `PRECONDITION_FAILURE`, not
a bug.

**Free libraries:** `opencv-headless` and `imagehash` power the vision tier (template + multiscale
matching with a perceptual-hash cache); Playwright **tracing** keeps a debuggable trace of every
deviating run.

## Verified results on SkyOps (a drone-operations console, versions 1.0 → 1.3)

### 1. Offline Gauntlet (`argus gauntlet`, 0 LLM calls) — source: `.argus/gauntlet.json`

| Trials | Heal success | False alarms | Bug recall | Steps self-healed |
|---|---|---|---|---|
| 3 random refactor trials + 5 injected-bug trials | **100%** | **0%** | **100% (5/5)** | **106** |

### 2. Four-act demo, offline (`argus demo`, 0 LLM calls)

| Release | What changed | Argus verdicts | LLM calls |
|---|---|---|---|
| v1.0 | baseline | **8 PASS** | 0 |
| v1.1 | ids renamed, classes hashed, test-ids removed, nav → sidebar, labels reworded | **7 COSMETIC_DRIFT + 1 PASS** | 0 |
| v1.2 | wizard reordered, new required field, Flight logs retired | **3 INTENDED_CHANGE** (release-note quotes verified) **+ 1 FEATURE_REMOVED + 4 PASS** | 0 |
| v1.3 | "performance improvements" + 5 silent regressions | **5 BUG + 2 PASS (+1 retired)** | 0 |

### 3. `argus diff` — v1.0 vs v1.3 on two live instances, no changelog, no LLM

| What was compared | Evidence captured | Argus verdicts |
|---|---|---|
| Same immutable intents, two builds | lost side effects, a crash, a 500, business-rule violations | all **5 hidden bugs** flagged **REGRESSION** |
| Refactor-only flows | identical outcome and side effects; only how elements were found changed | **UI_DRIFT** |

`argus demo` and `argus gauntlet` reproduce those numbers; `benchmarks/ten_runs.py` replays the suite
10 times across 1.0 → 1.2 with the LLM off (steady-state cost curve, 0 LLM calls). The dashboard shows
the 10-run curve at `/tenruns` and any `argus diff` output at `/diff`.

## Quick start

```bash
pip install -e .            # installs the `argus` command
python -m playwright install chromium
argus doctor                # browser, app, LLM mesh, memory

# the showcase
python -m demo_app.server --port 8000        # SkyOps
argus init --url http://localhost:8000 --context demo_app/context --user pilot@skyops.io --password flysafe123
argus demo                  # four acts: baseline -> refactor -> redesign -> buggy release
argus dashboard             # Mission Control: evidence, diffs, learned memory, cost curve

# your own app
argus init --url https://your.app --context ./product-docs --user qa@you.com --password ...
argus explore && argus generate
argus run --label "PR #42"
argus export                # standard Playwright tests
```

### LLM mesh (all free tiers)

Put the keys in `.env`. They're read from the environment and never written anywhere else.

| Layer | Provider | Env var | Free key |
|---|---|---|---|
| 1 | JEV / OpenRouter | `JEV_API` or `OPENROUTER_API_KEY` | openrouter.ai/keys |
| 2 | Google Gemini | `GEMINI_API_KEY` | aistudio.google.com/apikey |
| 2 | Groq | `GROQ_API_KEY` | console.groq.com/keys |
| 2 | Cerebras | `CEREBRAS_API_KEY` | cloud.cerebras.ai |
| offline | Ollama (local) | — (auto-detected) | ollama.com |

`argus models` shows the live chains. Argus runs fine with **no** LLM at all: the deterministic core
covers replay, healing, reordering and rule-based bug detection.

### For coding agents (MCP)

```bash
claude mcp add argus -- argus mcp     # or any MCP client (opencode, Kiro, Cursor…)
```
Tools: `run_tests`, `verify_change(description)`, `explore_and_generate`, `last_report`.
The coding agent passes its intent, like a PR description. Argus tests the app in a real browser and
answers BUG / INTENDED_CHANGE with evidence. That closes the software-factory loop.

## Research it builds on
* Similo / VON Similo / VON Similo LLM: multi-attribute web element localization (Nass, Alégroth, Feldt; TOSEM 2023, STVR 2024)
* Hammoudi et al., *Why do record/replay tests of web applications break?* (ICST 2016): 73.6% of breakages are locators
* Pradel, *Testora: using natural-language intent to detect behavioral regressions* (ICSE 2026)
* FCPAgent: falsifiable commitment planning for web agents (2026), the basis for Argus's step contracts
* Budget-constrained study of skill/memory modules for web agents (2026): why Argus keeps memory verified-only
* AutoE2E: feature-driven E2E test generation (ICSE 2025)
