"""Vision perception channel: capture + locate reusable visual marks on canvas / map UIs.

Public API:
    VisualMark                      - one crop-based identity for a point of interest
    capture_mark(page, selector, x, y, hint, size=64) -> VisualMark
    locate(page, mark, llm=None)    - cascade V0 cached -> V1 template -> V2 multiscale -> V3 vlm
"""
from argus.vision.locate import cached, locate, multiscale, template, vlm
from argus.vision.marks import VisualMark, capture_mark

__all__ = ["VisualMark", "capture_mark", "locate", "cached", "template", "multiscale", "vlm"]