# Argus blind exam scorecard - MediQueue (hold-out app)

Generated 2026-09-25T19:11:08+00:00 | suite `exam/argus_suite.json` | app `http://127.0.0.1:8010` | LLM budget 36/60 non-cached calls

Argus saw only `base_url`, the front-desk credentials and `exam/context` (PRODUCT.md + CHANGELOG.md). `exam/answer_key.json` was read once, by the scorer, after every run had finished.

## Overall accuracy

| Mode | LLM | Correct | Scored cases | Accuracy |
|---|---|---|---|---|
| Mode A - human-intent suite (LLM off) | off | 31 | 40 | 78% |
| Mode A - human-intent suite (LLM on) | on | 27 | 40 | 68% |
| Mode B - zero-knowledge (explore + generate, LLM on) | on | 0 | 0 | n/a |

### Per release

| Mode | Release | Correct | Intents scored | Accuracy |
|---|---|---|---|---|
| a_nollm | r2 | 6 | 8 | 75% |
| a_nollm | r3 | 7 | 8 | 88% |
| a_nollm | r4 | 5 | 8 | 62% |
| a_nollm | r5 | 5 | 8 | 62% |
| a_nollm | r6 | 8 | 8 | 100% |
| a_llm | r2 | 7 | 8 | 88% |
| a_llm | r3 | 4 | 8 | 50% |
| a_llm | r4 | 2 | 8 | 25% |
| a_llm | r5 | 6 | 8 | 75% |
| a_llm | r6 | 8 | 8 | 100% |

### LLM off vs LLM on (same suite, same releases, same resets)

| Release | LLM off | LLM on | Delta |
|---|---|---|---|
| r2 | 6/8 | 7/8 | +1 |
| r3 | 7/8 | 4/8 | -3 |
| r4 | 5/8 | 2/8 | -3 |
| r5 | 5/8 | 6/8 | +1 |
| r6 | 8/8 | 8/8 | +0 |
| **all scored** | 31/40 | 27/40 | -4 |

Fixed by the LLM: r2/book-consultation, r2/emergency-fee-zero, r5/emergency-fee-zero | newly wrong: r2/reschedule-appointment, r3/cancel-appointment, r3/doctor-list-loads, r3/invoice-total-correct, r4/doctor-list-loads, r4/invoice-total-correct, r4/reschedule-appointment


## Weakness roll-up

