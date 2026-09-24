"""argus/healing/similarity.py — Similo-style multi-attribute element matching.

Pure functions; no I/O, no async.

Public API:
    DEFAULT_WEIGHTS      dict[str, float]
    score(target, cand, weights) -> tuple[float, dict[str,float]]
    compatible(action, cand) -> bool
    rank(target, snap_elements, action, weights, top_k=5) -> list[Candidate]
    effective_weights(base, stability) -> dict[str, float]
    changed_attributes(old, new) -> dict[str, bool]

With the semantic-score extension requested by the caller:
    The `score` breakdown also contains keys "full_score" and "semantic_score".
    final = max(full, 0.9 * semantic)
"""
from __future__ import annotations

import math
import re
from typing import NamedTuple

from rapidfuzz import fuzz

from argus.models import BBox, ElementInfo, Fingerprint

# ── Weights ───────────────────────────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "testid": 3.0,
    "name": 2.5,
    "id": 1.5,
    "name_attr": 1.5,
    "role": 1.5,
    "label": 1.5,
    "context": 1.5,
    "text": 1.5,
    "tag": 1.0,
    "type": 1.0,
    "placeholder": 1.0,
    "href": 1.0,
    "neighbor_text": 1.0,
    "class": 0.5,
    "xpath": 0.5,
    "position": 0.5,
    "size": 0.3,
    "css": 0.3,
    "title": 0.5,
    "alt": 0.5,
}

# Human-visible attributes for semantic score
_SEMANTIC_ATTRS = frozenset(
    ["role", "name", "text", "label", "placeholder", "context", "neighbor_text"]
)

# ── Synonym groups ────────────────────────────────────────────────────────────────

_SYNONYM_GROUPS: list[frozenset[str]] = [
    frozenset({"log in", "login", "sign in", "signin"}),
    frozenset({"log out", "logout", "sign out", "signout"}),
    frozenset({"sign up", "register", "create account"}),
    frozenset({"next", "continue", "proceed"}),
    frozenset({"back", "previous"}),
    frozenset({"save", "save changes", "update", "apply"}),
    frozenset({"submit", "send", "confirm"}),
    frozenset({"delete", "remove"}),
    frozenset({"new", "create", "add"}),
    frozenset({"cancel", "close", "dismiss"}),
    frozenset({"search", "find", "filter"}),
    frozenset({"launch", "start", "go", "run"}),
    frozenset({"settings", "preferences"}),
    frozenset({"edit", "modify"}),
]

# Pre-build a lookup: normalized_word -> frozenset (its group)
_SYNONYM_MAP: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _term in _group:
        _SYNONYM_MAP[_term.lower()] = _group


# canonical token for every synonym phrase (word-boundary matched, longest phrase first)
_CANON: dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    _canon = sorted(_group, key=lambda t: (len(t), t))[0].replace(" ", "")
    for _term in _group:
        _CANON[_term] = _canon
_PHRASES = sorted(_CANON, key=len, reverse=True)
_PHRASE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _PHRASES) + r")\b")


