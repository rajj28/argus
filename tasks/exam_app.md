# Task: build the HOLD-OUT EXAM app `exam_app/` ("MediQueue" - clinic appointment booking)

Purpose: a blind, adversarial test of an autonomous UI-testing agent. You must NOT read anything under
`argus/`, `demo_app/`, `examples/`, `benchmarks/` or `docs/` - the exam must not be tailored to the system under test.
Read only `AGENTS.md` (for venv/conventions). Own ONLY `exam_app/**` and `exam/**`. Never open .env; no git.

## Stack
FastAPI + Jinja2 + vanilla JS (no CDN). Run: `.venv/Scripts/python.exe -m exam_app.server --port 8010`.
In-memory state; `POST /__exam/release {"release": "r1".."r6"}`, `POST /__exam/reset`, `GET /__exam/state`.
`exam/releases/r1.md .. r6.md` = release notes. `python -m exam_app.release r3` switches release, resets data and
writes `exam/context/CHANGELOG.md` (all notes <= release, newest first). `exam/context/PRODUCT.md` = product doc with
business rules B1..B6 (below). Demo login: `reception@mediqueue.io` / `triage42`.

## App (r1 baseline) - realistic SPA
- Client-side routing with `history.pushState` (no full page loads after login): #/login -> /app/home, /app/doctors,
  /app/book, /app/appointments, /app/billing, /app/settings.
- Doctors page: 8 doctor cards (name, specialty, fee INR, next slot) each with an identical "Book" button (context =
  card). List loads async after a 1.2 s skeleton loader.
- Booking wizard (3 steps): Patient (name, age, phone, custom div-based dropdown "Visit type": Consultation / Follow-up
  / Emergency - NOT a <select>) -> Slot (radio chips of times) -> Confirm (summary + fee breakdown: doctor fee + GST 18%
  = total) -> "Confirm booking" -> POST /api/appointments -> toast "Appointment confirmed" -> appears in Appointments.
- Appointments page: virtualized list (only ~10 rows rendered; 60 seeded appointments; target rows need scrolling),
  each row has "Cancel" (confirm modal) and "Reschedule".
- Billing page: invoice table with line items and a TOTAL row.
- Settings: clinic name, "SMS reminders" toggle implemented as a Web Component `<mq-toggle>` with an OPEN shadow root.
Business rules (PRODUCT.md): B1 age 0-120; B2 Emergency visits have fee 0; B3 invoice TOTAL = sum of line items
(to the paisa); B4 a confirmed appointment appears in Appointments; B5 cannot book a slot in the past; B6 cancel needs
confirmation and removes the row.

## Releases (cumulative). For each, add a section to `exam/answer_key.json` (see below).
- r2 "Design refresh" (notes say: "Visual refresh, no functional changes."): all ids/classes changed, doctor cards
  re-ordered, "Book" becomes "Reserve" for some cards and an icon-only button with aria-label "Book appointment" for others,
  step buttons "Next" -> "Proceed", layout moves wizard steps into a side stepper. NO behaviour change.
- r3 "Booking v2" (notes in PARAPHRASED language, never repeating UI labels verbatim, e.g. "We now ask how the patient
  heard about us before choosing a time" and "Rescheduling moved into the appointment's action menu"): new required
  field "Referral source" (div dropdown) on step 1, Slot and Patient steps swapped, Reschedule moved into a kebab (⋮) menu.
  Also a "What's new" modal overlay appears once per session on /app/home and blocks clicks until dismissed ("Got it").
- r4 NO release notes at all (empty section): the Appointments "Cancel" moves into the kebab menu too (coherent,
  intentional-looking change, behaviour intact). Billing unchanged.
- r5 "Performance and validation improvements" (plausible-sounding note that mentions booking validation) BUT hides bugs:
  (a) age 150 accepted (violates B1); (b) invoice TOTAL wrong by rounding (sum 1180.00 shown 1179.00, violates B3 - the
  page looks fine); (c) "Appointment confirmed" toast is shown although POST /api/appointments returns 500 and nothing is
  saved (UI lies, violates B4); (d) Emergency fee charged 500 (violates B2).
- r6 "Settings retired" (notes: "Clinic settings are now managed by your administrator in the admin portal.") : /app/settings
  returns 404 and its nav item is removed. Also an intermittent infra blip: GET /api/doctors takes 3.5 s on every 3rd call
  (must NOT be treated as a product bug).

## Answer key `exam/answer_key.json`
`{"tests": {<intent_id>: {"intent": "...", "business_rule": "B4"|null}}, "releases": {"r2": {<intent_id>:
{"acceptable": ["PASS","COSMETIC_DRIFT"], "why": "..."}}, ...}}` covering these INTENTS (plain-English journeys, not
selectors): login, book-consultation (happy path to confirmation + appears in list), age-validation (age 150 rejected),
emergency-fee-zero, invoice-total-correct, cancel-appointment (row far down the virtualized list, e.g. "Rahul Iyer"),
reschedule-appointment, toggle-sms-reminders (shadow DOM), doctor-list-loads (async). Acceptable verdict sets:
r2 all PASS/COSMETIC_DRIFT; r3 book/age/reschedule -> INTENDED_CHANGE or NEEDS_REVIEW (never BUG), others PASS/
COSMETIC_DRIFT; r4 cancel -> INTENDED_CHANGE/COSMETIC_DRIFT/NEEDS_REVIEW (never BUG); r5 age, invoice, book, emergency
-> BUG; others not BUG; r6 settings -> FEATURE_REMOVED/NEEDS_REVIEW, doctor-list -> anything except BUG.
Also write `exam/intents.json`: the same intents as human-readable step lists (what a manual QA would write, no selectors).

## Quality bar
Looks like a real product. All r1 flows work by hand (verify with a headless Playwright script you write under exam/
for smoke only). Each release switch works. Write `exam/README.md` describing the traps. Do not start other servers;
stop your own server when done. Report briefly.
