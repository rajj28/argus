from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Final

RELEASES: Final[tuple[str, ...]] = ("r1", "r2", "r3", "r4", "r5", "r6")

DOCTORS: Final[tuple[dict[str, Any], ...]] = (
    {"id": "doc-aarav", "name": "Dr. Aarav Mehta", "specialty": "General Medicine", "fee": 500, "next_slot": "Today"},
    {"id": "doc-diya", "name": "Dr. Diya Shah", "specialty": "Dermatology", "fee": 650, "next_slot": "Today"},
    {"id": "doc-kabir", "name": "Dr. Kabir Rao", "specialty": "Cardiology", "fee": 900, "next_slot": "Tomorrow"},
    {"id": "doc-meera", "name": "Dr. Meera Iyer", "specialty": "Paediatrics", "fee": 450, "next_slot": "Today"},
    {"id": "doc-neel", "name": "Dr. Neel Joshi", "specialty": "Orthopaedics", "fee": 700, "next_slot": "Fri"},
    {"id": "doc-priya", "name": "Dr. Priya Nair", "specialty": "Gynaecology", "fee": 600, "next_slot": "Tomorrow"},
    {"id": "doc-rohan", "name": "Dr. Rohan Sen", "specialty": "ENT", "fee": 550, "next_slot": "Sat"},
    {"id": "doc-zoya", "name": "Dr. Zoya Khan", "specialty": "Nutrition", "fee": 500, "next_slot": "Mon"},
)

_FIRST_NAMES: Final[tuple[str, ...]] = (
    "Aarav", "Diya", "Kabir", "Meera", "Neel", "Priya", "Rohan", "Zoya",
    "Vivaan", "Anaya", "Arjun", "Ira", "Aditya", "Sara", "Ishaan", "Kiara",
    "Vihaan", "Tara", "Ayaan", "Nisha",
)
_LAST_NAMES: Final[tuple[str, ...]] = (
    "Sharma", "Verma", "Nair", "Patel", "Singh", "Khan", "Bose", "Gupta",
    "Reddy", "Das", "Kapoor", "Malhotra", "Chopra", "Menon", "Jain", "Rao",
    "Sethi", "Dutta", "Gill", "Pillai",
)


class ReleaseError(ValueError):
    """Raised when an unknown release is requested."""


class ExamStore:
    """Thread-safe in-memory state for one exam run."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._release = "r1"
        self._reset_locked()

    @property
    def release(self) -> str:
        with self._lock:
            return self._release

    def switch_release(self, release: str) -> None:
        if release not in RELEASES:
            raise ReleaseError(f"Unknown release: {release}")
        with self._lock:
            self._release = release
            self._reset_locked()

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def next_doctor_call(self) -> int:
        with self._lock:
            self._doctor_calls += 1
            return self._doctor_calls

    def doctors(self) -> list[dict[str, Any]]:
        with self._lock:
            release = self._release
        doctors = list(DOCTORS)
        if release != "r1":
            doctors = [doctors[index] for index in (6, 1, 7, 0, 4, 2, 5, 3)]
        return deepcopy(doctors)

    def appointments(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._appointments)

    def appointment(self, appointment_id: str) -> dict[str, Any] | None:
        with self._lock:
            for appointment in self._appointments:
                if appointment["id"] == appointment_id:
                    return deepcopy(appointment)
        return None

    def add_appointment(self, appointment: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            stored = deepcopy(appointment)
            stored["id"] = f"apt-{self._next_appointment:03d}"
            self._next_appointment += 1
            self._appointments.append(stored)
            return deepcopy(stored)

    def cancel_appointment(self, appointment_id: str) -> bool:
        with self._lock:
            original_count = len(self._appointments)
            self._appointments = [
                appointment
                for appointment in self._appointments
                if appointment["id"] != appointment_id
            ]
            return len(self._appointments) < original_count

    def reschedule_appointment(self, appointment_id: str, slot: str) -> bool:
        with self._lock:
            for appointment in self._appointments:
                if appointment["id"] == appointment_id:
                    appointment["slot"] = slot
                    return True
        return False

    def settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if isinstance(changes.get("clinic_name"), str):
                clinic_name = changes["clinic_name"].strip()
                if clinic_name:
                    self._settings["clinic_name"] = clinic_name
            if isinstance(changes.get("sms_reminders"), bool):
                self._settings["sms_reminders"] = changes["sms_reminders"]
            return deepcopy(self._settings)

    def billing(self) -> dict[str, Any]:
        with self._lock:
            release = self._release
        line_items = [
            {"description": "Specialist consultation", "amount": 620.00},
            {"description": "Follow-up review", "amount": 380.00},
            {"description": "GST (18%)", "amount": 180.00},
        ]
        total = 1179.00 if release == "r5" else 1180.00
        return {"invoice_number": "MQ-2408-1180", "line_items": line_items, "total": total, "currency": "INR"}

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "release": self._release,
                "doctor_calls": self._doctor_calls,
                "appointment_count": len(self._appointments),
                "settings": deepcopy(self._settings),
            }

    def _reset_locked(self) -> None:
        base = datetime.now().replace(second=0, microsecond=0)
        appointments: list[dict[str, Any]] = []
        for index in range(60):
            doctor = DOCTORS[index % len(DOCTORS)]
            patient = f"{_FIRST_NAMES[index % len(_FIRST_NAMES)]} {_LAST_NAMES[(index * 7) % len(_LAST_NAMES)]}"
            if index == 47:
                patient = "Rahul Iyer"
            slot = (base + timedelta(days=(index // 3) + 1, hours=(index % 3) * 2)).replace(minute=0)
            visit_type = ("Consultation", "Follow-up", "Emergency")[index % 3]
            fee = 0 if visit_type == "Emergency" else float(doctor["fee"])
            appointments.append(
                {
                    "id": f"apt-{index + 1:03d}",
                    "patient": patient,
                    "age": 18 + (index % 57),
                    "phone": f"+91 98{600000000 + index:09d}",
                    "doctor_id": doctor["id"],
                    "doctor_name": doctor["name"],
                    "specialty": doctor["specialty"],
                    "slot": slot.isoformat(timespec="minutes"),
                    "visit_type": visit_type,
                    "referral_source": "Patient" if index % 2 == 0 else "Search engine",
                    "status": "confirmed",
                    "doctor_fee": fee,
                    "gst": round(fee * 0.18, 2),
                    "total": round(fee * 1.18, 2),
                }
            )
        self._appointments = appointments
        self._next_appointment = len(appointments) + 1
        self._doctor_calls = 0
        self._settings = {
            "clinic_name": "MediQueue Health Centre",
            "sms_reminders": True,
        }


store = ExamStore()
