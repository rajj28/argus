from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from collections.abc import Generator, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8010"
# The server rewrites these as releases switch; the smoke run restores them so the tree stays clean.
TRACKED_CONTEXT = (ROOT / "exam" / "context" / "CHANGELOG.md",)
DEMO_EMAIL = "reception@mediqueue.io"
DEMO_PASSWORD = "triage42"


@pytest.fixture(scope="session")
def live_server() -> Generator[str, None, None]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", 8010)) == 0:
            raise RuntimeError("Port 8010 is already in use")
    snapshot = {path: path.read_bytes() for path in TRACKED_CONTEXT if path.exists()}
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        [sys.executable, "-m", "exam_app.server", "--port", "8010"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("MediQueue server exited before becoming ready")
            try:
                if httpx.get(f"{BASE_URL}/__exam/state", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.15)
        else:
            raise RuntimeError("MediQueue server did not become ready")
        yield BASE_URL
    finally:
        try:
            httpx.post(f"{BASE_URL}/__exam/release", json={"release": "r1"}, timeout=2)
        except httpx.HTTPError:
            pass
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for path, content in snapshot.items():
            path.write_bytes(content)


@pytest.fixture(scope="session")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 960})
    context.set_default_timeout(8_000)
    current_page = context.new_page()
    yield current_page
    context.close()


@pytest.fixture
def api(live_server: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=live_server, timeout=10) as client:
        yield client


def set_release(api: httpx.Client, release: str) -> None:
    response = api.post("/__exam/release", json={"release": release})
    response.raise_for_status()
    assert response.json()["release"] == release


def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/#/login")
    expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()
    page.get_by_label("Email address").fill(DEMO_EMAIL)
    page.get_by_label("Password").fill(DEMO_PASSWORD)
    page.get_by_role("button", name="Sign in to MediQueue").click()
    expect(page.get_by_role("heading", name="Good morning, Front Desk")).to_be_visible()


def open_doctor_booking(page: Page) -> None:
    page.get_by_role("link", name="Doctors").click()
    expect(page.get_by_text("Loading directory")).to_be_visible()
    expect(page.get_by_role("heading", name="Dr. Aarav Mehta")).to_be_visible(timeout=6_000)
    card = page.locator("article").filter(has_text="Dr. Aarav Mehta")
    card.locator("button").click()
    expect(page.get_by_role("button", name=re.compile("^(Next|Proceed)$"))).to_be_visible()


def fill_patient(page: Page, age: str, visit_type: str, referral: str | None = None) -> None:
    page.get_by_label(re.compile("^Patient name")).fill("Smoke Patient")
    page.get_by_label(re.compile("^Age")).fill(age)
    page.get_by_label(re.compile("^Phone number")).fill("+91 9876543210")
    page.get_by_role("button", name=re.compile("^Choose visit type")).click()
    page.get_by_role("option", name=visit_type).click()
    if referral is not None:
        page.get_by_role("button", name=re.compile("^Choose referral source")).click()
        page.get_by_role("option", name=referral).click()


