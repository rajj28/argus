"""Explore step 3 confirm, doctor async loading, and appointment scroll."""
from __future__ import annotations
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def login_and_book_step3(browser) -> None:
    """Perform full booking up to step 3 confirm."""
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
    await page.fill("input[name=email]", EMAIL)
    await page.fill("input[type=password]", PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_timeout(1500)

    await page.goto(f"{BASE_URL}/#/app/doctors", wait_until="networkidle")
    await page.wait_for_timeout(1000)
    await page.click("button:has-text('Book')", timeout=3000)
    await page.wait_for_timeout(500)

    await page.fill("#mq-patient-name", "Test Patient")
    await page.fill("#mq-patient-age", "35")
    await page.fill("#mq-patient-phone", "+91 9876543210")
    await page.click(".mq-select-trigger", timeout=2000)
    await page.wait_for_timeout(200)
    await page.click("[data-mq-option='Consultation']", timeout=2000)
    await page.wait_for_timeout(200)
    await page.click("#mq-booking-next")
    await page.wait_for_timeout(1000)

    # Select first slot
    await page.click("label.mq-slot", timeout=2000)
    await page.wait_for_timeout(200)
    await page.click("#mq-booking-next")
    await page.wait_for_timeout(1000)

    # Step 3 - Confirm
    print("=== STEP 3 - CONFIRM ===")
    print("URL:", page.url)
    body = await page.evaluate("() => document.body.innerText.slice(0,600)")
    print("Body:", body)
    ids = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('[id]')).map(el => ({id: el.id, tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0,40)}));
    }""")
    print("IDs:", ids)
    # get all buttons
    btns = await page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => ({id: b.id, text: b.textContent.trim().slice(0,40)}))")
    print("Buttons:", btns)

    # Check the fee breakdown HTML
    fee_html = await page.evaluate("""() => {
        const el = document.querySelector('[class*=fee], [class*=price], [class*=breakdown], [class*=summary], [class*=review], [class*=confirm]');
        return el ? el.innerHTML.slice(0, 1000) : 'not found';
    }""")
    print("Fee HTML:", fee_html)
    
    # Get ALL text with data attributes
    data_els = await page.evaluate("""() => {
        const els = document.querySelectorAll('[data-mq-fee], [data-mq-total], [data-fee], [data-total], [class*=mq-fee], [class*=mq-total], [class*=mq-amount]');
        return Array.from(els).map(el => ({tag: el.tagName.toLowerCase(), cls: el.className, text: el.textContent.trim(), dataset: JSON.stringify(el.dataset)}));
    }""")
    print("Fee data elements:", data_els)

    # Click Confirm
    for sel in ["#mq-booking-confirm", "button:has-text('Confirm booking')", "button:has-text('Confirm')", "button:has-text('Book')"]:
        try:
            await page.click(sel, timeout=2000)
            break
        except Exception:
            continue
    await page.wait_for_timeout(1500)
    print("\n=== AFTER CONFIRM ===")
    print("URL:", page.url)
    body = await page.evaluate("() => document.body.innerText.slice(0,500)")
    print("Body:", body)
    
    await context.close()


async def explore_doctor_loading(browser) -> None:
    """Check doctor directory async loading state."""
    print("\n=== DOCTOR DIRECTORY LOADING ===")
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
    await page.fill("input[name=email]", EMAIL)
    await page.fill("input[type=password]", PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_timeout(1500)

    # Navigate to doctors and capture the loading state quickly
    all_frames = []
    
    # Navigate and capture early state
    await page.goto(f"{BASE_URL}/#/app/doctors", wait_until="domcontentloaded")
    # Immediately take snapshot
    early_body = await page.evaluate("() => document.body.innerText.slice(0,400)")
    print("Early body (loading?):", early_body)
    
    # Wait for network idle
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(500)
    final_body = await page.evaluate("() => document.body.innerText.slice(0,600)")
    print("Final body (loaded):", final_body)
    
    # Get loading indicator HTML
    load_html = await page.evaluate("""() => {
        const el = document.querySelector('[class*=loading], [class*=spinner], [class*=skeleton], [aria-busy]');
        return el ? el.outerHTML.slice(0, 300) : 'no loading element found';
    }""")
    print("Loading indicator HTML:", load_html)
    
    await context.close()


async def explore_appointments_scroll(browser) -> None:
    """Explore appointment list scroll and Rahul Iyer."""
    print("\n=== APPOINTMENTS SCROLL ===")
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
    await page.fill("input[name=email]", EMAIL)
    await page.fill("input[type=password]", PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_timeout(1500)

    await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
    await page.wait_for_timeout(500)

    # Get the appointment list structure
    list_html = await page.evaluate("""() => {
        const el = document.querySelector('[class*=list], [class*=schedule], [role=list], [class*=appt]');
        return el ? el.outerHTML.slice(0, 800) : 'not found';
    }""")
    print("List HTML:", list_html)

    # Check if Rahul is on page without scrolling
    has_rahul = await page.evaluate("() => document.body.innerText.includes('Rahul Iyer')")
    print("Rahul Iyer visible without scroll:", has_rahul)

    # Scroll down progressively
    for i in range(20):
        await page.keyboard.press("End")
        await page.wait_for_timeout(100)
        has_rahul = await page.evaluate("() => document.body.innerText.includes('Rahul Iyer')")
        if has_rahul:
            print(f"Found Rahul after {i+1} scrolls")
            break
    
    # Try scrolling the appointment container
    found_by_scroll = await page.evaluate("""() => {
        // Find scrollable container
        const containers = Array.from(document.querySelectorAll('*')).filter(el => {
            const style = window.getComputedStyle(el);
            return (style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 100;
        });
        console.log('Scrollable containers:', containers.length);
        for (const c of containers) {
            c.scrollTop = c.scrollHeight;
        }
        return containers.length;
    }""")
    print("Scrollable containers:", found_by_scroll)
    await page.wait_for_timeout(500)
    has_rahul = await page.evaluate("() => document.body.innerText.includes('Rahul Iyer')")
    print("Rahul after scrolling containers:", has_rahul)

    body = await page.evaluate("() => document.body.innerText.slice(-600)")
    print("Bottom of page:", body)
    
    # Check appointment item HTML
    apt_html = await page.evaluate("""() => {
        const items = document.querySelectorAll('[class*=apt], [class*=appt], [class*=appoint], [class*=patient], [class*=visit]');
        if (items.length > 0) return items[0].outerHTML.slice(0, 500);
        return 'no appt items found';
    }""")
    print("Appointment item HTML:", apt_html)
    
    await context.close()


async def explore_cancel_dialog(browser) -> None:
    """Explore cancel confirmation dialog."""
    print("\n=== CANCEL CONFIRMATION DIALOG ===")
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
    await page.fill("input[name=email]", EMAIL)
    await page.fill("input[type=password]", PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_timeout(1500)

    await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
    await page.wait_for_timeout(500)

    # click Cancel on first appointment
    await page.click("button:has-text('Cancel')", timeout=2000)
    await page.wait_for_timeout(500)

    # Get modal HTML
    modal_html = await page.evaluate("""() => {
        const modal = document.querySelector('[role=dialog], [class*=modal], [class*=dialog], #mq-modal-region');
        return modal ? modal.innerHTML.slice(0, 1000) : 'no modal';
    }""")
    print("Modal HTML:", modal_html)

    body = await page.evaluate("() => document.body.innerText.slice(0,600)")
    print("Body after cancel click:", body)

    await context.close()


async def explore_reschedule(browser) -> None:
    """Explore reschedule flow."""
    print("\n=== RESCHEDULE FLOW ===")
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
    await page.fill("input[name=email]", EMAIL)
    await page.fill("input[type=password]", PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_timeout(1500)

    await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
    await page.wait_for_timeout(500)

    # Click Reschedule on first appointment
    await page.click("button:has-text('Reschedule')", timeout=2000)
    await page.wait_for_timeout(500)

    modal_html = await page.evaluate("""() => {
        const modal = document.querySelector('[role=dialog], [class*=modal], [class*=dialog], #mq-modal-region');
        return modal ? modal.innerHTML.slice(0, 1500) : 'no modal';
    }""")
    print("Reschedule modal HTML:", modal_html)
    body = await page.evaluate("() => document.body.innerText.slice(0,500)")
    print("Body:", body)

    await context.close()


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        await login_and_book_step3(browser)
        await explore_doctor_loading(browser)
        await explore_appointments_scroll(browser)
        await explore_cancel_dialog(browser)
        await explore_reschedule(browser)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
