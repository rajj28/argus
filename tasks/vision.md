# Task: vision tier for canvas / map UIs (`argus/vision/*`)

Owner: you own ONLY `argus/vision/__init__.py`, `argus/vision/marks.py`, `argus/vision/locate.py`,
`tests/test_vision.py`, `tests/fixtures/canvas_*.html`. Read `AGENTS.md`, `argus/models.py`,
`argus/healing/resolver.py`, `argus/llm/client.py` (`LLMClient.json(..., tier="vision", images=[bytes])`).
Installed: opencv-python-headless, pillow, imagehash, numpy.

Principle: vision is a SECOND perception channel used only where the DOM is insufficient (canvas maps,
video, WebGL). The model never emits raw coordinates; it picks a mark id and Argus resolves it.

1. `marks.py`
   - `VisualMark` (pydantic): `state_key: str` (url path + viewport), `bbox: [x,y,w,h]`,
     `center_norm: [fx, fy]` (relative to the canvas element), `canvas_selector: str`, `crop_png_b64: str`
     (small 48-96px crop), `phash: str` (imagehash.phash of the crop), `nearby_labels: list[str]`,
     `hint: str` ("drone marker Falcon-1").
   - `async def capture_mark(page, canvas_selector, x, y, hint, size=64) -> VisualMark` (screenshot the canvas
     element, crop around (x,y) in canvas coords, compute phash).
2. `locate.py` — cascade, each returns `(x, y, tier, confidence)` in PAGE coordinates or None:
   - V0 `cached`: canvas bbox * center_norm, accepted if the crop at that point has phash distance <= 8.
   - V1 `template`: cv2.matchTemplate (TM_CCOEFF_NORMED) of the stored crop over a canvas screenshot;
     accept max score >= 0.80 and second-best peak (outside a 20px radius) at least 0.05 lower.
   - V2 `multiscale`: repeat V1 at scales 0.8..1.25 (zoomed maps).
   - V3 `vlm`: draw numbered set-of-marks candidates (up to 12 local maxima of the template response,
     plus a coarse 4x4 grid if fewer) onto the screenshot with PIL, send to `llm.json(tier="vision")` asking
     for `{"mark": <id or null>, "confidence": 0..1}` given the hint; map id -> coordinate.
   - `async def locate(page, mark, llm=None) -> dict | None` running V0..V3 in order, never V3 without an llm.
3. Tests (no network, no LLM): `tests/fixtures/canvas_map.html` draws a map-like canvas with 4 drone
   icons from a seeded layout (icons as small distinct shapes + labels drawn on canvas; a query param
   `?shift=40&zoom=1.1` moves/scales everything). Capture a mark on the base layout, then assert V0 finds it
   unchanged, V1 finds it after `shift=40`, V2 after `zoom=1.15`, and that a click at the returned point
   hits the right icon (the page records the last clicked icon id in `window.lastHit`).
Run `.venv/Scripts/python.exe -m pytest -q tests/test_vision.py` until green, then print a concise summary.
Do not modify other argus modules; describe the integration points (resolver/runner) in your summary.
