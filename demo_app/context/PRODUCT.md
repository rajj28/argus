# SkyOps — Product Overview

## What is SkyOps?

SkyOps is a drone fleet operations console for enterprise customers. It allows operations teams to
manage drone fleets, plan and execute missions, monitor flight logs, and configure system settings.

## Personas

**Pilot** — operates drones in the field. Creates missions, monitors battery/status, reviews flight logs.

**Ops Manager** — oversees the full fleet. Reviews analytics, manages settings, ensures compliance.

## Key Journeys

1. **Login** → Dashboard → view fleet status
2. **Plan mission** → 4-step wizard → launch → monitor on mission detail page
3. **Review logs** → filter by drone → export CSV
4. **Configure settings** → update display name, units, email preferences

## Business Rules

| ID | Rule |
|----|------|
| R1 | Maximum altitude is **120 m AGL** (DGCA regulation). Missions with altitude > 120 m must be rejected. |
| R2 | Drones with battery **< 30%** cannot be assigned to a new mission. |
| R3 | Mission **names must be unique**. Creating a mission with a duplicate name is rejected. |
| R4 | A successfully launched mission **appears in the Missions list** with status "Scheduled". |
| R5 | **Authentication required** for all pages except /login. Unauthenticated requests redirect to /login. |
| R6 | **Abort** requires a confirmation dialog and sets mission status to "Aborted". |
| R7 | **Settings persist** across page reloads (stored server-side in session). |

## Sites

- Pune Depot
- Mumbai Port
- Bengaluru Solar Farm

## Mission Types

Survey · Inspection · Delivery · Patrol
