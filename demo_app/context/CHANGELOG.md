# SkyOps Changelog

## v1.2 — Mission planner v2

Significant improvements to mission planning workflow.

- **Wizard reorder**: Mission details → Flight parameters → Select drone → Review & launch.
  Rationale: collecting flight parameters before drone selection allows SkyOps to recommend
  drones that can fulfil the plan (range, payload).
- **New required field "Pilot in command"** on Mission details step (DGCA compliance requirement).
- **Flight logs page retired**: flight history now lives in the new Analytics page (`/analytics`).
  The `/logs` URL returns 404.
- **New Analytics page** (`/analytics`): totals cards (flights, distance, incidents, missions)
  plus a flight history table with drone filter.

---

## v1.1 — Aurora design system

Visual refresh (Aurora design system). Navigation moved to a sidebar; button labels polished. No functional changes.

- New sidebar navigation with reordered items: Missions · Dashboard · Flight logs · Settings
- Element IDs updated to Aurora naming convention (e.g. `login-email` → `auth-email-input`)
- CSS class names updated to CSS-module hashed style
- `data-testid` attributes removed
- Form fields wrapped in additional semantic containers
- "Site" field now appears before "Mission type" in wizard
- Label changes: "Log in" → "Sign in", "New mission" → "Create mission", "Next" → "Continue",
  "Launch mission" → "Launch", "Save settings" → "Save changes"
- Some buttons converted to `<a role="button">` for accessibility

---

## v1.0 — Initial release

SkyOps first public release.

- Fleet dashboard with live battery and status indicators
- 4-step mission planning wizard (Mission details → Select drone → Flight parameters → Review & launch)
- Flight logs with CSV export
- Account settings
- Authentication with session cookies

---

