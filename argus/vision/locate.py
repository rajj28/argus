"""Vision locate cascade: cached -> template -> multiscale -> VLM set-of-marks.

Each stage returns ``(x, y, tier, confidence)`` in **page coordinates** or
``None``. `locate` runs the cascade in order; the VLM stage (V3) is used only
when an LLM client is provided, since it costs a call.
"""
from __future__ import annotations

import base64
import io
from typing import Any, Optional

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageDraw
from playwright.async_api import Page

from argus.vision.marks import VisualMark

_TEMPLATE_MIN_SCORE = 0.80
_TEMPLATE_MIN_MARGIN = 0.05
_PEAK_RADIUS = 20                     # px around a peak that must stay clear of rivals
_PHASH_DIST_LIMIT = 8                 # max phash hamming distance for V0 acceptance
_PHASH_SIZE = 16

SCALES: tuple[float, ...] = (0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25)

_VLM_MAX_CANDIDATES = 12
_VLM_MIN_CONFIDENCE = 0.4

_VLM_SYSTEM = (
    "You are a UI perception model. You receive a numbered screenshot of a canvas "
    "or map UI with candidate points drawn on it. Return ONLY a JSON object: "
    '{"mark": <integer id or null>, "confidence": <float 0..1>}.'
)
_VLM_USER = (
    'Find the marker that best matches the hint: "{hint}". '
    "Nearby text labels: {labels}. "
    "Candidate ids are the numbers drawn on the screenshot. "
    "Return the id of the best match (or null if none matches) with your confidence."
)

_hash_cache: dict[str, imagehash.ImageHash] = {}


async def locate(page: Page, mark: VisualMark, llm: Any = None) -> Optional[dict]:
    """Run V0..V3 on the live page; returns {x, y, tier, confidence, method} or None.

    ``x``/``y`` are page coordinates, ready to feed a Playwright click. V3 is never
    reached without an ``llm``.
    """
    stages = (("cached", 0), ("template", 1), ("multiscale", 2))
    for method, _ in stages:
        res = await _run_stage(method, page, mark)
        if res is not None:
            x, y, found_tier, confidence = res
            return {"x": x, "y": y, "tier": found_tier, "confidence": confidence, "method": method}
    if llm is not None:
        res = await vlm(page, mark, llm)
        if res is not None:
            x, y, found_tier, confidence = res
            return {"x": x, "y": y, "tier": found_tier, "confidence": confidence, "method": "vlm"}
    return None


async def _run_stage(method: str, page: Page, mark: VisualMark):
    if method == "cached":
        return await cached(page, mark)
    if method == "template":
        return await template(page, mark)
    return await multiscale(page, mark)


# -- V0: cached center, accepted by perceptual hash -----------------------------------------------

async def cached(page: Page, mark: VisualMark) -> Optional[tuple[float, float, int, float]]:
    """V0: canvas bbox * center_norm; accepted if the crop there has phash distance <= 8."""
    arr, bbox = await _canvas_bgr(page, mark.canvas_selector)
    if arr is None or bbox is None:
        return None
    w, h = bbox["width"], bbox["height"]
    cx, cy = mark.center_norm[0] * w, mark.center_norm[1] * h
    crop = _crop_bgr(arr, cx, cy, max(16, mark.crop_size))
    dist = _phash_distance(crop, mark.phash)
    if dist > _PHASH_DIST_LIMIT:
        return None
    return bbox["x"] + cx, bbox["y"] + cy, 0, round(max(0.0, 1.0 - dist / _PHASH_DIST_LIMIT), 3)


# -- V1: exact-scale template match ----------------------------------------------------------------

