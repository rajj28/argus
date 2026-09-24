"""Tests for argus/vision: visual marks + locate cascade.

Runs against the local `canvas_map.html` fixture served via file:// (no network,
no LLM - chromium only). Covers: mark capture fields, V0 unchanged, V1 after a
shift, V2 after a zoom, click-through hit verification, graceful None when the
canvas is absent, and V3 wiring with a fake LLM.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import async_playwright

from argus.vision import VisualMark, capture_mark, cached, locate, multiscale, template, vlm

FIXTURES = Path(__file__).parent / "fixtures"
MAP_URL = (FIXTURES / "canvas_map.html").as_uri()
VIEWPORT = {"width": 900, "height": 700}
MARK_SIZE = 48
HINT = "drone marker Falcon-1"
DRONE_ID = "falcon-1"


def _url(**query: str) -> str:
    if not query:
        return MAP_URL
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    return f"{MAP_URL}?{qs}"


@pytest_asyncio.fixture
async def browser():
    """One chromium instance shared across tests (each test opens its own page)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        yield browser
        await browser.close()


async def _open(browser):
    context = await browser.new_context(viewport=VIEWPORT)
    page = await context.new_page()
    await page.goto(MAP_URL)
    return page


async def _drone(page, drone_id: str) -> dict:
    drones = await page.evaluate("window.__drones || []")
    return next(d for d in drones if d["id"] == drone_id)


async def _capture_falcon(page) -> VisualMark:
    drone = await _drone(page, DRONE_ID)
    return await capture_mark(page, "#map", drone["x"], drone["y"], HINT, size=MARK_SIZE)


async def _canvas_bbox(page) -> dict:
    return await page.locator("#map").bounding_box()


@pytest.mark.asyncio
async def test_capture_mark_fields(browser):
    page = await _open(browser)
    try:
        drone = await _drone(page, DRONE_ID)
        mark = await _capture_falcon(page)

        assert isinstance(mark, VisualMark)
        assert mark.canvas_selector == "#map"
        assert mark.hint == HINT
        assert mark.bbox[:2] == pytest.approx([12.0, 12.0]) and mark.bbox[2:] == [800.0, 600.0]
        assert 0 <= mark.center_norm[0] <= 1 and 0 <= mark.center_norm[1] <= 1
        assert len(mark.phash) == 64                        # hash_size=16 -> 256 bits -> 64 hex
        assert isinstance(mark.nearby_labels, list)
        assert "@900x700" in mark.state_key and "canvas_map" in mark.state_key

        # the crop is a MARK_SIZE x MARK_SIZE PNG that round-trips
        img = Image.open(io.BytesIO(base64.b64decode(mark.crop_png_b64)))
        assert img.size == (MARK_SIZE, MARK_SIZE)
        assert mark.crop_size == MARK_SIZE

        # center_norm reproduces the captured canvas coordinate
        assert mark.center_norm[0] * 800 == pytest.approx(drone["x"], abs=2)
        assert mark.center_norm[1] * 600 == pytest.approx(drone["y"], abs=2)

        # page_point() is the stored page-space center
        px, py = mark.page_point()
        assert px == pytest.approx(mark.bbox[0] + mark.center_norm[0] * 800)
        assert py == pytest.approx(mark.bbox[1] + mark.center_norm[1] * 600)
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_locate_cascade_and_click_through(browser):
    """V0 unchanged, V1 after shift=40, V2 after zoom=1.15; clicks hit the right icon."""
    page = await _open(browser)
    try:
        mark = await _capture_falcon(page)

        # --- V0: unchanged layout -------------------------------------------------
        r0 = await locate(page, mark)
        assert r0 is not None and r0["tier"] == 0 and r0["method"] == "cached"
        await page.mouse.click(r0["x"], r0["y"])
        assert await page.evaluate("window.lastHit") == DRONE_ID

        # --- V1: after shift=40 the cached crop is gone but the template matches ---
        await page.goto(_url(shift=40))
        assert await cached(page, mark) is None
        r1 = await template(page, mark)
        assert r1 is not None
        x, y, tier, conf = r1
        assert tier == 1 and conf >= 0.80
        drone = await _drone(page, DRONE_ID)
        bbox = await _canvas_bbox(page)
        assert x == pytest.approx(bbox["x"] + drone["x"], abs=8)
        assert y == pytest.approx(bbox["y"] + drone["y"], abs=8)
        await page.mouse.click(x, y)
        assert await page.evaluate("window.lastHit") == DRONE_ID

        # --- V2: after zoom=1.15 the template only matches at a rescaled size ----
        await page.goto(_url(zoom=1.15))
        r2 = await locate(page, mark)
        assert r2 is not None and r2["tier"] == 2 and r2["method"] == "multiscale"
        assert r2["confidence"] >= 0.80
        drone = await _drone(page, DRONE_ID)
        bbox = await _canvas_bbox(page)
        assert r2["x"] == pytest.approx(bbox["x"] + drone["x"], abs=10)
        assert r2["y"] == pytest.approx(bbox["y"] + drone["y"], abs=10)
        await page.mouse.click(r2["x"], r2["y"])
        assert await page.evaluate("window.lastHit") == DRONE_ID
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_cascade_and_multiscale_near_located_points(browser):
    """template (V1) and multiscale (V2) locate the Falcon icon on the base layout."""
    page = await _open(browser)
    try:
        mark = await _capture_falcon(page)
        bbox = await _canvas_bbox(page)

        drone = await _drone(page, DRONE_ID)
        res = await template(page, mark)
        assert res is not None and res[2] == 1 and res[3] >= 0.80
        assert res[0] == pytest.approx(bbox["x"] + drone["x"], abs=6)
        assert res[1] == pytest.approx(bbox["y"] + drone["y"], abs=6)

        await page.goto(_url(zoom=1.15))
        drone = await _drone(page, DRONE_ID)
        res = await multiscale(page, mark)
        assert res is not None and res[2] == 2 and res[3] >= 0.80
        assert res[0] == pytest.approx(bbox["x"] + drone["x"], abs=10)
        assert res[1] == pytest.approx(bbox["y"] + drone["y"], abs=10)
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_locate_returns_none_when_canvas_absent(browser):
    """No canvas -> every stage returns None and locate() (without llm) returns None."""
    page = await _open(browser)
    try:
        mark = await _capture_falcon(page)
        await page.goto("about:blank")
        assert await cached(page, mark) is None
        assert await template(page, mark) is None
        assert await multiscale(page, mark) is None
        assert await locate(page, mark) is None
    finally:
        await page.close()


