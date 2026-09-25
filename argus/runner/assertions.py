"""Deterministic assertion checks (the business oracles). Only `llm_check` may call a model."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from argus.browser.snapshot import normalize_path, take_snapshot
from argus.healing.similarity import rank
from argus.llm import prompts as P
from argus.models import Assertion, Fingerprint, NetCall

_VAR = re.compile(r"\$\{(vars\.)?([a-zA-Z0-9_\.]+)\}")


def interpolate(value: Any, vars: dict[str, str]) -> Any:
    """Replace ${vars.name} / ${name} placeholders; unknown names are left untouched."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: str(vars.get(m.group(2), m.group(0))), value)
    if isinstance(value, dict):
        return {k: interpolate(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, vars) for v in value]
    return value


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def status_class(status: int) -> str:
    return "failed" if status == 0 else f"{status // 100}xx"


def path_matches(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    pat = "^" + re.escape(expected).replace(re.escape(":id"), "[^/]+") + "$"
    return re.match(pat, actual) is not None


def network_match(calls: list[NetCall], method: str, path: str, status_cls: str = "any") -> list[NetCall]:
    out = [c for c in calls if c.method.upper() == (method or "GET").upper() and path_matches(c.path, path)]
    if status_cls and status_cls != "any":
        out = [c for c in out if status_class(c.status) == status_cls]
    return out


# ── sum_equals: arithmetic invariant (total == sum of line items), currency-generic ──────────

# Amounts with a currency symbol/code (any decimals), or bare numbers that are unambiguously
# money (thousands separators or exactly two decimals) so quantities/dates are not picked up.
_AMOUNT_RE = re.compile(
    r"(?:[$€£¥₹]|(?:INR|USD|EUR|GBP|Rs)\.?\s)\s*(-?\d[\d,]*(?:\.\d+)?)"
    r"|(?<![\w.,])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d{2})(?![\w.,])"
)

_SUM_EQUALS_JS = """
([label, itemsSel]) => {
  const txt = el => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
  const ROWS = "tr, li, [role=row]";
  let deepest = null, deepestLen = Infinity;
  for (const el of document.querySelectorAll("body *")) {
    const t = txt(el);
    if (t.includes(label) && t.length < deepestLen) { deepest = el; deepestLen = t.length; }
  }
  if (!deepest) return null;
  let container = deepest;
  while (container && container !== document.body && container.parentElement) {
    const own = itemsSel
      ? container.querySelectorAll(itemsSel).length
      : Array.from(container.querySelectorAll(ROWS)).filter(r => /\\d/.test(txt(r))).length;
    if (own >= 2) break;
    container = container.parentElement;
  }
  if (!container) container = document.body;
  let items;
  if (itemsSel) {
    items = Array.from(container.querySelectorAll(itemsSel)).map(txt);
  } else {
    items = Array.from(container.querySelectorAll(ROWS)).map(txt).filter(t => t && /\\d/.test(t));
    if (items.length < 2) {
      items = Array.from(container.children).map(txt).filter(t => t && /\\d/.test(t));
    }
  }
  const row = deepest.closest(ROWS) || deepest;
  return { total_line: txt(row), items };
}
"""


def parse_amounts(text: str) -> list[float]:
    """All currency/number amounts in `text` (INR/USD/EUR symbols, thousands commas)."""
    out: list[float] = []
    for m in _AMOUNT_RE.finditer(text or ""):
        s = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out


def sum_equals_from_texts(total_label: str, total_line: str, item_lines: list[str],
                          tolerance: float = 0.005) -> tuple[bool, str]:
    """Pure check: last amount on the total line == sum of last amounts of the item lines."""
    totals = parse_amounts(total_line)
    if not totals:
        return False, f"no amount found on the '{total_label}' line"
    total = totals[-1]
    items: list[float] = []
    for line in item_lines:
        if total_label and total_label.lower() in line.lower():
            continue
        amts = parse_amounts(line)
        if amts:
            items.append(amts[-1])
    if not items:
        return False, f"no line-item amounts found near '{total_label}'"
    s = sum(items)
    ok = abs(s - total) <= tolerance
    detail = (f"sum of {len(items)} line items = {s:.2f}; '{total_label}' = {total:.2f}"
              + ("" if ok else f" (off by {s - total:+.2f})"))
    return ok, detail


async def _body_text(page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def check(a: Assertion, page: Any, ctx: Any, calls: list[NetCall], page_errors: list[str],
                wait_ms: int = 2500) -> tuple[bool, str]:
    """Return (holds, detail). `calls` = network calls observed during this test."""
    p = interpolate(a.params, getattr(ctx, "vars", {}))
    kind = a.kind
    if kind in ("text_visible", "text_absent"):
        want = _norm(str(p.get("text", "")))
        deadline = wait_ms if kind == "text_visible" else 400
        waited = 0
        while True:
            present = want in _norm(await _body_text(page))
            if kind == "text_visible" and present:
                return True, f'text "{p.get("text")}" is visible'
            if waited >= deadline:
                break
            await asyncio.sleep(0.25)
            waited += 250
        if kind == "text_absent":
            return (not present), f'text "{p.get("text")}" {"is present" if present else "is absent"}'
        return False, f'expected text "{p.get("text")}" not found on {normalize_path(page.url)}'
    if kind == "url_matches":
        path = normalize_path(page.url)
        full = page.url.split("://", 1)[-1]
        ok = bool(re.search(str(p.get("pattern", "")), path) or re.search(str(p.get("pattern", "")), full))
        return ok, f"url {path} {'matches' if ok else 'does not match'} /{p.get('pattern')}/"
    if kind in ("network_called", "network_absent"):
        hits = network_match(calls, str(p.get("method", "GET")), str(p.get("path", "")),
                             str(p.get("status_class", "any")))
        desc = f'{p.get("method")} {p.get("path")} [{p.get("status_class", "any")}]'
        seen = ", ".join(f"{c.method} {c.path} {c.status}" for c in calls[-6:]) or "none"
        if kind == "network_called":
            return bool(hits), f"{desc} {'observed' if hits else 'NOT observed'} (recent calls: {seen})"
        return (not hits), f"{desc} {'unexpectedly observed' if hits else 'absent as expected'}"
    if kind == "no_page_errors":
        return (not page_errors), ("no uncaught errors" if not page_errors else f"uncaught: {page_errors[0][:160]}")
    if kind in ("element_visible", "element_state", "value_equals"):
        fp = Fingerprint(**p["fingerprint"]) if isinstance(p.get("fingerprint"), dict) else None
        if fp is None:
            return False, "assertion has no fingerprint"
        snap = await take_snapshot(page)
        weights = ctx.memory_weights() if hasattr(ctx, "memory_weights") else None
        ranked = rank(fp, snap.elements, "goto", weights or {}, top_k=2) if weights else \
            rank(fp, snap.elements, "goto", _default_weights(), top_k=2)
        if not ranked:
            return False, f"{fp.describe()} not found"
        el, score = (ranked[0][0], ranked[0][1]) if isinstance(ranked[0], (tuple, list)) else \
            (ranked[0].element, ranked[0].score)
        if score < 0.6:
            return False, f"{fp.describe()} not found (best {score:.2f})"
        if kind == "element_visible":
            return True, f"{fp.describe()} visible"
        if kind == "value_equals":
            val = el.attrs.get("value", "")
            return val == str(p.get("value")), f'{fp.describe()} value "{val}" (expected "{p.get("value")}")'
        problems = []
        if "enabled" in p and bool(p["enabled"]) != el.enabled:
            problems.append(f"enabled={el.enabled} (expected {p['enabled']})")
        if "checked" in p and el.checked is not None and bool(p["checked"]) != el.checked:
            problems.append(f"checked={el.checked} (expected {p['checked']})")
        return (not problems), f"{fp.describe()}: " + ("; ".join(problems) if problems else "state as expected")
    if kind == "sum_equals":
        total_label = re.sub(r"\s+", " ", str(p.get("total_label", ""))).strip()
        if not total_label:
            return False, "sum_equals requires params.total_label"
        items_sel = str(p.get("items_selector_text") or "").strip() or None
        last: tuple[bool, str] = (False, f"'{total_label}' not found on {normalize_path(page.url)}")
        waited, deadline = 0, wait_ms
        while True:
            try:
                data = await page.evaluate(_SUM_EQUALS_JS, [total_label, items_sel])
            except Exception:
                data = None
            if data:
                last = sum_equals_from_texts(total_label, str(data.get("total_line", "")),
                                             [str(i) for i in (data.get("items") or [])])
                if last[0]:
                    return last
            if waited >= deadline:
                return last
            await asyncio.sleep(0.25)
            waited += 250
    if kind == "llm_check":
        llm = getattr(ctx, "llm", None)
        if llm is None:
            return False, "llm_check skipped: no LLM"
        snap = await take_snapshot(page)
        user = P.LLM_CHECK_USER.format(question=p.get("question", ""), url=snap.url, title=snap.title,
                                       headings=snap.headings, alerts=snap.alerts, text=snap.text_digest[:1500])
        try:
            data = await llm.json(tier="fast", purpose="assert", system=P.LLM_CHECK_SYSTEM, user=user,
                                  max_tokens=120)
        except Exception as exc:
            return False, f"llm_check unavailable ({type(exc).__name__})"
        return bool(data.get("holds")), f"LLM: {data.get('reason', '')}"
    return False, f"unknown assertion kind {kind}"


def _default_weights() -> dict[str, float]:
    from argus.healing.similarity import DEFAULT_WEIGHTS
    return dict(DEFAULT_WEIGHTS)