async def template(page: Page, mark: VisualMark) -> Optional[tuple[float, float, int, float]]:
    """V1: cv2.matchTemplate of the stored crop over the current canvas screenshot."""
    arr, bbox = await _canvas_bgr(page, mark.canvas_selector)
    if arr is None or bbox is None:
        return None
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tpl = _template_gray(mark)
    if tpl is None or _too_large(tpl, gray):
        return None
    score, second, (y, x) = _best_match(gray, tpl)
    if score < _TEMPLATE_MIN_SCORE or (score - second) < _TEMPLATE_MIN_MARGIN:
        return None
    return bbox["x"] + x + tpl.shape[1] / 2, bbox["y"] + y + tpl.shape[0] / 2, 1, round(score, 3)


# -- V2: multiscale template match (zoomed maps) ---------------------------------------------------

async def multiscale(page: Page, mark: VisualMark) -> Optional[tuple[float, float, int, float]]:
    """V2: repeat V1 matching at scales 0.8..1.25; return the strongest accepted match."""
    arr, bbox = await _canvas_bgr(page, mark.canvas_selector)
    if arr is None or bbox is None:
        return None
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tpl = _template_gray(mark)
    if tpl is None:
        return None
    best: Optional[tuple[float, float, float, float]] = None
    for scale in SCALES:
        if scale == 1.0:
            t = tpl
        else:
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            t = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=interp)
        if _too_large(t, gray):
            continue
        score, second, (y, x) = _best_match(gray, t)
        if score < _TEMPLATE_MIN_SCORE or (score - second) < _TEMPLATE_MIN_MARGIN:
            continue
        cx, cy = x + t.shape[1] / 2, y + t.shape[0] / 2
        if best is None or score > best[0]:
            best = (score, cx, cy, scale)
    if best is None:
        return None
    score, cx, cy, _ = best
    return bbox["x"] + cx, bbox["y"] + cy, 2, round(score, 3)


# -- V3: VLM picks among numbered set-of-marks candidates ------------------------------------------

async def vlm(page: Page, mark: VisualMark, llm: Any) -> Optional[tuple[float, float, int, float]]:
    """V3: draw numbered candidates, ask the vision tier to pick one, map id -> coordinate."""
    arr, bbox = await _canvas_bgr(page, mark.canvas_selector)
    if arr is None or bbox is None:
        return None
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tpl = _template_gray(mark)
    if tpl is None or _too_large(tpl, gray):
        return None
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)

    candidates: list[tuple[float, float, float]] = []
    for _, y, x in _peaks(res, k=_VLM_MAX_CANDIDATES, radius=_PEAK_RADIUS):
        candidates.append((0.0, x + tpl.shape[1] / 2, y + tpl.shape[0] / 2))
    if len(candidates) < _VLM_MAX_CANDIDATES:          # pad with a coarse 4x4 grid
        for gx, gy in _coarse_grid(gray.shape[1], gray.shape[0]):
            if len(candidates) >= _VLM_MAX_CANDIDATES:
                break
            if any((c[1] - gx) ** 2 + (c[2] - gy) ** 2 <= _PEAK_RADIUS ** 2 for c in candidates):
                continue
            candidates.append((0.0, gx, gy))
    candidates = candidates[:_VLM_MAX_CANDIDATES]

    numbered = _draw_candidates(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB), candidates)
    jpeg = _jpg_bytes(numbered)
    user = _VLM_USER.format(hint=mark.hint, labels=", ".join(mark.nearby_labels) or "none")
    try:
        data = await llm.json(tier="vision", purpose="vision", system=_VLM_SYSTEM, user=user, images=[jpeg])
    except Exception:
        return None
    try:
        idx, conf = int(data.get("mark") or 0), float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    if not 1 <= idx <= len(candidates) or conf < _VLM_MIN_CONFIDENCE:
        return None
    _, cx, cy = candidates[idx - 1]
    return bbox["x"] + cx, bbox["y"] + cy, 3, round(min(1.0, conf), 3)


# -- image helpers ----------------------------------------------------------------------------------