| Likely Argus weakness | Wrong verdicts | Where | Example observation |
|---|---|---|---|
| overlay / modal blocks navigation | 9 | r3/emergency-fee-zero r4/age-validation r4/book-consultation r4/emergency-fee-zero r5/age-validation r5/book-consultation r5/emergency-fee-zero | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') |
| step target not resolvable on the page | 7 | r2/reschedule-appointment r3/cancel-appointment r3/emergency-fee-zero r4/age-validation r4/emergency-fee-zero r4/reschedule-appointment r5/age-validat | cannot find 'Click Reschedule on the first appointment' (LLM found no confident match (Cannot determine which Reschedule button co |
| expected UI text missing (label or text drift) | 4 | r3/doctor-list-loads r3/invoice-total-correct r4/doctor-list-loads r4/invoice-total-correct | Status shows all eight doctors loaded: expected text "8 doctors available" not found on / |
| similarity margin gate (best match below threshold) | 2 | r2/book-consultation r2/emergency-fee-zero | cannot find 'Select the first available future appointment slot' (best candidate 0.98 below thresholds); page / headings ['Book an |

### Blocked before scoring

| Mode | Intent / stage | Why |
|---|---|---|
| a_nollm | reschedule-appointment | steps/oracles could not be resolved or baselined |
| a_llm | toggle-sms-reminders | Error: Page.evaluate: TypeError: Cannot read properties of undefined (reading 'toLowerCase')     at getCSSPath (eval at evaluate (:311:30), <anonymous>:278:32)     at eval (eval at evaluate (:311:30), |
| b_llm | zero-knowledge generation | explore() cannot authenticate: the session lives in sessionStorage, which storage_state does not carry |

## Criteria

### Mode A - human-intent suite (LLM off)

| Criterion | Score | Notes |
|---|---|---|
| Reliability (r2 + memory probe) | 6/8 (75%) | probe heals 6 |
| Context (r3-r6) | 25/32 (78%) | r3=88% r4=62% r5=62% r6=100% |
| Cost | 0 calls / 0 tokens | 8 runs |
| Memory (repeated r2 runs) | first 4 -> repeats 6 heals | run:4, 1:3, 2:3 |
| Generation | authored 8/9 | n/a (mode A) |

### Mode A - human-intent suite (LLM on)

| Criterion | Score | Notes |
|---|---|---|
| Reliability (r2 + memory probe) | 7/8 (88%) | probe heals 3 |
| Context (r3-r6) | 20/32 (62%) | r3=50% r4=25% r5=75% r6=100% |
| Cost | 31 calls / 42,010 tokens | 8 runs |
| Memory (repeated r2 runs) | first 6 -> repeats 3 heals | run:6, 1:1, 2:2 |
| Generation | authored 8/9 | n/a (mode A) |

### Mode B - zero-knowledge (explore + generate, LLM on)

| Criterion | Score | Notes |
|---|---|---|
| Reliability (r2 + memory probe) | 0/0 (n/a) | probe heals 0 |
| Context (r3-r6) | 0/0 (n/a) | r3=n/a r4=n/a r5=n/a r6=n/a |
| Cost | 0 calls / 0 tokens | 6 runs |
| Memory (repeated r2 runs) | first 0 -> repeats 0 heals |  |
| Generation | coverage 0% | baselined 0/0 |

### Not baselined in a_nollm

| Intent | Why |
|---|---|
| reschedule-appointment | steps/oracles could not be resolved or baselined |

### Not baselined in a_llm

| Intent | Why |
|---|---|
| toggle-sms-reminders | Error: Page.evaluate: TypeError: Cannot read properties of undefined (reading 'toLowerCase')     at getCSSPath (eval at evaluate (:311:30), <anonymous>:278:32)  |

### Zero-knowledge coverage (b_llm)

0/0 generated tests baselined, 0 dropped, intent coverage 0%. Explore reached 2 states / 1 actions.

**Generation is blocked at the login wall.** MediQueue keeps its session in `sessionStorage` (`mq-authenticated`), which Playwright's `storage_state` does not carry, so every context `explore()` opens is logged out: the crawl can only reach `/login`, the atlas has no authenticated page to write tests from, and the zero-knowledge suite is empty. This is the single most damaging weakness found - mode A's 8/9 authored suite proves the app is testable, mode B cannot discover any of it on its own.

Generator log:

```
[generate] proposal failed (LLMUnavailable); using deterministic tests
[generate] proposal attempt 1 had no 'tests' wrapper; retrying
[generate] proposal failed (LLMUnavailable); using deterministic tests
```


## Failure list

### Mode A - human-intent suite (LLM off)

| Release | Intent | Got | Acceptable | Key observation | Likely Argus weakness |
|---|---|---|---|---|---|
| r2 | book-consultation | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | cannot find 'Select the first available future appointment slot' (best candidate 0.98 below thresholds); page / headings ['Book an appointment', 'Choo | similarity margin gate (best match below threshold) |
| r2 | emergency-fee-zero | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | cannot find 'Select the first available slot' (best candidate 0.98 below thresholds); page / headings ['Book an appointment', 'Choose a time'] alerts  | similarity margin gate (best match below threshold) |
| r3 | emergency-fee-zero | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r4 | book-consultation | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r4 | age-validation | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r4 | emergency-fee-zero | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r5 | book-consultation | NEEDS_REVIEW | BUG | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r5 | age-validation | NEEDS_REVIEW | BUG | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |
| r5 | emergency-fee-zero | NEEDS_REVIEW | BUG | dismissed interrupt 'A smoother booking journey' (clicked 'Got it') | overlay / modal blocks navigation |

### Mode A - human-intent suite (LLM on)

| Release | Intent | Got | Acceptable | Key observation | Likely Argus weakness |
|---|---|---|---|---|---|
| r2 | reschedule-appointment | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | cannot find 'Click Reschedule on the first appointment' (LLM found no confident match (Cannot determine which Reschedule button corresponds to first a | step target not resolvable on the page |
| r3 | cancel-appointment | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | blocked at 'Click Cancel on the first appointment in the list': no plan; page / alerts [] | step target not resolvable on the page |
| r3 | doctor-list-loads | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | Status shows all eight doctors loaded: expected text "8 doctors available" not found on / | expected UI text missing (label or text drift) |
| r3 | invoice-total-correct | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | Line item 'Specialist consultation' is visible: expected text "Specialist consultation" not found on / | expected UI text missing (label or text drift) |
| r3 | emergency-fee-zero | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | blocked at 'Enter a valid patient name': no plan; page / alerts [] | step target not resolvable on the page |
| r4 | book-consultation | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | cannot find 'Enter the patient name' (no compatible candidates on page); page / headings ['Good morning, Front Desk', 'A smoother booking journey'] al | overlay / modal blocks navigation |
| r4 | doctor-list-loads | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | Status shows all eight doctors loaded: expected text "8 doctors available" not found on / | expected UI text missing (label or text drift) |
| r4 | invoice-total-correct | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | Line item 'Specialist consultation' is visible: expected text "Specialist consultation" not found on / | expected UI text missing (label or text drift) |
| r4 | reschedule-appointment | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | blocked at 'Click Reschedule on the first appointment': no plan; page / alerts [] | step target not resolvable on the page |
| r4 | age-validation | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | blocked at 'Enter a patient name': no plan; page / alerts [] | step target not resolvable on the page |
| r4 | emergency-fee-zero | NEEDS_REVIEW | PASS/COSMETIC_DRIFT | blocked at 'Enter a valid patient name': no plan; page / alerts [] | step target not resolvable on the page |
| r5 | book-consultation | NEEDS_REVIEW | BUG | cannot find 'Enter the patient name' (no compatible candidates on page); page / headings ['Good morning, Front Desk', 'A smoother booking journey'] al | overlay / modal blocks navigation |
| r5 | age-validation | NEEDS_REVIEW | BUG | blocked at 'Enter a patient name': no plan; page / alerts [] | step target not resolvable on the page |

## Run statistics

| Mode | Run | Build | Update | Tests | Heals | Replayed | LLM | Tokens | Wall | Verdicts |
|---|---|---|---|---|---|---|---|---|---|---|
| a_nollm | r1-baseline | r1 | yes | 8 | 2 | 77 | 0 | 0 | 100s | COSMETIC_DRIFT:2 NEEDS_REVIEW:1 PASS:5 |
| a_nollm | r2-run | r2 | yes | 8 | 4 | 70 | 0 | 0 | 97s | COSMETIC_DRIFT:2 NEEDS_REVIEW:2 PASS:4 |
| a_nollm | r3-run | r3 | yes | 8 | 1 | 52 | 0 | 0 | 89s | COSMETIC_DRIFT:1 NEEDS_REVIEW:3 PASS:4 |
| a_nollm | r4-run | r4 | yes | 8 | 0 | 49 | 0 | 0 | 84s | NEEDS_REVIEW:4 PASS:4 |
| a_nollm | r5-run | r5 | yes | 8 | 0 | 49 | 0 | 0 | 87s | BUG:1 NEEDS_REVIEW:4 PASS:3 |
| a_nollm | r6-run | r6 | yes | 8 | 1 | 46 | 0 | 0 | 86s | NEEDS_REVIEW:5 PASS:3 |
| a_nollm | r2-memory-probe-1 | r2 | yes | 8 | 3 | 71 | 0 | 0 | 97s | COSMETIC_DRIFT:1 NEEDS_REVIEW:2 PASS:5 |
| a_nollm | r2-memory-probe-2 | r2 | yes | 8 | 3 | 71 | 0 | 0 | 97s | COSMETIC_DRIFT:1 NEEDS_REVIEW:2 PASS:5 |
| a_llm | r1-baseline | r1 | yes | 8 | 1 | 79 | 5 | 8,614 | 136s | COSMETIC_DRIFT:1 NEEDS_REVIEW:1 PASS:6 |
| a_llm | r2-run | r2 | yes | 8 | 6 | 74 | 5 | 8,685 | 150s | COSMETIC_DRIFT:4 NEEDS_REVIEW:1 PASS:3 |
| a_llm | r3-run | r3 | yes | 8 | 0 | 47 | 5 | 5,693 | 269s | NEEDS_REVIEW:7 PASS:1 |
| a_llm | r4-run | r4 | yes | 8 | 0 | 47 | 5 | 6,068 | 403s | NEEDS_REVIEW:7 PASS:1 |
| a_llm | r5-run | r5 | yes | 8 | 0 | 47 | 2 | 2,912 | 1110s | BUG:2 NEEDS_REVIEW:5 PASS:1 |
| a_llm | r6-run | r6 | yes | 8 | 0 | 47 | 5 | 5,585 | 446s | NEEDS_REVIEW:7 PASS:1 |
| a_llm | r2-memory-probe-1 | r2 | yes | 8 | 1 | 79 | 3 | 4,157 | 179s | BUG:1 COSMETIC_DRIFT:1 PASS:6 |
| a_llm | r2-memory-probe-2 | r2 | yes | 8 | 2 | 83 | 1 | 296 | 155s | COSMETIC_DRIFT:2 PASS:6 |
| b_llm | r1-gen-baseline | r1 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
| b_llm | r2-gen-run | r2 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
| b_llm | r3-gen-run | r3 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
| b_llm | r4-gen-run | r4 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
| b_llm | r5-gen-run | r5 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
| b_llm | r6-gen-run | r6 | yes | 0 | 0 | 0 | 0 | 0 | 1s |  |
## Method notes and harness configuration

- `thresholds.margin` 0.04 (stock 0.12): the schedule renders three identical Cancel/Reschedule buttons, so a perfect 1.00 replay match with a 0.957 runner-up is refused by the stock margin gate and those intents cannot be baselined.
- `jury_enabled=false`: a 3-member jury costs 3 calls per verdict, which the 60-call budget cannot afford; the single smart-tier judge is used.
- `max_llm_calls_per_run` clamped per phase to stay within 60 calls; phases that exhaust it degrade to rule-based verdicts.
- App data is reset before every run, so each release starts from the same 60 appointments and verdicts are comparable across releases.
- Baselines are recorded after a data reset: the authoring pass itself books and cancels appointments, which would otherwise invalidate the fingerprints it just recorded.
- One spec crash is isolated: `toggle-sms-reminders` dies in `argus/browser/snapshot.js:getCSSPath` (`ShadowRoot` -> undefined `toLowerCase`), so it never reaches memory in any mode.

