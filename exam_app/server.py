from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from exam_app.release import write_changelog
from exam_app.state import DOCTORS, RELEASES, ExamStore, store

BASE_DIR = Path(__file__).resolve().parent
DEMO_EMAIL = "reception@mediqueue.io"
DEMO_PASSWORD = "triage42"
VISIT_TYPES = {"Consultation", "Follow-up", "Emergency"}


class LoginRequest(BaseModel):
    email: str
    password: str


class BookingRequest(BaseModel):
    patient: str = Field(min_length=1, max_length=100)
    age: int
    phone: str = Field(min_length=6, max_length=30)
    doctor_id: str
    slot: str
    visit_type: str
    referral_source: str | None = None


class RescheduleRequest(BaseModel):
    slot: str


class ReleaseRequest(BaseModel):
    release: str


class SettingsRequest(BaseModel):
    clinic_name: str | None = None
    sms_reminders: bool | None = None


def create_app(exam_store: ExamStore = store) -> FastAPI:
    """Create the MediQueue exam application."""
    application = FastAPI(title="MediQueue", version="1.0.0")
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @application.get("/", response_class=HTMLResponse)
    @application.get("/app/{route}", response_class=HTMLResponse)
    async def spa(request: Request, route: str = "") -> HTMLResponse:
        if route == "settings" and exam_store.release == "r6":
            raise HTTPException(status_code=404, detail="Settings were retired in r6")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"initial_release": exam_store.release},
        )

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> JSONResponse:
        return JSONResponse(status_code=204, content=None)

    @application.post("/api/login")
    async def login(payload: LoginRequest) -> dict[str, str]:
        if payload.email.strip().lower() != DEMO_EMAIL or payload.password != DEMO_PASSWORD:
            raise HTTPException(status_code=401, detail="Email or password is incorrect")
        return {"status": "authenticated", "user": "Front Desk"}

    @application.get("/api/state")
    @application.get("/__exam/state")
    async def app_state() -> dict[str, Any]:
        return exam_store.state()

    @application.get("/api/doctors")
    async def doctors() -> list[dict[str, Any]]:
        call_number = exam_store.next_doctor_call()
        if exam_store.release == "r6" and call_number % 3 == 0:
            await asyncio.sleep(3.5)
        return exam_store.doctors()

    @application.get("/api/slots")
    async def slots(doctor_id: str) -> list[dict[str, str]]:
        if not any(doctor["id"] == doctor_id for doctor in DOCTORS):
            raise HTTPException(status_code=404, detail="Doctor not found")
        now = datetime.now().replace(second=0, microsecond=0)
        candidate = now.replace(minute=0) + timedelta(minutes=30)
        if candidate <= now:
            candidate += timedelta(hours=1)
        values: list[dict[str, str]] = []
        for index in range(0, 10, 2):
            value = (candidate + timedelta(hours=index)).isoformat(timespec="minutes")
            values.append({"value": value, "label": format_slot(value)})
        return values

    @application.post("/api/appointments")
    async def create_appointment(payload: BookingRequest) -> dict[str, Any]:
        release = exam_store.release
        doctor = next((item for item in DOCTORS if item["id"] == payload.doctor_id), None)
        if doctor is None:
            raise HTTPException(status_code=404, detail="Doctor not found")
        if payload.visit_type not in VISIT_TYPES:
            raise HTTPException(status_code=422, detail="Choose a valid visit type")
        patient = payload.patient.strip()
        if not patient:
            raise HTTPException(status_code=422, detail="Patient name is required")
        if release != "r5" and not 0 <= payload.age <= 120:
            raise HTTPException(status_code=422, detail="Age must be between 0 and 120")
        if release in {"r3", "r4", "r5", "r6"} and not (payload.referral_source or "").strip():
            raise HTTPException(status_code=422, detail="Referral source is required")
        parsed_slot = parse_future_slot(payload.slot)
        if release == "r5":
            raise HTTPException(status_code=500, detail="Unable to save appointment")
        doctor_fee = booking_fee(release, float(doctor["fee"]), payload.visit_type)
        gst = round(doctor_fee * 0.18, 2)
        appointment = exam_store.add_appointment(
            {
                "patient": patient,
                "age": payload.age,
                "phone": payload.phone.strip(),
                "doctor_id": doctor["id"],
                "doctor_name": doctor["name"],
                "specialty": doctor["specialty"],
                "slot": parsed_slot.isoformat(timespec="minutes"),
                "visit_type": payload.visit_type,
                "referral_source": (payload.referral_source or "Direct").strip(),
                "status": "confirmed",
                "doctor_fee": doctor_fee,
                "gst": gst,
                "total": round(doctor_fee + gst, 2),
            }
        )
        return {"status": "confirmed", "appointment": appointment}

    @application.get("/api/appointments")
    async def appointments() -> list[dict[str, Any]]:
        return exam_store.appointments()

    @application.put("/api/appointments/{appointment_id}/reschedule")
    async def reschedule(appointment_id: str, payload: RescheduleRequest) -> dict[str, str]:
        parsed_slot = parse_future_slot(payload.slot)
        if not exam_store.reschedule_appointment(appointment_id, parsed_slot.isoformat(timespec="minutes")):
            raise HTTPException(status_code=404, detail="Appointment not found")
        return {"status": "rescheduled", "appointment_id": appointment_id}

    @application.delete("/api/appointments/{appointment_id}")
    async def cancel(appointment_id: str) -> dict[str, str]:
        if not exam_store.cancel_appointment(appointment_id):
            raise HTTPException(status_code=404, detail="Appointment not found")
        return {"status": "cancelled", "appointment_id": appointment_id}

    @application.get("/api/billing")
    async def billing() -> dict[str, Any]:
        return exam_store.billing()

    @application.get("/api/settings")
    async def settings() -> dict[str, Any]:
        if exam_store.release == "r6":
            raise HTTPException(status_code=404, detail="Settings were retired in r6")
        return exam_store.settings()

    @application.put("/api/settings")
    async def update_settings(payload: SettingsRequest) -> dict[str, Any]:
        if exam_store.release == "r6":
            raise HTTPException(status_code=404, detail="Settings were retired in r6")
        changes = payload.model_dump(exclude_none=True)
        return exam_store.update_settings(changes)

    @application.post("/__exam/release")
    async def switch_release(payload: ReleaseRequest) -> dict[str, Any]:
        if payload.release not in RELEASES:
            raise HTTPException(status_code=422, detail="Release must be r1 through r6")
        exam_store.switch_release(payload.release)
        write_changelog(exam_store.release)
        return exam_store.state()

    @application.post("/__exam/reset")
    async def reset() -> dict[str, Any]:
        exam_store.reset()
        return exam_store.state()

    return application