def _clean(s: str) -> str:
    s = re.sub(r"[^\w\s%]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _canonical(s: str) -> str:
    """Map synonym words/phrases to one canonical token: 'Sign in' -> 'login', 'Continue' -> 'next'."""
    return _PHRASE_RE.sub(lambda m: _CANON[m.group(1)], s)


def _strip_trailing_ids(s: str) -> str:
    """Strip trailing numbers/ids from a string (e.g., 'item 42' -> 'item')."""
    return re.sub(r"\s+\d+$", "", s.strip())


def _blend(a: str, b: str) -> float:
    # token_set alone scores any subset as 1.0 ("Save" vs "Save draft"); blending with
    # token_sort keeps exact matches at 1.0 while penalising extra words.
    return 0.5 * fuzz.token_set_ratio(a, b) / 100.0 + 0.5 * fuzz.token_sort_ratio(a, b) / 100.0


def text_sim(a: str, b: str) -> float:
    """Fuzzy text similarity with synonym credit (capped at 0.85 when only synonyms agree)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    a = _strip_trailing_ids(_clean(a))
    b = _strip_trailing_ids(_clean(b))
    if a == b:
        return 1.0
    raw = _blend(a, b)
    ca, cb = _canonical(a), _canonical(b)
    syn = 0.85 * _blend(ca, cb) if (ca != a or cb != b) else 0.0
    return max(raw, syn)


# ── Class token normalization ─────────────────────────────────────────────────────

# Patterns for hash-like class tokens to drop
_HASH_CLASS_RE = re.compile(r"[0-9]")  # contains a digit AND len >= 5
_CSS_MODULE_SUFFIX_RE = re.compile(r"__[a-z0-9]{4,}$", re.IGNORECASE)


def _normalize_class_tokens(cls_str: str) -> frozenset[str]:
    """Tokenize class string, strip hash-like / CSS-module tokens, return frozenset."""
    tokens: set[str] = set()
    for tok in cls_str.split():
        # Drop tokens starting with css- or sc-
        if tok.startswith("css-") or tok.startswith("sc-"):
            continue
        # Strip CSS module suffixes like Button_primary__x7f2a -> button, primary
        clean = _CSS_MODULE_SUFFIX_RE.sub("", tok)
        # Split on underscores/hyphens for compound tokens
        parts = re.split(r"[_\-]", clean)
        for part in parts:
            part = part.lower()
            if not part:
                continue
            # Drop hash-like tokens: contain a digit AND len >= 5
            if len(part) >= 5 and _HASH_CLASS_RE.search(part):
                continue
            tokens.add(part)
    return frozenset(tokens)


def _class_sim(a: str, b: str) -> float:
    """Jaccard similarity of normalized class token sets."""
    ta = _normalize_class_tokens(a)
    tb = _normalize_class_tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


# ── BBox helpers ──────────────────────────────────────────────────────────────────

def _center(bbox: BBox) -> tuple[float, float]:
    return (bbox.x + bbox.w / 2, bbox.y + bbox.h / 2)


def _dist(a: BBox, b: BBox) -> float:
    ax, ay = _center(a)
    bx, by = _center(b)
    return math.hypot(ax - bx, ay - by)


def _area(bbox: BBox) -> float:
    return bbox.w * bbox.h


# ── Extract fingerprint attributes ────────────────────────────────────────────────

def _testid(attrs: dict[str, str]) -> str:
    """Return the first available test-id attribute value."""
    for key in ("data-testid", "data-test", "data-test-id", "data-cy", "data-qa"):
        val = attrs.get(key, "")
        if val:
            return val
    return ""


def _extract_fp_attrs(fp: Fingerprint) -> dict[str, str]:
    """Flatten all scorable attributes from a Fingerprint into a dict."""
    attrs = fp.attrs or {}
    return {
        "tag": fp.tag,
        "role": fp.role,
        "name": fp.name,
        "text": fp.text,
        "label": fp.label,
        "context": fp.context,
        "neighbor_text": fp.neighbor_text,
        "xpath": fp.xpath,
        "css": fp.css,
        "id": attrs.get("id", ""),
        "name_attr": attrs.get("name", ""),
        "testid": _testid(attrs),
        "type": attrs.get("type", ""),
        "placeholder": attrs.get("placeholder", ""),
        "href": attrs.get("href", ""),
        "class": attrs.get("class", ""),
        "title": attrs.get("title", ""),
        "alt": attrs.get("alt", ""),
    }


def _extract_ei_attrs(ei: ElementInfo) -> dict[str, str]:
    """Flatten all scorable attributes from an ElementInfo into a dict."""
    attrs = ei.attrs or {}
    return {
        "tag": ei.tag,
        "role": ei.role,
        "name": ei.name,
        "text": ei.text,
        "label": ei.label,
        "context": ei.context,
        "neighbor_text": ei.neighbor_text,
        "xpath": ei.xpath,
        "css": ei.css,
        "id": attrs.get("id", ""),
        "name_attr": attrs.get("name", ""),
        "testid": _testid(attrs),
        "type": attrs.get("type", ""),
        "placeholder": attrs.get("placeholder", ""),
        "href": attrs.get("href", ""),
        "class": attrs.get("class", ""),
        "title": attrs.get("title", ""),
        "alt": attrs.get("alt", ""),
    }


# ── Single-attribute similarity ───────────────────────────────────────────────────

_EXACT_ATTRS = frozenset(["id", "name_attr", "testid", "tag", "type"])
_ROLE_ATTRS = frozenset(["role"])
_TEXT_ATTRS = frozenset(["name", "text", "label", "placeholder", "context", "neighbor_text", "title", "alt"])
_CLASS_ATTRS = frozenset(["class"])
_PATH_ATTRS = frozenset(["xpath", "css"])
_HREF_ATTRS = frozenset(["href"])


def _attr_sim(attr: str, val_target: str, val_cand: str) -> float:
    """Compute similarity in [0,1] for a single attribute."""
    if attr in _EXACT_ATTRS:
        return 1.0 if val_target == val_cand and val_target != "" else 0.0
    if attr in _ROLE_ATTRS:
        if val_target == val_cand:
            return 1.0
        # button ≈ link gets 0.5
        if {val_target, val_cand} == {"button", "link"}:
            return 0.5
        return 0.0
    if attr in _TEXT_ATTRS:
        return text_sim(val_target, val_cand)
    if attr in _CLASS_ATTRS:
        return _class_sim(val_target, val_cand)
    if attr in _PATH_ATTRS:
        # Levenshtein normalized over path segments
        if not val_target and not val_cand:
            return 1.0
        if not val_target or not val_cand:
            return 0.0
        return fuzz.ratio(val_target, val_cand) / 100.0
    if attr in _HREF_ATTRS:
        return text_sim(val_target, val_cand)
    if attr == "position":
        return 0.0  # handled separately
    if attr == "size":
        return 0.0  # handled separately
    return 0.0


# ── Main scoring function ─────────────────────────────────────────────────────────

def score(
    target: Fingerprint,
    cand: ElementInfo,
    weights: dict[str, float],
) -> tuple[float, dict[str, float]]:
    """Weighted-mean similarity between *target* fingerprint and *cand* element.

    Only attributes **present in target** (non-empty) contribute. A missing attribute
    in cand scores 0 for that dimension. Returns (final_score, breakdown_dict).

    breakdown also contains "full_score" and "semantic_score".
    """
    fp_attrs = _extract_fp_attrs(target)
    ei_attrs = _extract_ei_attrs(cand)

    breakdown: dict[str, float] = {}
    total_weight = 0.0
    weighted_sum = 0.0
    semantic_weight = 0.0
    semantic_sum = 0.0

    for attr, w in weights.items():
        if attr in ("position", "size"):
            # Handled via bbox separately
            continue

        val_target = fp_attrs.get(attr, "")
        if not val_target:
            # Attribute not present in target — skip
            continue

        val_cand = ei_attrs.get(attr, "")
        sim = _attr_sim(attr, val_target, val_cand)
        breakdown[attr] = sim

        total_weight += w
        weighted_sum += w * sim

        if attr in _SEMANTIC_ATTRS:
            semantic_weight += w
            semantic_sum += w * sim

    # Position and size via bbox
    if target.bbox and cand.bbox:
        pos_w = weights.get("position", 0.5)
        sz_w = weights.get("size", 0.3)

        dist = _dist(target.bbox, cand.bbox)
        pos_sim = max(0.0, 1.0 - dist / 600.0)
        breakdown["position"] = pos_sim
        total_weight += pos_w
        weighted_sum += pos_w * pos_sim

        t_area = _area(target.bbox)
        c_area = _area(cand.bbox)
        if t_area > 0 and c_area > 0:
            sz_sim = min(t_area, c_area) / max(t_area, c_area)
        else:
            sz_sim = 1.0 if t_area == c_area else 0.0
        breakdown["size"] = sz_sim
        total_weight += sz_w
        weighted_sum += sz_w * sz_sim

    full_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    semantic_score = semantic_sum / semantic_weight if semantic_weight > 0 else 0.0

    # final = max(full, 0.9 * semantic) — heavy refactors with unambiguous meaning still heal
    final = max(full_score, 0.9 * semantic_score)

    breakdown["full_score"] = full_score
    breakdown["semantic_score"] = semantic_score

    return final, breakdown


# ── Compatibility check ───────────────────────────────────────────────────────────

def compatible(action: str, cand: ElementInfo) -> bool:
    """Check whether *action* is compatible with *cand*'s role/state."""
    if action in ("goto", "press", "wait"):
        return True
    if action == "fill":
        return cand.editable
    if action == "select":
        return cand.role in ("combobox", "listbox") or cand.tag == "select"
    if action in ("check", "uncheck"):
        return cand.role in ("checkbox", "radio", "switch")
    if action in ("click", "hover"):
        return cand.visible and cand.enabled and cand.interactive
    return True


# ── Candidate namedtuple ──────────────────────────────────────────────────────────

class Candidate(NamedTuple):
    element: ElementInfo
    score: float
    breakdown: dict[str, float]


# ── Rank function ─────────────────────────────────────────────────────────────────

def rank(
    target: Fingerprint,
    snap_elements: list[ElementInfo],
    action: str,
    weights: dict[str, float],
    top_k: int = 5,
) -> list[Candidate]:
    """Return top-k compatible, interactive candidates sorted by score descending."""
    candidates: list[Candidate] = []
    for el in snap_elements:
        if not el.interactive:
            continue
        if not compatible(action, el):
            continue
        s, bd = score(target, el, weights)
        candidates.append(Candidate(element=el, score=s, breakdown=bd))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


# ── Effective weights (stability-adjusted) ────────────────────────────────────────

def effective_weights(
    base: dict[str, float],
    stability: dict[str, float],
) -> dict[str, float]:
    """Scale weights by (0.35 + 0.65 * stability) so unstable attrs count less."""
    return {
        attr: w * (0.35 + 0.65 * stability.get(attr, 1.0))
        for attr, w in base.items()
    }


# ── Changed attributes ────────────────────────────────────────────────────────────

def changed_attributes(
    old: Fingerprint,
    new: Fingerprint,
) -> dict[str, bool]:
    """Return a dict attr->bool (True = unchanged) comparing two fingerprints."""
    old_attrs = _extract_fp_attrs(old)
    new_attrs = _extract_fp_attrs(new)
    result: dict[str, bool] = {}
    all_keys = set(old_attrs.keys()) | set(new_attrs.keys())
    for k in all_keys:
        result[k] = old_attrs.get(k, "") == new_attrs.get(k, "")
    # bbox position/size
    if old.bbox and new.bbox:
        result["position"] = (
            abs(old.bbox.x - new.bbox.x) < 5
            and abs(old.bbox.y - new.bbox.y) < 5
        )
        result["size"] = (
            abs(old.bbox.w - new.bbox.w) < 5
            and abs(old.bbox.h - new.bbox.h) < 5
        )
    return result
