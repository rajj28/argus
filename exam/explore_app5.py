"""Explore SMS toggle web component and appointment virtualized scroll."""
from __future__ import annotations
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8010"
EMAIL = "reception@mediqueue.io"
PASSWORD = "triage42"


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/#/login", wait_until="networkidle")
        await page.fill("input[name=email]", EMAIL)
        await page.fill("input[type=password]", PASSWORD)
        await page.click("button[type=submit]")
        await page.wait_for_timeout(1500)

        # Settings page - SMS toggle
        await page.goto(f"{BASE_URL}/#/app/settings", wait_until="networkidle")
        await page.wait_for_timeout(500)

        # Inspect mq-toggle custom element
        toggle_info = await page.evaluate("""() => {
            const el = document.querySelector('mq-toggle');
            if (!el) return 'not found';
            const shadow = el.shadowRoot;
            const inner = shadow ? shadow.innerHTML : 'no shadow root';
            return {
                outerHTML: el.outerHTML,
                shadowHTML: inner.slice(0, 1000),
                ariaLabel: el.getAttribute('aria-label'),
                ariaChecked: el.getAttribute('aria-checked'),
                role: el.getAttribute('role'),
                dataset: JSON.stringify(el.dataset),
                lightChildren: el.innerHTML.slice(0, 300)
            };
        }""")
        print("MQ-Toggle info:", toggle_info)

        # Check if there's a checkbox or button inside
        toggle_inner = await page.evaluate("""() => {
            const el = document.querySelector('mq-toggle');
            if (!el) return 'no mq-toggle';
            // Check light DOM children
            const children = Array.from(el.children);
            const shadow = el.shadowRoot;
            const shadowChildren = shadow ? Array.from(shadow.querySelectorAll('*')) : [];
            return {
                lightChildren: children.map(c => ({tag: c.tagName, id: c.id, role: c.getAttribute('role'), type: c.type})),
                shadowChildren: shadowChildren.map(c => ({tag: c.tagName, id: c.id, role: c.getAttribute('role'), type: c.type}))
            };
        }""")
        print("Toggle inner structure:", toggle_inner)

        # Try clicking the mq-toggle
        print("\nCurrent SMS state:")
        state_before = await page.evaluate("() => ({ ariaChecked: document.querySelector('mq-toggle')?.getAttribute('aria-checked'), checked: document.querySelector('mq-toggle')?.checked })")
        print("Before:", state_before)

        # Click the toggle
        await page.click("mq-toggle", timeout=2000)
        await page.wait_for_timeout(500)
        state_after = await page.evaluate("() => ({ ariaChecked: document.querySelector('mq-toggle')?.getAttribute('aria-checked') })")
        print("After click:", state_after)

        # Check network call
        print("\nTrying to save SMS setting:")
        # Check if clicking triggers a network request
        reqs = []
        page.on("request", lambda r: reqs.append(f"{r.method} {r.url}") if "/api/" in r.url else None)
        await page.click("mq-toggle", timeout=2000)
        await page.wait_for_timeout(1000)
        print("Network requests:", reqs)

        # Check the actual state via API
        import urllib.request
        state_api = urllib.request.urlopen(f"{BASE_URL}/__exam/state", timeout=5).read()
        print("API state:", state_api.decode())

        # Reload and check persistence
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(500)
        state_reload = await page.evaluate("() => document.querySelector('mq-toggle')?.getAttribute('aria-checked')")
        print("After reload:", state_reload)

        # ---- Appointment virtualized scroll - find Rahul ----
        print("\n=== FINDING RAHUL IYER (VIRTUALIZED) ===")
        await page.goto(f"{BASE_URL}/#/app/appointments", wait_until="networkidle")
        await page.wait_for_timeout(500)

        # The list seems to have transform: translateY - virtualized rendering
        # We need to scroll the viewport
        count = await page.evaluate("() => parseInt(document.body.innerText.match(/(\d+) confirmed/)?.[1] || '0')")
        print("Appointment count shown:", count)

        # Find the scroll container and its dimensions
        scroll_info = await page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('*'));
            const scrollable = all.filter(el => {
                const s = window.getComputedStyle(el);
                return (s.overflow === 'auto' || s.overflow === 'scroll' || s.overflowY === 'auto' || s.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight + 10;
            });
            return scrollable.map(el => ({
                tag: el.tagName.toLowerCase(),
                cls: el.className.slice(0, 50),
                id: el.id,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight
            }));
        }""")
        print("Scrollable containers:", scroll_info)

        # Try scrolling the page to trigger virtualized rendering
        # First get the window-like scroll container
        scroll_el = await page.query_selector(".mq-appointment-list, [class*=appointment-list], [class*=schedule-list]")
        print("Schedule list element:", scroll_el)
        
        # Scroll the body 10 times
        for _ in range(20):
            await page.evaluate("window.scrollBy(0, 400)")
            await page.wait_for_timeout(50)
        
        has_rahul = await page.evaluate("() => document.body.innerText.includes('Rahul Iyer')")
        print("Rahul after window scrolling:", has_rahul)
        
        # Try keyboard scrolling
        await page.keyboard.press("End")
        await page.wait_for_timeout(200)
        has_rahul = await page.evaluate("() => document.body.innerText.includes('Rahul Iyer')")
        print("Rahul after End key:", has_rahul)
        
        # Let's look at the actual scroll container class  
        apt_html = await page.evaluate("""() => {
            const el = document.querySelector('#mq-content');
            return el ? el.innerHTML.slice(0, 2000) : 'not found';
        }""")
        print("mq-content HTML:", apt_html)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