class _FakeLLM:
    """Records the vision-call contract and answers with a fixed mark id."""

    def __init__(self, mark_id: int = 1, confidence: float = 0.9):
        self.mark_id = mark_id
        self.confidence = confidence
        self.calls: list[dict] = []

    async def json(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"mark": self.mark_id, "confidence": self.confidence}


@pytest.mark.asyncio
async def test_vlm_uses_llm_and_maps_id_to_coordinate(browser):
    """V3 draws numbered candidates, sends a vision image, and maps id -> page coord."""
    page = await _open(browser)
    try:
        mark = await _capture_falcon(page)
        fake = _FakeLLM(mark_id=1, confidence=0.9)
        res = await vlm(page, mark, fake)
        assert res is not None
        x, y, tier, conf = res
        assert tier == 3 and conf == 0.9

        call = fake.calls[0]
        assert call["tier"] == "vision" and call["purpose"] == "vision"
        assert HINT in call["user"]
        assert len(call["images"]) == 1 and len(call["images"][0]) > 0

        # id 1 is the strongest template peak -> the Falcon icon itself
        drone = await _drone(page, DRONE_ID)
        bbox = await _canvas_bbox(page)
        assert x == pytest.approx(bbox["x"] + drone["x"], abs=10)
        assert y == pytest.approx(bbox["y"] + drone["y"], abs=10)
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_vlm_null_mark_returns_none(browser):
    """A null id / low confidence answer is not accepted."""
    page = await _open(browser)
    try:
        mark = await _capture_falcon(page)
        assert await vlm(page, mark, _FakeLLM(mark_id=None)) is None
        assert await vlm(page, mark, _FakeLLM(mark_id=1, confidence=0.1)) is None
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_visual_heal_persisted(browser):
    """After a tier>0 heal the new VisualMark must let the next locate call hit V0 (tier 0).

    This is the unit-level proof for the 'visual heal persistence' fix in _execute_visual:
    - Capture mark on the base layout (V0 baseline).
    - Navigate to a panned layout so V0 misses and locate() uses V1/V2 (tier >= 1).
    - Re-capture the mark at the hit point (same as _execute_visual does after the fix).
    - Verify the fresh mark: its center_norm encodes the *new* canvas position.
    - Verify that locate() on the SAME panned page using the fresh mark returns tier 0.
    """
    page = await _open(browser)
    try:
        # --- step 1: baseline capture ---
        mark_v0 = await _capture_falcon(page)

        # --- step 2: navigate to a panned layout so V0 misses ---
        await page.goto(_url(shift=40))
        v0_result = await cached(page, mark_v0)
        assert v0_result is None, "V0 must miss after pan so the test is valid"

        # --- step 3: locate() falls through to V1/V2 ---
        hit = await locate(page, mark_v0)
        assert hit is not None, "locate() must still find the marker via template/multiscale"
        assert hit["tier"] >= 1, f"expected tier>=1 after pan, got {hit['tier']}"

        # --- step 4: re-capture at the hit point (mirrors _execute_visual after the fix) ---
        bbox = await page.locator(mark_v0.canvas_selector).bounding_box()
        assert bbox is not None
        canvas_x = hit["x"] - bbox["x"]
        canvas_y = hit["y"] - bbox["y"]
        fresh_mark = await capture_mark(
            page, mark_v0.canvas_selector, canvas_x, canvas_y,
            mark_v0.hint, size=mark_v0.crop_size or 64,
        )

        # --- step 5: the fresh mark must encode the new canvas position ---
        drone = await _drone(page, DRONE_ID)
        assert fresh_mark.center_norm[0] * fresh_mark.bbox[2] == pytest.approx(drone["x"], abs=8)
        assert fresh_mark.center_norm[1] * fresh_mark.bbox[3] == pytest.approx(drone["y"], abs=8)

        # the fresh mark must differ from the original (the drone moved)
        original_cx = mark_v0.center_norm[0] * mark_v0.bbox[2]
        fresh_cx = fresh_mark.center_norm[0] * fresh_mark.bbox[2]
        assert abs(fresh_cx - original_cx) > 2, (
            "recaptured mark center_norm must differ from original after pan"
        )

        # --- step 6: using the fresh mark, the next locate() must return tier 0 (V0) ---
        r_v0 = await locate(page, fresh_mark)
        assert r_v0 is not None, "locate() with fresh mark must find the marker"
        assert r_v0["tier"] == 0, (
            f"next run must be pure tier-0 replay after a heal; got tier={r_v0['tier']}"
        )
    finally:
        await page.close()


