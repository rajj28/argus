"""Visual marks: crop-based identities captured from canvas / map UIs.

Vision is Argus's second perception channel, used only where the DOM is
insufficient (canvas maps, video, WebGL). A `VisualMark` stores a small
screenshot crop around a point of interest plus its perceptual hash - never raw
coordinates. The locator cascade in `argus/vision/locate.py` re-finds the point
on later runs by image content.
"""
from __future__ import annotations

import base64
import io

import cv2
import imagehash
import numpy as np
from PIL import Image
from playwright.async_api import Page
from pydantic import BaseModel, Field

from argus.browser.snapshot import normalize_path

_PHASH_SIZE = 16

# Foreground mask parameters
_MASK_BG_COLORS = 4         # number of dominant background colours to subtract
_MASK_BG_THRESH = 28        # per-channel tolerance for "matches background"
_MASK_DILATE_PX = 3         # dilation radius to blend marker edges
_MASK_LABEL_PAD = 6         # extra pixels below icon centre included in mask (text region)


class VisualMark(BaseModel):
    """One reusable visual identity for a canvas / map point of interest."""

    state_key: str                   # normalized url path + viewport, e.g. "/map@1280x800"
    bbox: list[float]                # page coords of the canvas element: [x, y, w, h]
    center_norm: list[float]         # [fx, fy] relative to the canvas element, each in [0, 1]
    canvas_selector: str             # Playwright selector string of the canvas
    crop_png_b64: str                # small (48-96px) crop as base64 PNG
    phash: str                       # imagehash.phash of the crop (hex string)
    nearby_labels: list[str] = Field(default_factory=list)
    hint: str = ""
    mask_png_b64: str = ""           # uint8 foreground mask (255=foreground, 0=background); "" for old marks

    @property
    def crop_size(self) -> int:
        """Pixel size (square) of the stored crop; 0 when it cannot be decoded."""
        try:
            img = Image.open(io.BytesIO(base64.b64decode(self.crop_png_b64)))
            return max(img.size)
        except Exception:
            return 0

    def page_point(self) -> tuple[float, float]:
        """Page coordinates of the mark's stored center."""
        x, y, w, h = self.bbox
        return x + self.center_norm[0] * w, y + self.center_norm[1] * h


async def capture_mark(page: Page, canvas_selector: str, x: float, y: float,
                       hint: str = "", size: int = 64) -> VisualMark:
    """Screenshot the canvas and record a `size` px crop around canvas coord (x, y).

    ``x`` / ``y`` are coordinates in the canvas element's own pixel grid. The
    crop's phash is the mark's reusable identity; `nearby_labels` collects the
    closest DOM text (best-effort - canvas glyphs are not OCR'd).

    A foreground mask is also computed: pixels that differ from the dominant
    background colours of the crop are marked as 255 (foreground), the rest as
    0 (background). The label text region below the icon centre is unconditionally
    included so that different marker labels contribute to uniqueness.
    """
    locator = page.locator(canvas_selector)
    bbox = await locator.bounding_box()
    if bbox is None:
        raise ValueError(f"canvas {canvas_selector!r} is not attached/visible")
    raw = await locator.screenshot()
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError(f"cannot decode screenshot of {canvas_selector!r}")

    x0, y0, x1, y1 = _crop_box(x, y, size, arr.shape[1], arr.shape[0])
    patch = arr[y0:y1, x0:x1]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    crop_rgb = Image.fromarray(cv2.cvtColor(patch, cv2.COLOR_BGR2RGB))

    w, h = arr.shape[1], arr.shape[0]
    fx = min(1.0, max(0.0, x / w))
    fy = min(1.0, max(0.0, y / h))
    px, py = bbox["x"] + x, bbox["y"] + y
    viewport = page.viewport_size or {"width": 0, "height": 0}

    # Build foreground mask: marker pixels that are not part of the background.
    mask = _build_foreground_mask(patch, size)

    return VisualMark(
        state_key=f"{normalize_path(page.url)}@{viewport['width']}x{viewport['height']}",
        bbox=[bbox["x"], bbox["y"], bbox["width"], bbox["height"]],
        center_norm=[fx, fy],
        canvas_selector=canvas_selector,
        crop_png_b64=_png_b64(crop_rgb),
        phash=str(imagehash.phash(Image.fromarray(gray), hash_size=_PHASH_SIZE)),
        nearby_labels=await _nearby_labels(page, px, py),
        hint=hint,
        mask_png_b64=_mask_b64(mask),
    )


