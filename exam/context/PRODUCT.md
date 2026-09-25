# MediQueue product rules

- **B1 — Patient age:** A patient age must be from 0 through 120 inclusive.
- **B2 — Emergency fee:** An emergency visit has a doctor fee of INR 0 and a total of INR 0.
- **B3 — Invoice total:** The invoice TOTAL is exactly the sum of all displayed line-item amounts, to the paisa.
- **B4 — Confirmed booking:** A confirmed appointment is persisted and appears in the Appointments list.
- **B5 — Future booking:** A new appointment cannot be booked at a time that is already in the past.
- **B6 — Cancellation:** Cancelling requires explicit confirmation and removes the appointment row.
