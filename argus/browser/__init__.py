"""argus.browser — DOM snapshot and effect recording."""
from argus.browser.snapshot import (
    take_snapshot,
    structural_signature,
    element_handle,
    compact_for_llm,
    normalize_path,
)
from argus.browser.effects import EffectRecorder, wait_for_settle

__all__ = [
    "take_snapshot",
    "structural_signature",
    "element_handle",
    "compact_for_llm",
    "normalize_path",
    "EffectRecorder",
    "wait_for_settle",
]
