"""Argus Live scenario engine: real browsers, ground truth, narrated recordings, grouped findings.

A scenario drives the cockpit like an operator, changes conditions (faults, devices, network), and checks what the
screen claims against what the simulator knows. Every run is recorded in real time with a HUD overlay that narrates
the step, the UI's claim, the ground truth and the verdict, so the recording is the evidence.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from argus.live.truth import Truth

COCKPIT = "http://localhost:4010"
# measurements must reflect the app, not Chrome saving power on covered windows
NO_THROTTLE = ["--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
               "--disable-background-timer-throttling"]
DEVICES = {
    "desktop": {"viewport": {"width": 1440, "height": 900}},
    "tablet": {"viewport": {"width": 820, "height": 1180}, "is_mobile": True, "has_touch": True},
    "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
              "device_scale_factor": 2},
}
NOT_LIVE = re.compile(r"\b(stale|delayed|lagging|offline|disconnected|lost|no data|reconnecting|unavailable|"
                      r"not live|outdated|last (update|seen)|\d+\s*s ago|paused|frozen)\b", re.I)

# What the operator sees - read by meaning (labels and visible text), with the app's test ids as anchors.
READ_UI = r"""() => {
  const txt = el => (el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : '');
  const q = s => document.querySelector(s);
  const telemetry = {};
  document.querySelectorAll('[data-testid^="telemetry-"]').forEach(el => {
    const box = el.closest('div') || el; const label = txt(box).replace(txt(el), '').trim() || el.dataset.testid;
    telemetry[label] = txt(el);
  });
  const v = q('video'); const pq = v && v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
  return {
    socket: txt(q('[data-testid="socket-status"]')),
    rows: [...document.querySelectorAll('[data-testid^="device-row-"]')].map(txt),
    selected_status: txt(q('[data-testid="status-flight"]')),
    telemetry,
    video_label: txt(q('[data-testid="video-state"]')),
    video_frames: pq ? pq.totalVideoFrames : null,
    video_time: v ? v.currentTime : null,
    video_title: txt(v && v.closest('div') && v.closest('div').parentElement),
    page_text: (document.body.innerText || '').slice(0, 4000),
  };
}"""

FRAME_HASH = r"""() => {
  const v = document.querySelector('video'); if (!v || !v.videoWidth) return null;
  const c = document.createElement('canvas'); c.width = 16; c.height = 9;
  const g = c.getContext('2d'); g.drawImage(v, 0, 0, 16, 9);
  const d = g.getImageData(0, 0, 16, 9).data; let sum = 0; const lum = [];
  for (let i = 0; i < d.length; i += 4) { const l = (d[i] * 3 + d[i+1] * 6 + d[i+2]) / 10; lum.push(l); sum += l; }
  const avg = sum / lum.length; return lum.map(l => l > avg ? '1' : '0').join('');
}"""

HUD_JS = r"""(s) => {
  let h = document.querySelector('[data-argus-hud]');
  if (!h) {
    h = document.createElement('div'); h.setAttribute('data-argus-hud', '1');
    h.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;font:12px/1.45 ui-monospace,Consolas,monospace;' +
      'color:#e8ecf2;background:rgba(10,14,22,.90);border:1px solid rgba(255,255,255,.18);border-radius:10px;' +
      'padding:10px 12px;box-shadow:0 10px 30px rgba(0,0,0,.45);';
    document.documentElement.appendChild(h);
  }
  const phone = innerWidth < 600;
  const top = s.pos === 'top';
  Object.assign(h.style, phone ? {left:'6px', right:'6px', bottom: top ? 'auto' : '6px', top: top ? '52px' : 'auto', maxHeight:'34vh', overflow:'hidden', fontSize:'10.5px', opacity:'0.94'}
                               : {right:'14px', bottom: top ? 'auto' : '14px', top: top ? '60px' : 'auto', left:'auto', width:'470px'});
  const esc = t => String(t).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const color = {bug:'#ff6b6b', pass:'#3ddc97', info:'#8ab4ff', warn:'#ffc861'};
  const rows = (s.compare || []).map(r => `<tr><td style="color:#9aa4b2;padding-right:8px">${esc(r[0])}</td>` +
      `<td style="color:#ffd48a;padding-right:8px">${esc(r[1])}</td><td style="color:#8ef0c0">${esc(r[2])}</td></tr>`).join('');
  const checks = (s.checks || []).map(c => `<div style="color:${color[c[0]]||'#e8ecf2'}">${c[0]==='bug'?'✗':c[0]==='pass'?'✓':'•'} ${esc(c[1])}</div>`).join('');
  h.innerHTML = `<div style="font-weight:700;color:#fff;margin-bottom:4px">ARGUS · ${esc(s.title||'')}</div>` +
    `<div style="color:#8ab4ff;margin-bottom:6px">▶ ${esc(s.step||'')}</div>` +
    (rows ? `<table style="border-collapse:collapse;margin-bottom:6px"><tr><th style="text-align:left;color:#9aa4b2"></th>` +
      `<th style="text-align:left;color:#ffd48a">UI shows</th><th style="text-align:left;color:#8ef0c0">Ground truth</th></tr>${rows}</table>` : '') +
    checks + (s.verdict ? `<div style="margin-top:6px;padding:4px 8px;border-radius:6px;font-weight:700;` +
      `background:${s.verdict.startsWith('BUG')?'#5c1d24':'#15452f'};color:#fff">${esc(s.verdict)}</div>` : '');
}"""

LAYOUT_JS = r"""(sel) => {
  const hud = document.querySelector('[data-argus-hud]'); if (hud) hud.style.visibility = 'hidden';
  const out = [];
  document.querySelectorAll(sel).forEach(el => {
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = el.getBoundingClientRect(); const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const inView = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth && r.width > 0;
    const pts = [[cx, cy], [r.left + 3, r.top + 3], [r.right - 3, r.top + 3], [r.left + 3, r.bottom - 3], [r.right - 3, r.bottom - 3]];
    let coveredPts = 0, top = null;
    if (inView) for (const [x, y] of pts) {
      const t = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, x)), Math.min(innerHeight - 1, Math.max(0, y)));
      if (t && t !== el && !el.contains(t) && !t.contains(el)) { coveredPts++; top = top || t; }
    }
    const covered = coveredPts === pts.length;
    let scroller = null; let p = el.parentElement;
    while (p) { const cs = getComputedStyle(p); if (/(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 2) { scroller = p; break; } p = p.parentElement; }
    const pageScrolls = document.documentElement.scrollHeight > innerHeight + 2 || document.body.scrollHeight > innerHeight + 2;
    out.push({id: el.dataset.testid || el.tagName, text: (el.innerText || '').replace(/\s+/g,' ').trim().slice(0, 40),
              rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], in_view: inView,
              covered, covered_points: coveredPts, covered_by: covered ? ((top.dataset && top.dataset.testid) || top.tagName + '.' + String(top.className).slice(0, 40)) : null,
              scroller: !!scroller, page_scrolls: pageScrolls});
  });
  if (hud) hud.style.visibility = '';
  return out;
}"""


@dataclass
class Finding:
    title: str
    category: str
    level: str
    severity: str
    detail: str
    symptoms: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    id: str
    title: str
    level: str
    category: str
    description: str
    approach: str
    verdict: str = "PASS"
    findings: list[Finding] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)
    started: str = ""
    duration_s: float = 0.0


class Ctx:
    """What a scenario gets: truth, operators (browser users), a HUD, and result bookkeeping."""

    def __init__(self, browser: Browser, truth: Truth, result: Result, out: Path):
        self.browser, self.truth, self.r, self.out = browser, truth, result, out
        self.contexts: list[tuple[str, BrowserContext]] = []
        self.hud_state: dict[str, Any] = {"title": result.title, "checks": []}
        self._t0 = time.time()

    async def operator(self, label: str = "operator", device: str = "desktop", url: str = COCKPIT) -> Page:
        ctx = await self.browser.new_context(record_video_dir=str(self.out / "raw" / label),
                                             record_video_size=DEVICES[device]["viewport"], **DEVICES[device])
        page = await ctx.new_page()
        self.contexts.append((label, ctx))
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)
        await self.hud(page)
        return page

    async def hud(self, page: Page, **update) -> None:
        self.hud_state.update(update)
        try:
            await page.evaluate(HUD_JS, self.hud_state)
        except Exception:
            pass

    async def hud_all(self, **update) -> None:
        for _, c in self.contexts:
            for p in c.pages:
                await self.hud(p, **update)

    async def step(self, text: str) -> None:
        stamp = f"{time.time() - self._t0:5.1f}s"
        self.r.steps.append(f"[{stamp}] {text}")
        await self.hud_all(step=text)

    async def note(self, kind: str, text: str) -> None:
        self.hud_state["checks"] = (self.hud_state.get("checks", []) + [[kind, text]])[-7:]
        await self.hud_all()

    async def ui(self, page: Page) -> dict:
        return await page.evaluate(READ_UI)

    async def shot(self, page: Page, name: str) -> None:
        rel = f"shots/{name}.png"
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(self.out / rel), timeout=60000)
            self.r.shots.append(rel)
        except Exception as exc:   # a page too busy to paint is itself evidence
            self.r.steps.append(f"screenshot '{name}' failed: {type(exc).__name__} (page did not paint within 60 s)")

    def find(self, finding: Finding) -> None:
        self.r.findings.append(finding)
        self.r.verdict = "BUG"

    def sample(self, **row) -> None:
        row["t"] = round(time.time() - self._t0, 1)
        self.r.samples.append(row)


def number(text: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def _ffmpeg() -> Optional[str]:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def finalize_videos(out: Path, labels: list[str]) -> list[str]:
    """Convert each operator's webm to mp4; stack several operators side by side into one video."""
    webms = []
    for label in labels:
        files = sorted((out / "raw" / label).glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if files:
            webms.append(files[-1])
    ff = _ffmpeg()
    if not webms:
        return []
    if not ff:
        return [str(p.relative_to(out)) for p in webms]
    target = out / "video.mp4"
    if len(webms) == 1:
        cmd = [ff, "-y", "-loglevel", "error", "-i", str(webms[0]), "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(target)]
    else:
        inputs = sum((["-i", str(w)] for w in webms), [])
        scaled = ";".join(f"[{i}:v]scale=-2:900[v{i}]" for i in range(len(webms)))
        stack = "".join(f"[v{i}]" for i in range(len(webms))) + f"hstack=inputs={len(webms)}[out]"
        cmd = [ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", f"{scaled};{stack}", "-map", "[out]",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]
    try:
        subprocess.run(cmd, check=True, timeout=300)
        return [target.name]
    except Exception:
        return [str(p.relative_to(out)) for p in webms]


ScenarioFn = Callable[[Ctx], Awaitable[None]]


@dataclass
class Scenario:
    id: str
    title: str
    level: str
    category: str
    description: str
    approach: str
    fn: ScenarioFn


async def run_scenarios(scenarios: list[Scenario], out_root: Path, *, headed: bool = True,
                        truth: Optional[Truth] = None, log=print) -> list[Result]:
    truth = truth or Truth()
    results = []
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(channel="chrome", headless=not headed, args=NO_THROTTLE)   # real GPU
        except Exception:
            browser = await pw.chromium.launch(headless=not headed, args=NO_THROTTLE)
        try:
            for sc in scenarios:
                out = out_root / sc.id
                shutil.rmtree(out, ignore_errors=True)
                out.mkdir(parents=True, exist_ok=True)
                r = Result(id=sc.id, title=sc.title, level=sc.level, category=sc.category,
                           description=sc.description, approach=sc.approach,
                           started=time.strftime("%Y-%m-%d %H:%M:%S"))
                ctx = Ctx(browser, truth, r, out)
                log(f"▶ {sc.id}: {sc.title}")
                t0 = time.time()
                try:
                    truth.reset()
                    await sc.fn(ctx)
                except Exception as exc:  # a crashed scenario is reported, never hidden
                    r.verdict = "ERROR"
                    r.steps.append(f"scenario error: {type(exc).__name__}: {exc}"[:400])
                finally:
                    truth.clear_faults()
                    await ctx.hud_all(verdict=("BUG FOUND - " + r.findings[0].title) if r.findings else
                                      ("PASS - behaviour matches ground truth" if r.verdict == "PASS" else r.verdict))
                    await asyncio.sleep(2.5)                      # hold the verdict on screen in the recording
                    labels = [label for label, _ in ctx.contexts]
                    for _, c in ctx.contexts:
                        await c.close()
                    r.duration_s = round(time.time() - t0, 1)
                    r.videos = finalize_videos(out, labels)
                    (out / "result.json").write_text(json.dumps(asdict(r), indent=2, default=str), encoding="utf-8")
                    log(f"  {r.verdict}: " + (r.findings[0].title if r.findings else "no issue") +
                        f"  ({r.duration_s}s, video: {r.videos[:1]})")
                results.append(r)
        finally:
            await browser.close()
    return results