# -- helpers --------------------------------------------------------------------------------------------

def _crop_box(x: float, y: float, size: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Integer crop box of `size` px around (x, y), clamped to the image bounds."""
    half = size // 2
    x0 = max(0, int(round(x - half)))
    y0 = max(0, int(round(y - half)))
    x1 = min(w, x0 + size)
    y1 = min(h, y0 + size)
    return x0, y0, x1, y1


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _mask_b64(mask: np.ndarray) -> str:
    """Encode a uint8 single-channel mask as a base64 PNG string."""
    pil = Image.fromarray(mask, mode="L")
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _build_foreground_mask(patch: np.ndarray, size: int) -> np.ndarray:
    """Build a uint8 foreground mask for a BGR crop.

    Algorithm:
      1. Sample the four corner regions to estimate dominant background colours.
      2. Mark every pixel that is NOT within ``_MASK_BG_THRESH`` of any background
         colour as foreground (255).
      3. Dilate slightly so marker edges are captured cleanly.
      4. Force the lower third of the crop (label text region) to be foreground so
         that different drone labels contribute to uniqueness between markers.
    """
    h, w = patch.shape[:2]
    # --- step 1: collect background colour samples from corners ---
    corner_size = max(4, size // 8)
    corners = [
        patch[:corner_size, :corner_size],          # top-left
        patch[:corner_size, -corner_size:],          # top-right
        patch[-corner_size:, :corner_size],          # bottom-left
        patch[-corner_size:, -corner_size:],         # bottom-right
    ]
    bg_pixels = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0).astype(np.float32)

    # cluster into _MASK_BG_COLORS dominant colours with k-means
    n_clusters = min(_MASK_BG_COLORS, len(bg_pixels))
    if n_clusters >= 2:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, _, centers = cv2.kmeans(bg_pixels, n_clusters, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
        bg_colors = centers.astype(np.float32)
    else:
        bg_colors = bg_pixels[:1]

    # --- step 2: per-pixel background test ---
    flat = patch.reshape(-1, 3).astype(np.float32)
    is_bg = np.zeros(flat.shape[0], dtype=bool)
    for bgc in bg_colors:
        diff = np.abs(flat - bgc)
        is_bg |= (diff.max(axis=1) <= _MASK_BG_THRESH)

    fg_mask = (~is_bg).reshape(h, w).astype(np.uint8) * 255

    # --- step 3: dilate so edges are included ---
    if _MASK_DILATE_PX > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (2 * _MASK_DILATE_PX + 1, 2 * _MASK_DILATE_PX + 1),
        )
        fg_mask = cv2.dilate(fg_mask, kernel)

    # --- step 4: force label text region (lower portion of crop) ---
    label_y_start = max(0, h // 2)
    fg_mask[label_y_start:, :] = 255

    return fg_mask


_LABEL_JS = """(arg) => {
  const px = arg.px - window.scrollX;
  const py = arg.py - window.scrollY;
  const radius = 140;
  const found = [];
  for (const el of document.querySelectorAll('div,span,label,p,li,td,dt,dd,h1,h2,h3,h4')) {
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!text || text.length > 60) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const d = Math.hypot(r.left + r.width / 2 - px, r.top + r.height / 2 - py);
    if (d <= radius) found.push([d, text]);
  }
  found.sort((a, b) => a[0] - b[0]);
  const seen = new Set();
  const out = [];
  for (const [, t] of found) {
    if (seen.has(t)) continue;
    seen.add(t);
    out.push(t);
    if (out.length === 4) break;
  }
  return out;
}"""


async def _nearby_labels(page: Page, px: float, py: float) -> list[str]:
    """Closest DOM texts around the page point (never raises)."""
    try:
        return await page.evaluate(_LABEL_JS, {"px": px, "py": py})
    except Exception:
        return []