@pytest.mark.asyncio
async def test_template_robust_to_pan(browser):
    """V1/V2 must succeed even when the map is panned (background pixels in the crop change).

    This mirrors chaos-seed-7 in the live demo (dx=-41, dy=11, zoom=1.0): the stored
    crop contains a background that is now at a different offset, so the raw pixel
    content of the crop differs from the live canvas at the new marker position.
    The foreground mask ensures only the marker shape/colour/label pixels are compared.
    """
    page = await _open(browser)
    try:
        # --- baseline capture (zoom=1.0, no shift) ---
        mark = await _capture_falcon(page)

        # Mask must be stored (non-empty) for new marks.
        assert mark.mask_png_b64 != "", "mask_png_b64 must be stored by capture_mark"

        # --- pan only (shift=41 approximates dx=-41 in the live app) ---
        await page.goto(_url(shift=41))

        # V0 must fail (the stored position no longer has the same phash).
        v0 = await cached(page, mark)
        assert v0 is None, "V0 should miss after a pure pan"

        # V1 must succeed with confidence >= 0.80 despite the background shift.
        v1 = await template(page, mark)
        assert v1 is not None, "V1 must find the marker after a pure pan using the foreground mask"
        x1, y1, tier1, conf1 = v1
        assert tier1 == 1
        assert conf1 >= 0.80, f"V1 confidence too low after pan: {conf1}"

        # The located point must be close to the actual drone position on the panned canvas.
        drone = await _drone(page, DRONE_ID)
        bbox = await page.locator("#map").bounding_box()
        assert x1 == pytest.approx(bbox["x"] + drone["x"], abs=10)
        assert y1 == pytest.approx(bbox["y"] + drone["y"], abs=10)

        # Clicking the located point must hit the correct drone.
        await page.mouse.click(x1, y1)
        assert await page.evaluate("window.lastHit") == DRONE_ID, (
            "click after pan-locate must hit the same drone"
        )
    finally:
        await page.close()