def test_all_release_switches_and_changelog(api: httpx.Client, live_server: str) -> None:
    for index, release in enumerate(("r1", "r2", "r3", "r4", "r5", "r6"), start=1):
        set_release(api, release)
        assert api.get("/__exam/state").json()["release"] == release
        changelog = (ROOT / "exam" / "context" / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## r{index}" in changelog
        if index < 6:
            assert f"## r{index + 1}" not in changelog
    assert (ROOT / "exam" / "releases" / "r4.md").read_text(encoding="utf-8") == ""


def test_r1_booking_validation_invoice_and_virtual_actions(
    page: Page,
    api: httpx.Client,
    live_server: str,
) -> None:
    set_release(api, "r1")
    login(page)
    open_doctor_booking(page)
    fill_patient(page, "35", "Consultation")
    page.get_by_role("button", name="Next").click()
    page.get_by_role("radio").first.check()
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name="Confirm booking").click()
    expect(page.get_by_text("Appointment confirmed")).to_be_visible()
    schedule = page.get_by_role("region", name="Appointment list").or_(page.get_by_label("Appointment list"))
    schedule.evaluate("element => { element.scrollTop = element.scrollHeight; }")
    expect(page.get_by_text("Smoke Patient", exact=True)).to_be_visible()
    assert any(item["patient"] == "Smoke Patient" for item in api.get("/api/appointments").json())

    page.get_by_role("link", name="Doctors").click()
    card = page.locator("article").filter(has_text="Dr. Aarav Mehta")
    expect(card.get_by_role("button", name="Book")).to_be_visible(timeout=6_000)
    card.get_by_role("button", name="Book").click()
    fill_patient(page, "150", "Consultation")
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_text("Age must be between 0 and 120")).to_be_visible()

    page.get_by_role("link", name="Doctors").click()
    card = page.locator("article").filter(has_text="Dr. Aarav Mehta")
    expect(card.get_by_role("button", name="Book")).to_be_visible(timeout=6_000)
    card.get_by_role("button", name="Book").click()
    fill_patient(page, "29", "Emergency")
    page.get_by_role("button", name="Next").click()
    page.get_by_role("radio").first.check()
    page.get_by_role("button", name="Next").click()
    fee_row = page.get_by_text("Doctor fee", exact=True).locator("..")
    total_row = page.get_by_text("Total", exact=True).locator("..")
    expect(fee_row).to_contain_text("₹0.00")
    expect(total_row).to_contain_text("₹0.00")

    page.get_by_role("link", name="Billing").click()
    expect(page.get_by_text("₹1,180.00")).to_be_visible()
    invoice = api.get("/api/billing").json()
    assert round(sum(item["amount"] for item in invoice["line_items"]), 2) == invoice["total"]

    page.get_by_role("link", name="Appointments").click()
    schedule = page.get_by_label("Appointment list")
    schedule.evaluate("element => { element.scrollTop = 47 * 86; }")
    rahul = page.locator("article").filter(has_text="Rahul Iyer")
    expect(rahul).to_be_visible()
    appointment_id = rahul.get_attribute("data-mq-appointment")
    count_before = len(api.get("/api/appointments").json())
    rahul.get_by_role("button", name="Cancel").click()
    expect(page.get_by_role("dialog", name="Cancel appointment")).to_be_visible()
    assert len(api.get("/api/appointments").json()) == count_before
    page.get_by_role("button", name="Yes, cancel").click()
    expect(page.get_by_text("Appointment cancelled")).to_be_visible()
    assert len(api.get("/api/appointments").json()) == count_before - 1
    assert all(item["id"] != appointment_id for item in api.get("/api/appointments").json())

    schedule.evaluate("element => { element.scrollTop = 0; }")
    first_row = page.locator("article").first
    first_id = first_row.get_attribute("data-mq-appointment")
    old_slot = next(item["slot"] for item in api.get("/api/appointments").json() if item["id"] == first_id)
    first_row.get_by_role("button", name="Reschedule").click()
    page.get_by_role("dialog", name="Reschedule appointment").get_by_role("radio").first.check()
    page.get_by_role("button", name="Confirm new time").click()
    expect(page.get_by_text("Appointment rescheduled")).to_be_visible()
    new_slot = next(item["slot"] for item in api.get("/api/appointments").json() if item["id"] == first_id)
    assert new_slot != old_slot

    page.get_by_role("link", name="Settings").click()
    switch = page.get_by_role("switch", name="SMS reminders")
    expect(switch).to_have_attribute("aria-checked", "true")
    switch.click()
    expect(page.get_by_text("SMS reminder preference saved")).to_be_visible()
    assert api.get("/api/settings").json()["sms_reminders"] is False


def test_r2_refresh_preserves_behavior(page: Page, api: httpx.Client, live_server: str) -> None:
    set_release(api, "r2")
    login(page)
    page.get_by_role("link", name="Doctors").click()
    expect(page.get_by_role("heading", name="Dr. Rohan Sen")).to_be_visible(timeout=6_000)
    assert page.locator(".v2-doctor-card").count() == 8
    assert page.locator(".mq-doctor-card").count() == 0
    expect(page.get_by_role("button", name="Reserve").first).to_be_visible()
    expect(page.get_by_role("button", name="Book appointment").first).to_be_visible()


