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

## The resolution cascade

```
T0 replay        cached fingerprint still matches (score >= 0.90, same path)         $0
T1 heal          multi-attribute similarity, clear winner (score/margin, identity)   $0
T2 reorder       flow reordered: a later step's target is here -> execute it now      $0
T3 LLM heal      ambiguous: small model picks among the top-5 candidates only         ~300 tokens
T4 replan        target gone (new required field / new screen): bounded LLM replan    ~2k tokens
triage           rules first; LLM judge only for unexplained behaviour changes        ~2k tokens
```

Each step also carries a **falsifiable contract** captured automatically from the green run: the API calls it
makes (method, path, status class), where it navigates, and which screen appears. A mismatch becomes
evidence, with no model involved.

## Results on SkyOps (a drone-operations console, versions 1.0 → 1.3)

| Release | What changed | Argus verdicts | LLM calls |
|---|---|---|---|
| v1.0 | baseline | 8/8 PASS, 32/32 steps replayed | 0 |
| v1.1 | ids renamed, classes hashed, test-ids removed, nav → sidebar, labels reworded | 7× COSMETIC_DRIFT (auto-healed, tests → v2), 1 PASS | **0** |
| v1.2 | wizard reordered, new required field, Flight logs retired | INTENDED_CHANGE (tests updated with diff, release note cited), FEATURE_REMOVED (test retired) | a few |
| v1.3 | "performance improvements" + 5 silent regressions | BUG × 5 with repro steps + evidence | ~0 (rules) |

Run `argus demo` to reproduce, and `argus gauntlet` for the randomized robustness benchmark
(heal-success rate, false-alarm rate, bug recall).

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