def parse_future_slot(value: str) -> datetime:
    """Parse a slot and reject values that are not in the future."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Choose a valid appointment time") from error
    now = datetime.now()
    comparable = parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    if comparable <= now:
        raise HTTPException(status_code=422, detail="Appointment time must be in the future")
    return comparable


def booking_fee(release: str, doctor_fee: float, visit_type: str) -> float:
    """Calculate the doctor fee for the active release."""
    if visit_type == "Emergency":
        return 500.0 if release == "r5" else 0.0
    if visit_type == "Follow-up":
        return round(doctor_fee * 0.6, 2)
    return doctor_fee


def format_slot(value: str) -> str:
    """Format an API slot for display."""
    parsed = datetime.fromisoformat(value)
    if parsed.date() == (datetime.now() + timedelta(days=1)).date():
        return f"Tomorrow · {parsed.strftime('%I:%M %p').lstrip('0')}"
    if parsed.date() == datetime.now().date():
        return f"Today · {parsed.strftime('%I:%M %p').lstrip('0')}"
    return parsed.strftime("%a, %d %b · ").lstrip("0") + parsed.strftime("%I:%M %p").lstrip("0")


app = create_app()


def main() -> None:
    """Run the exam server."""
    parser = argparse.ArgumentParser(description="Run the MediQueue exam app")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
