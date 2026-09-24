"""argus/browser/snapshot.py — DOM snapshot helpers.

Public API:
    take_snapshot(page, max_elements=350) -> PageSnapshot
    structural_signature(snap) -> str
    element_handle(page, ref) -> ElementHandle | None
    compact_for_llm(snap, limit=120, only_interactive=False) -> str
    normalize_path(url) -> str
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Optional

from playwright.async_api import ElementHandle, Page

from argus.models import ElementInfo, PageSnapshot

# ── JS distiller source ──────────────────────────────────────────────────────────

_JS_PATH = Path(__file__).parent / "snapshot.js"
_SNAPSHOT_JS: str = _JS_PATH.read_text(encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────────

async def take_snapshot(page: Page, max_elements: int = 350) -> PageSnapshot:
    """Inject snapshot.js and return a fully-populated PageSnapshot with signature."""
    raw = await page.evaluate(_SNAPSHOT_JS, {"maxElements": max_elements})

    elements = []
    for e in raw.get("elements", []):
        bbox_raw = e.get("bbox")
        from argus.models import BBox
        bbox = BBox(**bbox_raw) if bbox_raw else None
        checked = e.get("checked")  # may be None/undefined
        elements.append(ElementInfo(
            ref=e["ref"],
            tag=e["tag"],
            role=e.get("role", ""),
            name=e.get("name", ""),
            text=e.get("text", ""),
            attrs=e.get("attrs", {}),
            label=e.get("label", ""),
            context=e.get("context", ""),
            neighbor_text=e.get("neighbor_text", ""),
            xpath=e.get("xpath", ""),
            css=e.get("css", ""),
            bbox=bbox,
            visible=e.get("visible", True),
            enabled=e.get("enabled", True),
            editable=e.get("editable", False),
            checked=checked,
            interactive=e.get("interactive", True),
            in_dialog=e.get("in_dialog", False),
        ))

    snap = PageSnapshot(
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        elements=elements,
        headings=raw.get("headings", []),
        alerts=raw.get("alerts", []),
        text_digest=raw.get("text_digest", ""),
        viewport=raw.get("viewport", {"w": 1280, "h": 800}),
    )
    snap.signature = structural_signature(snap)
    return snap


def structural_signature(snap: PageSnapshot) -> str:
    """SHA-1 of sorted (role, normalized name) of interactive elements + normalized url path.

    Ignores counts of repeated rows so layout changes don't change the signature.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for e in snap.elements:
        if not e.interactive:
            continue
        role = e.role.strip().lower()
        name = _normalize_name(e.name or e.text)
        key = (role, name)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    parts = sorted(f"{r}|{n}" for r, n in pairs)
    parts.append(normalize_path(snap.url))
    digest = "\n".join(parts).encode("utf-8")
    return hashlib.sha1(digest).hexdigest()


async def element_handle(page: Page, ref: str) -> Optional[ElementHandle]:
    """Retrieve the live ElementHandle for ``ref`` (e.g. "e12") stored in window.__argus."""
    try:
        idx = int(ref[1:])  # "e12" -> 12
        handle = await page.evaluate_handle(
            f"() => window.__argus && window.__argus.refs && window.__argus.refs[{idx}]"
        )
        # evaluate_handle returns JSHandle; check it's not null
        is_null = await page.evaluate(
            f"() => !window.__argus || !window.__argus.refs || window.__argus.refs[{idx}] == null"
        )
        if is_null:
            return None
        # Convert JSHandle -> ElementHandle
        el = handle.as_element()
        return el
    except Exception:
        return None


def compact_for_llm(
    snap: PageSnapshot,
    limit: int = 120,
    only_interactive: bool = False,
) -> str:
    """One line per element:  [e12] button "Add drone" (in: Fleet) {id=add, disabled}

    Plus headings and alerts header. Target: ~15 tokens per element.
    """
    lines: list[str] = []

    # Header: page info
    lines.append(f"# {snap.title} | {normalize_path(snap.url)}")

    # Headings
    if snap.headings:
        lines.append("## Headings: " + " / ".join(snap.headings[:5]))

    # Alerts
    if snap.alerts:
        lines.append("## Alerts: " + " | ".join(snap.alerts[:3]))

    # Elements
    count = 0
    for e in snap.elements:
        if only_interactive and not e.interactive:
            continue
        if count >= limit:
            break
        count += 1

        label = e.name or e.text or ""
        ctx = f" (in: {e.context[:40]})" if e.context else ""

        # Compact attrs: only id, type, data-testid, disabled
        useful_attrs: list[str] = []
        if "id" in e.attrs:
            useful_attrs.append(f"id={e.attrs['id']}")
        if "type" in e.attrs and e.attrs["type"] not in ("text", ""):
            useful_attrs.append(f"type={e.attrs['type']}")
        for testid_key in ("data-testid", "data-test", "data-test-id", "data-cy", "data-qa"):
            if testid_key in e.attrs:
                useful_attrs.append(f"testid={e.attrs[testid_key]}")
                break
        if not e.enabled:
            useful_attrs.append("disabled")
        if "required" in e.attrs:
            useful_attrs.append("required")
        if e.editable and not e.attrs.get("value"):
            useful_attrs.append("empty")
        if not e.interactive:
            useful_attrs.append("anchor")

        attrs_str = " {" + ", ".join(useful_attrs) + "}" if useful_attrs else ""
        lines.append(f"[{e.ref}] {e.role or e.tag} \"{label[:50]}\"{ctx}{attrs_str}")

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────────

# Match numeric segments, prefixed ids (m5, d-12, usr_42), UUIDs, hex >= 8, or long mixed hash-like slugs
_ID_SEGMENT_RE = re.compile(
    r"^(?:\d+|[A-Za-z]{1,4}[-_]?\d+[A-Za-z]{0,2}|[0-9a-fA-F]{8,}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|(?=[A-Za-z0-9_-]{16,}$)(?=.*\d)[A-Za-z0-9_-]+)$"
)


def normalize_path(url: str) -> str:
    """Strip origin, replace numeric/uuid/hex(>=8) segments with ':id', drop query+hash."""
    # Remove protocol+host
    path = re.sub(r"^https?://[^/]+", "", url)
    # Drop query and hash
    path = re.split(r"[?#]", path)[0]
    # Normalize each segment
    segments = path.split("/")
    normalized = [":id" if _ID_SEGMENT_RE.match(seg) else seg for seg in segments]
    return "/".join(normalized) or "/"


def _normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for signature comparison."""
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    # strip trailing numbers/ids (e.g. "item 42" -> "item")
    name = re.sub(r"\s+\d+$", "", name)
    return name
