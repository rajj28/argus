"""UI helper — resolves ids, classes, texts, tags for each version + chaos seed."""
from __future__ import annotations
import hashlib
import random
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from demo_app.state import AppState

# ---------------------------------------------------------------------------
# Version profiles
# ---------------------------------------------------------------------------

VERSION_IDS: dict[str, dict[str, str]] = {
    "1.1": {
        "login-email": "auth-email-input",
        "login-password": "auth-password-input",
        "login-submit": "auth-sign-in-btn",
        "login-error": "auth-error-banner",
    },
}

VERSION_TEXTS: dict[str, dict[str, str]] = {
    "1.1": {
        "Log in": "Sign in",
        "New mission": "Create mission",
        "Next": "Continue",
        "Launch mission": "Launch",
        "Save settings": "Save changes",
    },
}
# 1.2 and 1.3 are cumulative and include 1.1 text changes
for _v in ("1.2", "1.3"):
    VERSION_TEXTS[_v] = dict(VERSION_TEXTS["1.1"])

# Chaos text synonym table
TEXT_SYNONYMS: dict[str, list[str]] = {
    "Next": ["Next", "Continue", "Proceed"],
    "Continue": ["Continue", "Next", "Proceed"],
    "Save settings": ["Save settings", "Save changes", "Apply"],
    "Save changes": ["Save changes", "Save settings", "Apply"],
    "Log in": ["Log in", "Sign in"],
    "Sign in": ["Sign in", "Log in"],
    "Launch mission": ["Launch mission", "Start mission", "Launch"],
    "Launch": ["Launch", "Start mission", "Launch mission"],
    "New mission": ["New mission", "Create mission", "Add mission"],
    "Create mission": ["Create mission", "New mission", "Add mission"],
    "Back": ["Back", "Previous"],
    "Previous": ["Previous", "Back"],
    "View": ["View", "Open", "Details"],
    "Open": ["Open", "View", "Details"],
}


def _det_rng(seed: int, key: str) -> random.Random:
    """Deterministic RNG per (seed, key)."""
    combined = f"{seed}:{key}"
    seed_val = int(hashlib.md5(combined.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed_val)


class UIHelper:
    """Template-callable UI helper."""

    def __init__(self, state: "AppState") -> None:
        self._state = state

    def _has_mutation(self, name: str) -> bool:
        return bool(
            self._state.chaos_seed is not None
            and name in self._state.chaos_mutations
        )

    def id(self, canonical: str) -> str:
        """Return the element id for current version/chaos."""
        ver = self._state.version
        result = canonical
        for v in ("1.1", "1.2", "1.3"):
            if ver >= v and canonical in VERSION_IDS.get(v, {}):
                result = VERSION_IDS[v][canonical]
                break
        if self._has_mutation("ids") and result:
            rng = _det_rng(self._state.chaos_seed, f"id:{canonical}")
            suffix = rng.randint(10000, 99999)
            result = f"chaos-{suffix}"
        return result

    def cls(self, canonical: str) -> str:
        """Return CSS class string, possibly hashed for chaos."""
        if self._has_mutation("classes"):
            parts = canonical.split()
            hashed = []
            for p in parts:
                rng = _det_rng(self._state.chaos_seed, f"cls:{p}")
                suffix = rng.randint(0, 0xFFFF)
                hashed.append(f"{p.replace('-', '_').title().replace('_', '')}_{suffix:04x}")
            return " ".join(hashed)
        return canonical

    def text(self, canonical: str) -> str:
        """Return button/label text for current version/chaos."""
        ver = self._state.version
        result = canonical
        for v in ("1.1", "1.2", "1.3"):
            if ver >= v and canonical in VERSION_TEXTS.get(v, {}):
                result = VERSION_TEXTS[v][canonical]
                break
        if self._has_mutation("text") and result in TEXT_SYNONYMS:
            rng = _det_rng(self._state.chaos_seed, f"text:{result}")
            result = rng.choice(TEXT_SYNONYMS[result])
        return result

    def testid(self, canonical: str) -> str:
        """Return data-testid value, or empty string if chaos drops it."""
        if self._has_mutation("testids"):
            return ""
        return canonical

    def tag(self, canonical: str) -> str:
        """Return the tag type for chaos/version: 'button', 'a_role_button', 'div_role_button'."""
        if self._has_mutation("tags") and canonical == "button":
            variants = ["button", "a_role_button", "div_role_button"]
            rng = _det_rng(self._state.chaos_seed, f"tag:{canonical}")
            return rng.choice(variants)
        if self._state.version >= "1.1" and canonical == "button":
            return "a_role_button"
        return canonical

    def order(self, items: list) -> list:
        """Return items in chaos order (if mutation active)."""
        if not self._has_mutation("order") or len(items) <= 1:
            return items
        key = f"order:{'|'.join(str(x) for x in items)}"
        rng = _det_rng(self._state.chaos_seed, key)
        shuffled = list(items)
        rng.shuffle(shuffled)
        return shuffled

    def nav_order(self, items: list) -> list:
        """Nav items ordered per version (v1.1: sidebar reorder) + chaos."""
        if self._state.version >= "1.1":
            priority = {"Missions": 0, "Dashboard": 1, "Flight logs": 2, "Analytics": 2, "Settings": 3}
            items = sorted(items, key=lambda x: priority.get(x.get("label", ""), 99))
        if self._has_mutation("order"):
            rng = _det_rng(self._state.chaos_seed, "nav_order")
            shuffled = list(items)
            rng.shuffle(shuffled)
            return shuffled
        return items

    def layout(self) -> str:
        """Return 'sidebar' or 'topbar'."""
        if self._has_mutation("layout"):
            rng = _det_rng(self._state.chaos_seed, "layout")
            return rng.choice(["sidebar", "topbar"])
        if self._state.version >= "1.1":
            return "sidebar"
        return "topbar"

    def wrap_start(self) -> str:
        """Opening wrapper divs for chaos wrappers mutation."""
        if self._has_mutation("wrappers"):
            rng = _det_rng(self._state.chaos_seed, "wrappers_count")
            count = rng.randint(1, 2)
            return '<div class="chaos-wrap">' * count
        return ""

    def wrap_end(self) -> str:
        """Closing wrapper divs for chaos wrappers mutation."""
        if self._has_mutation("wrappers"):
            rng = _det_rng(self._state.chaos_seed, "wrappers_count")
            count = rng.randint(1, 2)
            return "</div>" * count
        return ""

    def kpi_order(self, cards: list) -> list:
        """Reorder KPI cards if chaos order mutation active."""
        return self.order(cards)
