"""Tiered element resolution: replay -> similarity heal -> (runner: out-of-order) -> LLM heal.

`resolve` never touches the page; it scores the snapshot and, only when the evidence is ambiguous,
asks a small model to choose among the top-5 candidates.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from argus.healing.similarity import rank
from argus.llm import prompts as P
from argus.models import ElementInfo, PageSnapshot, Step


class Resolution(BaseModel):
    element: Optional[ElementInfo] = None
    tier: Optional[int] = None
    score: float = 0.0
    margin: float = 0.0
    method: Literal["replay", "similarity", "llm", "vision", "not_found"] = "not_found"
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.element is not None


def _unpack(c: Any) -> tuple[ElementInfo, float, dict]:
    if isinstance(c, (tuple, list)):
        return c[0], float(c[1]), dict(c[2]) if len(c) > 2 else {}
    return c.element, float(c.score), dict(getattr(c, "breakdown", {}) or {})


def describe(e: ElementInfo) -> str:
    label = e.name or e.text or e.label or e.attrs.get("placeholder", "")
    extra = []
    if e.label and e.label != label:
        extra.append(f"label={e.label[:40]!r}")
    if not e.enabled:
        extra.append("disabled")
    ctx = f" (in: {e.context[:60]})" if e.context else ""
    return f'[{e.ref}] {e.role or e.tag} "{label[:60]}"{ctx}' + (f" {{{', '.join(extra)}}}" if extra else "")


async def resolve(snap: PageSnapshot, step: Step, weights: dict[str, float], llm: Any, cfg: Any, *,
                  allow_llm: bool = True, strict: bool = False) -> Resolution:
    """Find the element for `step` on `snap`. `strict=True` is the out-of-order probe (no LLM)."""
    if step.target is None:
        return Resolution(reason="step has no target")
    ranked = [_unpack(c) for c in rank(step.target, snap.elements, step.action, weights, top_k=5)]
    if not ranked:
        return Resolution(reason="no compatible candidates on page")
    e1, s1, _ = ranked[0]
    s2 = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = s1 - s2
    cands = [{"ref": e.ref, "desc": describe(e), "score": round(s, 3),
              "breakdown": {k: round(float(v), 2) for k, v in b.items() if isinstance(v, (int, float))}}
             for e, s, b in ranked]
    th = cfg.thresholds
    base = dict(score=round(s1, 3), margin=round(margin, 3), candidates=cands)

    if strict:
        if s1 >= th.strict and margin >= th.margin:
            return Resolution(element=e1, tier=2, method="similarity", reason="out-of-order match", **base)
        return Resolution(reason=f"no strict match (best {s1:.2f})", **base)

    t = step.target
    same_path = bool((t.xpath and e1.xpath == t.xpath) or (t.css and e1.css == t.css))
    if s1 >= th.replay and margin >= 0.10 and same_path:
        return Resolution(element=e1, tier=0, method="replay", **base)
    b1 = ranked[0][2]
    same_identity = b1.get("name", 0) >= 0.95 and b1.get("role", 0) >= 1.0 and s1 >= 0.5 and margin >= 0.15
    # two top candidates that are links to the target's own destination are interchangeable
    e2 = ranked[1][0] if len(ranked) > 1 else None
    href = t.attrs.get("href", "")
    equivalent = bool(href and e2 is not None and e1.attrs.get("href") == href == e2.attrs.get("href")
                      and s1 >= th.accept)
    if (s1 >= th.accept and margin >= th.margin) or (s1 >= 0.92 and margin >= 0.05) or same_identity or equivalent:
        return Resolution(element=e1, tier=1, method="similarity",
                          reason=f"multi-attribute match {s1:.2f} (runner-up {s2:.2f})", **base)

    if allow_llm and llm is not None and s1 >= th.llm_min:
        user = P.HEAL_USER.format(intent=step.intent, action=step.action, original=t.describe(),
                                  candidates="\n".join(f'{c["desc"]} score={c["score"]}' for c in cands))
        try:
            data = await llm.json(tier="fast", purpose="heal", system=P.HEAL_SYSTEM, user=user, max_tokens=200)
        except Exception as exc:  # LLM unavailable / budget: degrade to not_found
            return Resolution(reason=f"ambiguous (best {s1:.2f}, margin {margin:.2f}); LLM unavailable: "
                                     f"{type(exc).__name__}", **base)
        ref = data.get("ref")
        try:
            conf = float(data.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        chosen = next((e for e, _, _ in ranked if e.ref == ref), None)
        if chosen is not None and conf >= th.llm_confidence:
            s = next(s for e, s, _ in ranked if e.ref == ref)
            return Resolution(element=chosen, tier=3, method="llm", score=round(s, 3), margin=round(margin, 3),
                              candidates=cands, reason=f"LLM pick ({conf:.2f}): {data.get('reason', '')}"[:200])
        return Resolution(reason=f"LLM found no confident match ({data.get('reason', '')})"[:200], **base)

    return Resolution(reason=f"best candidate {s1:.2f} below thresholds", **base)