async def _canvas_bgr(page: Page, selector: str) -> tuple[Optional[np.ndarray], Optional[dict]]:
    """Canvas element screenshot as an OpenCV BGR array plus its page bounding box.

    Uses `query_selector` (immediate) so a missing canvas yields (None, None)
    instead of waiting for a 30s locator timeout.
    """
    handle = await page.query_selector(selector)
    if handle is None:
        return None, None
    bbox = await handle.bounding_box()
    if bbox is None:
        return None, None
    raw = await handle.screenshot()
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return arr, bbox


def _template_gray(mark: VisualMark) -> Optional[np.ndarray]:
    """Stored crop decoded to a float32 grayscale template."""
    try:
        raw = base64.b64decode(mark.crop_png_b64)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    except Exception:
        return None


def _crop_bgr(arr: np.ndarray, cx: float, cy: float, size: int) -> np.ndarray:
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = _clamped_box(cx, cy, size, w, h)
    return arr[y0:y1, x0:x1]


def _clamped_box(cx: float, cy: float, size: int, w: int, h: int) -> tuple[int, int, int, int]:
    half = size // 2
    x0 = max(0, int(round(cx - half)))
    y0 = max(0, int(round(cy - half)))
    return x0, y0, min(w, x0 + size), min(h, y0 + size)


def _best_match(gray: np.ndarray, tpl: np.ndarray) -> tuple[float, float, tuple[int, int]]:
    """(best score, best score outside a 20px radius, (y, x) of the best match)."""
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    loc = np.unravel_index(int(np.argmax(res)), res.shape)
    score = float(res[loc])
    return score, _second_peak(res, loc), (int(loc[0]), int(loc[1]))


def _second_peak(res: np.ndarray, loc: tuple[int, int]) -> float:
    m = _blank_radius(res, loc[0], loc[1], _PEAK_RADIUS)
    if m.size == 0:
        return 0.0
    return float(m.max())


def _blank_radius(res: np.ndarray, y: int, x: int, radius: int) -> np.ndarray:
    h, w = res.shape
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    out = res.copy()
    out[y0:y1, x0:x1] = -2.0
    return out


def _peaks(res: np.ndarray, k: int, radius: int) -> list[tuple[float, int, int]]:
    """Top non-overlapping local maxima as (score, y, x), descending by score."""
    m = res.copy()
    out: list[tuple[float, int, int]] = []
    for _ in range(k):
        if m.size == 0:
            break
        idx = np.unravel_index(int(np.argmax(m)), m.shape)
        score = float(m[idx])
        if score <= 0.0:
            break
        out.append((score, int(idx[0]), int(idx[1])))
        m = _blank_radius(m, idx[0], idx[1], radius)
    return out


def _coarse_grid(w: int, h: int) -> list[tuple[float, float]]:
    """16 evenly spaced points (a 4x4 grid spanning the screenshot)."""
    return [((i + 0.5) * w / 4, (j + 0.5) * h / 4) for i in range(4) for j in range(4)]


def _draw_candidates(img_rgb: np.ndarray, candidates: list[tuple[float, float, float]]) -> Image.Image:
    img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(img)
    for i, (_, cx, cy) in enumerate(candidates):
        n = i + 1
        r = 16
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(222, 30, 30), width=3)
        draw.rectangle((cx - 12, cy - r - 14, cx + 12, cy - r + 2), fill=(255, 255, 255))
        draw.text((cx, cy - r - 12), str(n), fill=(20, 20, 20))
    return img


def _jpg_bytes(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _phash_distance(gray_crop: np.ndarray, stored_hex: str) -> int:
    key = stored_hex
    if key not in _hash_cache:
        _hash_cache[key] = imagehash.hex_to_hash(key)
    live = imagehash.phash(Image.fromarray(gray_crop), hash_size=_PHASH_SIZE)
    return int(live - _hash_cache[key])


def _too_large(tpl: np.ndarray, gray: np.ndarray) -> bool:
    return tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]