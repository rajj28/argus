# Task: fix the weaknesses the blind exam exposed (general web patterns - do NOT special-case MediQueue)

Read `AGENTS.md`, `exam/results/scorecard.md` (failure list), `docs/SPEC.md`. Do NOT read `exam/answer_key.json`
and do not edit anything under `exam/` or `exam_app/`. Never open .env; no git; do not start/stop servers
(an exam run may be using port 8010 - do not touch it). Edit only the argus files named below + new tests.

1. Interrupt handling (biggest): popups/modals ("What's new", cookie banners, tours) block the flow. In
   `argus/runner/runner.py` add `_dismiss_interrupts(ctx, page, rec, snap) -> bool`: if the snapshot contains a visible
   blocking overlay (element with role=dialog / aria-modal=true / <dialog open>, or a fixed full-viewport layer) that is
   NOT the step's target, click its safest dismiss control (name matches got it|close|dismiss|skip|not now|no thanks|
   maybe later|continue|ok|×|✕, never destructive words) or press Escape. Call it before resolving each step and before
   T2/T4 when a target is not found. Record an `Observation(kind="locator_healed", tier=1, detail="dismissed interrupt ...")`
   is NOT right - add a new observation kind "interrupt_dismissed" in `argus/models.py` (default-compatible) and make
   triage treat it as cosmetic (ignore for verdicts). Remember dismissed overlays (by dialog heading) in knowledge.json so
   next runs dismiss instantly.
2. Identical siblings: in `argus/healing/resolver.py`, when the top candidates have near-identical scores (margin < gate)
   and identical role+name, break the tie by (a) best `context` text similarity, then (b) same ordinal index among
   identical siblings as the recorded target (store `ordinal` in Fingerprint.attrs at record time via snapshot.js:
   index among elements with same role+name in document order). Accept as tier 1 if the tie-break is decisive.
3. Shadow DOM crash: `argus/browser/snapshot.js` getCSSPath / getXPath / helpers must handle ShadowRoot and
   DocumentFragment parents (no tagName) - stop at the shadow root and prefix with the host's path. Add a fixture test.
4. sessionStorage auth: `ensure_auth` in runner.py must also capture `sessionStorage` (page.evaluate) into
   `.argus/auth/session.json`, and every new context must re-inject it via `context.add_init_script` for the app origin.
5. Arithmetic invariant oracle: new assertion kind `sum_equals` in `argus/models.py` + `argus/runner/assertions.py`:
   params {items_selector_text?: str, total_label: str} - parse all currency/number amounts in the table/section
   containing total_label, check total == sum(line items) within 0.005. Keep it generic (INR/USD/EUR symbols, commas).
Write unit tests (tests/test_exam_fixes.py) with local HTML fixtures (no servers). Run
`.venv/Scripts/python.exe -m pytest -q tests/ --ignore=tests/test_demo_app.py` and make everything pass. Report changes.
