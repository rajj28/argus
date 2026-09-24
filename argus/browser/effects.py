"""argus/browser/effects.py — EffectRecorder and wait_for_settle.

Records what happened after a browser action:
  - Network calls (fetch, xhr, document — same-origin only)
  - Console errors
  - Page (JS) errors
  - Dialogs (auto-accepted, message captured)
  - DOM changes (via structural signature diff)
  - URL / title / headings / alerts diff

Public API:
    class EffectRecorder
    async def wait_for_settle(page, recorder, timeout_ms=4000)
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.async_api import Page, ConsoleMessage, Request, Response

from argus.models import Effects, NetCall
from argus.browser.snapshot import normalize_path, take_snapshot


class EffectRecorder:
    """Attach to a page, call begin() before an action, end() after to get Effects."""

    def __init__(self, page: "Page") -> None:
        self._page = page
        self._origin: str = ""
        self._net: list[NetCall] = []
        self._console_errors: list[str] = []
        self._page_errors: list[str] = []
        self._dialogs: list[str] = []
        self._url_before: str = ""
        self._title_before: str = ""
        self._sig_before: str = ""
        self._headings_before: list[str] = []
        self._alerts_before: list[str] = []
        # Pending requests (for failed tracking) and cumulative error logs (never cleared)
        self._pending: dict[str, "Request"] = {}
        self._all_page_errors: list[str] = []
        self._all_console: list[str] = []
        self._last_net_activity: float = 0.0

        # Attach event listeners once (idempotent on repeated begin/end)
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._page.on("requestfailed", self._on_request_failed)
        self._page.on("console", self._on_console)
        self._page.on("pageerror", self._on_page_error)
        self._page.on("dialog", self._on_dialog)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_request(self, request: "Request") -> None:
        rt = request.resource_type
        if rt not in ("fetch", "xhr", "document"):
            return
        url = request.url
        if not self._is_same_origin(url):
            return
        self._pending[request.url + "|" + request.method] = request

    def _on_response(self, response: "Response") -> None:
        rt = response.request.resource_type
        if rt not in ("fetch", "xhr", "document"):
            return
        url = response.url
        if not self._is_same_origin(url):
            return
        key = url + "|" + response.request.method
        self._pending.pop(key, None)
        self._net.append(NetCall(
            method=response.request.method,
            path=normalize_path(url),
            status=response.status,
            resource_type=rt,
        ))

    def _on_request_failed(self, request: "Request") -> None:
        rt = request.resource_type
        if rt not in ("fetch", "xhr", "document"):
            return
        url = request.url
        if not self._is_same_origin(url):
            return
        key = url + "|" + request.method
        self._pending.pop(key, None)
        failure = (request.failure or "") if not callable(request.failure) else ""
        if "ERR_ABORTED" in str(failure) or "NS_BINDING_ABORTED" in str(failure):
            return  # superseded navigation / cancelled fetch, not an app failure
        self._net.append(NetCall(
            method=request.method,
            path=normalize_path(url),
            status=0,
            resource_type=rt,
        ))

    def _on_console(self, msg: "ConsoleMessage") -> None:
        if msg.type == "error":
            self._console_errors.append(msg.text)
            self._all_console.append(msg.text)

    def _on_page_error(self, error: Exception) -> None:
        self._page_errors.append(str(error))
        self._all_page_errors.append(str(error))

    def _on_dialog(self, dialog) -> None:  # type: ignore[no-untyped-def]
        """Auto-accept all dialogs and record their message."""
        self._dialogs.append(dialog.message or "")
        asyncio.ensure_future(dialog.accept())

    # ── Public API ────────────────────────────────────────────────────────────

    async def begin(self) -> None:
        """Snapshot current state (url, title, headings, alerts, signature) before action."""
        self._net.clear()
        self._console_errors.clear()
        self._page_errors.clear()
        self._dialogs.clear()
        self._pending.clear()

        self._url_before = self._page.url
        self._title_before = await self._page.title()
        # Determine same-origin from current URL
        parsed = urlparse(self._url_before)
        self._origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

        try:
            snap_before = await take_snapshot(self._page)
            self._sig_before = snap_before.signature
            self._headings_before = list(snap_before.headings)
            self._alerts_before = list(snap_before.alerts)
        except Exception:
            self._sig_before = ""
            self._headings_before = []
            self._alerts_before = []

    async def end(self, settle_ms: int = 4000) -> Effects:
        """Wait for settle, diff state, return Effects."""
        await wait_for_settle(self._page, self, timeout_ms=settle_ms)

        url_after = self._page.url
        title_after = await self._page.title()
        url_changed = url_after != self._url_before

        # Re-snapshot to get new headings/alerts/signature
        sig_after = ""
        new_headings: list[str] = []
        new_alerts: list[str] = []
        try:
            snap_after = await take_snapshot(self._page)
            sig_after = snap_after.signature
            # new headings = those not in before set
            before_set = set(self._headings_before)
            new_headings = [h for h in snap_after.headings if h not in before_set]
            before_alerts_set = set(self._alerts_before)
            new_alerts = [a for a in snap_after.alerts if a not in before_alerts_set]
        except Exception:
            pass

        dom_changed = sig_after != self._sig_before and sig_after != ""

        return Effects(
            url_before=self._url_before,
            url_after=url_after,
            url_changed=url_changed,
            title_after=title_after,
            new_headings=new_headings,
            new_alerts=new_alerts,
            network=list(self._net),
            console_errors=list(self._console_errors),
            page_errors=list(self._page_errors),
            dialogs=list(self._dialogs),
            dom_changed=dom_changed,
        )

    def all_page_errors(self) -> list[str]:
        """All page-level JS errors recorded since the recorder was attached."""
        return list(self._all_page_errors)

    def all_console_errors(self) -> list[str]:
        """All console errors recorded since the recorder was attached."""
        return list(self._all_console)

    def inflight(self) -> int:
        return len(self._pending)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _is_same_origin(self, url: str) -> bool:
        if not self._origin:
            return True  # unknown origin — record everything
        parsed = urlparse(url)
        req_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
        return req_origin == self._origin


# ── Settle logic ──────────────────────────────────────────────────────────────────

_SETTLE_JS = """
() => {
  return new Promise((resolve) => {
    let networkQuiet = false;
    let domQuiet = false;
    let networkTimer = null;
    let domTimer = null;
    let resolved = false;

    function tryResolve() {
      if (!resolved && networkQuiet && domQuiet) {
        resolved = true;
        observer.disconnect();
        resolve();
      }
    }

    // DOM mutation observer
    const observer = new MutationObserver(() => {
      clearTimeout(domTimer);
      domQuiet = false;
      domTimer = setTimeout(() => {
        domQuiet = true;
        tryResolve();
      }, 250);
    });
    observer.observe(document.body || document.documentElement, {
      childList: true, subtree: true, attributes: true, characterData: true
    });

    // Start dom quiet timer immediately (no mutations yet = quiet)
    domTimer = setTimeout(() => {
      domQuiet = true;
      tryResolve();
    }, 250);

    // We rely on Playwright's waitForLoadState for network; resolve dom after 300ms quiet
    networkQuiet = true;  // let Python handle network settle
    tryResolve();
  });
}
"""


async def wait_for_settle(
    page: "Page",
    recorder: "EffectRecorder",
    timeout_ms: int = 4000,
) -> None:
    """Wait until: no in-flight fetch/xhr for 300 ms AND no DOM mutation for 250 ms, or timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000.0

    # A navigation may be in progress: let the new document reach DOMContentLoaded first.
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

    # Network quiet: nothing in flight for 300 ms (fetch/xhr/document, same-origin).
    await asyncio.sleep(0.05)
    quiet_since = loop.time() if recorder.inflight() == 0 else None
    while loop.time() < deadline:
        if recorder.inflight() == 0:
            if quiet_since is None:
                quiet_since = loop.time()
            if loop.time() - quiet_since >= 0.3:
                break
        else:
            quiet_since = None
        await asyncio.sleep(0.05)

    # Also wait for DOM quiet via injected MutationObserver
    remaining = max(0.1, deadline - asyncio.get_event_loop().time())
    try:
        await asyncio.wait_for(
            page.evaluate(_SETTLE_JS),
            timeout=remaining,
        )
    except (asyncio.TimeoutError, Exception):
        pass  # timeout is fine — best-effort settle