def test_r3_intended_booking_changes_and_modal(page: Page, api: httpx.Client, live_server: str) -> None:
    set_release(api, "r3")
    login(page)
    expect(page.get_by_role("dialog", name="What’s new")).to_be_visible()
    page.get_by_role("button", name="Got it").click()
    expect(page.get_by_role("dialog", name="What’s new")).to_have_count(0)
    page.reload()
    expect(page.get_by_role("dialog", name="What’s new")).to_have_count(0)

    open_doctor_booking(page)
    expect(page.get_by_role("heading", name="Choose a time")).to_be_visible()
    page.get_by_role("radio").first.check()
    page.get_by_role("button", name="Proceed").click()
    expect(page.get_by_role("heading", name="Patient details")).to_be_visible()
    fill_patient(page, "42", "Consultation", "Patient recommendation")
    page.get_by_role("button", name="Proceed").click()
    expect(page.get_by_role("heading", name="Review and confirm")).to_be_visible()

    page.get_by_role("link", name="Appointments").click()
    first_row = page.locator("article").first
    expect(first_row.get_by_role("button", name="Reschedule")).to_have_count(0)
    first_row.get_by_role("button", name="Appointment actions").click()
    expect(first_row.get_by_role("menuitem", name="Reschedule")).to_be_visible()


def test_r4_silent_cancel_move(page: Page, api: httpx.Client, live_server: str) -> None:
    set_release(api, "r4")
    login(page)
    page.get_by_role("link", name="Appointments", exact=True).click()
    first_row = page.locator("article").first
    expect(first_row.get_by_role("button", name="Cancel")).to_have_count(0)
    first_row.get_by_role("button", name="Appointment actions").click()
    expect(first_row.get_by_role("menuitem", name="Cancel")).to_be_visible()
    first_row.get_by_role("menuitem", name="Cancel").click()
    page.get_by_role("button", name="Yes, cancel").click()
    expect(page.get_by_text("Appointment cancelled")).to_be_visible()


def test_r5_business_rule_bugs(page: Page, api: httpx.Client, live_server: str) -> None:
    set_release(api, "r5")
    login(page)
    open_doctor_booking(page)
    page.get_by_role("radio").first.check()
    page.get_by_role("button", name="Proceed").click()
    fill_patient(page, "150", "Consultation", "Patient recommendation")
    page.get_by_role("button", name="Proceed").click()
    page.get_by_role("button", name="Confirm booking").click()
    expect(page.get_by_text("Appointment confirmed")).to_be_visible()
    expect(page.get_by_role("heading", name="Appointments", exact=True)).to_be_visible()
    assert len(api.get("/api/appointments").json()) == 60

    page.get_by_role("link", name="Doctors").click()
    card = page.locator("article").filter(has_text="Dr. Aarav Mehta")
    expect(card.get_by_role("button", name="Reserve")).to_be_visible(timeout=6_000)
    card.get_by_role("button", name="Reserve").click()
    page.get_by_role("radio").first.check()
    page.get_by_role("button", name="Proceed").click()
    fill_patient(page, "31", "Emergency", "Patient recommendation")
    page.get_by_role("button", name="Proceed").click()
    expect(page.get_by_text("Doctor fee", exact=True).locator("..")).to_contain_text("₹500.00")

    page.get_by_role("link", name="Billing").click()
    expect(page.get_by_text("₹1,179.00")).to_be_visible()
    invoice = api.get("/api/billing").json()
    assert round(sum(item["amount"] for item in invoice["line_items"]), 2) == 1180.0
    assert invoice["total"] == 1179.0


def test_r6_retirement_and_infrastructure_delay(page: Page, api: httpx.Client, live_server: str) -> None:
    set_release(api, "r6")
    assert api.get("/app/settings").status_code == 404
    assert api.get("/api/settings").status_code == 404
    login(page)
    expect(page.get_by_role("link", name="Settings")).to_have_count(0)
    started = time.monotonic()
    doctors = api.get("/api/doctors")
    first_elapsed = time.monotonic() - started
    assert doctors.status_code == 200
    assert len(doctors.json()) == 8
    assert first_elapsed < 1.0
    assert api.get("/api/doctors").status_code == 200
    started = time.monotonic()
    third = api.get("/api/doctors")
    third_elapsed = time.monotonic() - started
    assert third.status_code == 200
    assert len(third.json()) == 8
    assert third_elapsed >= 3.3
    assert api.get("/__exam/state").json()["doctor_calls"] == 3
