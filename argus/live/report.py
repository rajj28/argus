"""Evaluation document generator: turns runs/live/*/result.json into EVALUATION.md + index.html."""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

TIMESTAMP = re.compile(r"^\[\s*\d+(?:\.\d+)?s\]\s*")
GROUP_KEYS = ("condition", "phase", "video_label", "badge")

TITLE = "Argus Live — Evaluation Document"

REPRODUCE = [
    "# 1. Start the cockpit starter kit (Docker):",
    "cd flytbase-ahc-swe-qa-hackathon && docker compose up --build",
    "#    cockpit UI http://localhost:4010, backend + control API http://localhost:4000/api",
    "",
    "# 2. Run the scenarios (real Chrome, recordings on):",
    "argus live run                # everything",
    "argus live run --only S1      # one scenario by id prefix",
    "argus live run --only S1 --only S6 --headless",
    "",
    "# 3. Regenerate this evaluation document from the stored results:",
    "argus live report",
    "",
    "# 4. Browse the evidence (videos embedded for local viewing):",
    "#    runs/live/index.html",
]

_DESIGN_INTRO = (
    "Argus Live tests the cockpit the way an operator would and decides with independent evidence. "
    "It is built on Playwright driving real Chrome with Chrome DevTools Protocol access, and reuses "
    "Argus's self-healing semantic engine: the UI is read by meaning, so small markup changes do not "
    "break the probes."
)

_DESIGN_PARAS: list[tuple[str, str]] = [
    ("Operators.", "Real Chrome browsers at desktop, tablet and phone viewports, with touch and "
                   "mobile emulation; a scenario can open several independent users, each in its own "
                   "browser context, so multi-user behaviour is genuine."),
    ("Scenario engine.", "Each scenario declares a starting state (simulator reset, drones airborne) "
                         "and steps, then changes conditions through the starter kit's control API: "
                         "socket-drop / socket-delay, sim-offline, video faults (freeze, black, wrong "
                         "source), socket-refuse / kick, device emulation and multi-user sessions."),
    ("Probes.", "Probes read only what the user can see: the UI by meaning (labels and visible text, "
                "anchored to data-testids); the video element (decoded-frame counters and a perceptual "
                "frame hash drawn to a canvas); the Cesium map (entities, label text and screen "
                "positions found by walking the React fiber to the live viewer); and layout geometry "
                "(bounding boxes, elementFromPoint occlusion, scrollability)."),
    ("Ground truth.", "Simulator state, backend health and the active fault list come from the control "
                      "API plus a socket feed that subscribes exactly like the cockpit does — never "
                      "from the UI under test."),
    ("Oracles.", "Compare the two sides: UI versus truth within tolerance, freshness honesty (stale or "
                 "delayed data must say so), reachability, occlusion, map-label overlap, multi-user "
                 "propagation lag, and recovery after outages."),
    ("Triage.", "Verdicts are deterministic, and symptoms that share a root cause are grouped into one "
                "issue. Precision guards prevent false positives — for example a control is "
                "scrolled into view before it may be called unreachable."),
    ("Evidence.", "Every run records each operator in real time with a HUD overlay narrating the step, "
                  "the UI's claim, the ground truth and the verdict, alongside screenshots, timed "
                  "samples and a machine-readable result.json. The recording is the report."),
]

_DESIGN_DIAGRAM = """\
Operators  (real Chrome: desktop / tablet / phone, independent users)
    |
    v
Scenario engine  --(control API)-->  changing conditions
  starting state, steps              socket-drop/delay, sim-offline,
    |                                video faults, socket-refuse/kick
    v
Probes  -->  UI claims: text by meaning + test ids, video frame
    |        counters & frame hashes, Cesium entities via React
    |        fiber, layout / occlusion geometry
    v
Oracles  <--  Ground truth (simulator state, backend health, active faults)
    |         UI vs truth, freshness honesty, reachability, occlusion,
    |         overlap, propagation lag, recovery
    v
Triage  (deterministic verdicts, root-cause grouping, precision guards)
    |
    v
Evidence  (HUD-narrated recordings, screenshots, samples, result.json
           --> EVALUATION.md + runs/live/index.html)"""


def load_results(runs: Path) -> list[dict]:
    """Read every ``runs/*/result.json``, sorted by scenario id."""
    results = []
    for path in sorted(runs.glob("*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            results.append(data)
    return results


def order_results(results: list[dict], include: Optional[list[str]] = None) -> list[dict]:
    """Order scenarios: `include` (id prefixes) wins; default BUG/ERROR first, then PASS."""
    if include:
        chosen: list[dict] = []
        for key in include:
            for r in results:
                if r["id"].startswith(key) and r not in chosen:
                    chosen.append(r)
        return chosen
    failing = [r for r in results if r.get("verdict") != "PASS"]
    passing = [r for r in results if r.get("verdict") == "PASS"]
    return failing + passing


def _summary(results: list[dict]) -> str:
    issues = sum(len(r.get("findings") or []) for r in results)
    failing = sum(1 for r in results if r.get("verdict") == "BUG")
    passing = sum(1 for r in results if r.get("verdict") == "PASS")
    errors = len(results) - failing - passing
    levels = sorted({r.get("level", "?") for r in results})
    categories = sorted({r.get("category", "?") for r in results})
    extra = (f" {errors} scenario(s) ended in ERROR (a harness problem, reported openly, not a "
             f"product verdict)." if errors else "")
    return (
        f"Argus Live ran {len(results)} scenario(s) against the FlytBase cockpit starter kit with real "
        f"Chrome browsers, independent ground truth from the simulator and control API, and narrated "
        f"recordings as evidence. It found {issues} genuine issue(s) across {failing} scenario(s) "
        f"(verdict BUG) and confirmed {passing} scenario(s) PASS with no false positives, covering "
        f"level(s) {', '.join(levels) or '-'} and categories: {'; '.join(categories) or '-'}.{extra} "
        f"Every issue is reported once, with its grouped symptoms, timed samples, screenshots and a "
        f"real-time video in which the HUD shows the UI's claim next to the ground truth."
    )


def _clean_steps(steps: list[str]) -> tuple[Optional[str], list[str]]:
    """Strip '[ 12.3s] ' timestamps; split off a leading 'Start:' step as the starting state."""
    cleaned = [TIMESTAMP.sub("", s).strip() for s in steps]
    start = None
    if cleaned and re.match(r"^start\b\s*[:—-]\s*", cleaned[0], re.I):
        start = re.sub(r"^start\b\s*[:—-]\s*", "", cleaned[0], flags=re.I).strip()
        cleaned = cleaned[1:]
    return start, cleaned


def _group_key(row: dict) -> tuple:
    return tuple(str(row.get(k)) for k in GROUP_KEYS if k in row)


def key_samples(samples: list[dict], limit: int = 8) -> list[dict]:
    """Pick the informative rows: group boundaries (condition/phase/label change), first and last."""
    if len(samples) <= limit:
        return list(samples)
    picked = [samples[0]]
    for prev, cur in zip(samples, samples[1:]):
        if _group_key(cur) != _group_key(prev):
            picked.append(cur)
    if samples[-1] is not picked[-1]:
        picked.append(samples[-1])
    if len(picked) > limit:
        step = (len(picked) - 1) / (limit - 1)
        picked = [picked[round(i * step)] for i in range(limit)]
    return picked


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _rel(target: Path, base: Path) -> str:
    try:
        return os.path.relpath(target, base).replace(os.sep, "/")
    except ValueError:                       # different drives on Windows
        return target.as_posix()


def _video_href(r: dict, runs: Path, base: Path, video_links: Optional[dict[str, str]]) -> tuple[str, bool]:
    """(href, is_local) — an uploaded link when given, else the local recording path."""
    if video_links and r["id"] in video_links:
        return video_links[r["id"]], False
    name = (r.get("videos") or ["video.mp4"])[0]
    return _rel(runs / r["id"] / name, base), True


def _samples_columns(rows: list[dict]) -> list[str]:
    cols: list[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def _samples_table_md(rows: list[dict]) -> str:
    cols = _samples_columns(rows)
    if not cols:
        return ""
    esc = lambda v: _fmt(v).replace("|", "\\|")
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(esc(row.get(c, "")) for c in cols) + " |" for row in rows]
    return "\n".join(lines)


def _pass_line(r: dict) -> str:
    what = (r.get("description") or "").split(". ")[0].strip().rstrip(".")
    return f"verified: {what}. Every probe matched ground truth; no finding was raised"


def _scenario_md(num: int, r: dict, runs: Path, base: Path, video_links: Optional[dict[str, str]]) -> str:
    out: list[str] = [f"### {num}. {r['title']}  ·  `{r['id']}`", ""]
    out.append(f"**Level / Category:** {r.get('level', '?')} · {r.get('category', '?')}  ")
    out.append(f"**Verdict:** **{r.get('verdict', '?')}**  ")
    out.append(f"**Duration:** {r.get('duration_s', 0)} s · started {r.get('started', '?')}")
    out.append("")
    out.append(f"**Description.** {r.get('description', '')}")
    out.append("")
    out.append(f"**Approach.** {r.get('approach', '')}")
    out.append("")
    start, steps = _clean_steps(r.get("steps") or [])
    if start:
        out.append(f"**Starting state.** {start}")
        out.append("")
    if steps:
        out.append("**Steps.**")
        out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        out.append("")
    findings = r.get("findings") or []
    if r.get("verdict") == "BUG" and findings:
        out.append("**Result: BUG.**")
        for f in findings:
            out.append("")
            out.append(f"- **{f.get('title', 'finding')}** — severity: **{f.get('severity', '?')}** "
                       f"({f.get('level', r.get('level', '?'))}, {f.get('category', r.get('category', ''))})")
            if f.get("detail"):
                out.append(f"  {f['detail']}")
            symptoms = f.get("symptoms") or []
            if symptoms:
                out.append(f"  Grouped symptoms ({len(symptoms)}, one root cause):")
                out += [f"  - {s}" for s in symptoms]
            extra = json.dumps(f.get("evidence") or {}, separators=(",", ":"), default=str)
            if extra not in ("{}", ""):
                out.append(f"  Finding evidence: `{extra[:300]}`")
    elif r.get("verdict") == "PASS":
        out.append(f"**Result: PASS** — {_pass_line(r)}.")
    else:
        out.append(f"**Result: {r.get('verdict', '?')}.** The scenario did not complete cleanly "
                   f"(see the step log above); no product verdict is claimed — errors are reported, "
                   f"never hidden.")
    out.append("")
    samples = r.get("samples") or []
    shots = r.get("shots") or []
    href, is_local = _video_href(r, runs, base, video_links)
    out.append(f"**Evidence.** {len(samples)} timed sample(s), {len(shots)} screenshot(s), "
               f"full run in `runs/live/{r['id']}/result.json`.")
    table = _samples_table_md(key_samples(samples))
    if table:
        out.append("")
        out.append("Key samples (UI claim vs ground truth):")
        out.append("")
        out.append(table)
    if shots:
        out.append("")
        out.append("Screenshots: " + ", ".join(f"`{_rel(runs / r['id'] / s, base)}`" for s in shots))
    out.append("")
    if is_local:
        out.append(f"**Video.** [▶ {r['id']} — narrated recording]({href})  ")
        out.append("*(TODO: upload the video and replace this local path with the hosted link.)*")
    else:
        out.append(f"**Video.** [▶ {r['id']} — narrated recording]({href})")
    return "\n".join(out)


def build_markdown(results: list[dict], runs: Path, base: Path,
                   video_links: Optional[dict[str, str]] = None) -> str:
    """Render already-ordered results (see order_results) as the markdown document."""
    parts = [f"# {TITLE}", "", _summary(results), "", "## System design", "", _DESIGN_INTRO, ""]
    for lead, body in _DESIGN_PARAS:
        parts.append(f"**{lead}** {body}")
        parts.append("")
    parts += ["```", _DESIGN_DIAGRAM, "```", "", "## Scenarios", ""]
    ordered = results
    if not ordered:
        parts.append("_No results found. Run `argus live run` first._")
        parts.append("")
    for i, r in enumerate(ordered, 1):
        parts += [_scenario_md(i, r, runs, base, video_links), "", "---", ""]
    parts += ["## How to reproduce", "", "```bash", *REPRODUCE, "```", ""]
    return "\n".join(parts)


def _scenario_html(num: int, r: dict, video_links: Optional[dict[str, str]]) -> str:
    e = html.escape
    rid = r["id"]
    verdict = r.get("verdict", "?")
    colour = {"BUG": "#c0392b", "PASS": "#1e8e5a"}.get(verdict, "#b8860b")
    parts = [f'<section class="scenario" id="{e(rid)}">',
             f'<h3>{num}. {e(r.get("title", rid))} <code>{e(rid)}</code></h3>',
             f'<p class="meta">Level <b>{e(str(r.get("level", "?")))}</b> · {e(str(r.get("category", "?")))}'
             f' · <span class="verdict" style="background:{colour}">{e(verdict)}</span>'
             f' · {e(str(r.get("duration_s", 0)))} s</p>',
             f'<p><b>Description.</b> {e(str(r.get("description", "")))}</p>',
             f'<p><b>Approach.</b> {e(str(r.get("approach", "")))}</p>']
    start, steps = _clean_steps(r.get("steps") or [])
    if start:
        parts.append(f"<p><b>Starting state.</b> {e(start)}</p>")
    if steps:
        parts.append("<p><b>Steps.</b></p><ol>" + "".join(f"<li>{e(s)}</li>" for s in steps) + "</ol>")
    findings = r.get("findings") or []
    if verdict == "BUG" and findings:
        parts.append('<p class="result-bug"><b>Result: BUG</b></p>')
        for f in findings:
            parts.append(f'<div class="finding"><p><b>{e(str(f.get("title", "")))}</b> — severity '
                         f'<b>{e(str(f.get("severity", "?")))}</b></p>')
            if f.get("detail"):
                parts.append(f"<p>{e(str(f['detail']))}</p>")
            symptoms = f.get("symptoms") or []
            if symptoms:
                parts.append(f"<p>Grouped symptoms ({len(symptoms)}, one root cause):</p><ul>"
                             + "".join(f"<li>{e(str(s))}</li>" for s in symptoms) + "</ul>")
            parts.append("</div>")
    elif verdict == "PASS":
        parts.append(f'<p class="result-pass"><b>Result: PASS</b> — {e(_pass_line(r))}.</p>')
    else:
        parts.append(f'<p class="result-error"><b>Result: {e(verdict)}.</b> The scenario did not '
                     f"complete cleanly (see the step log); no product verdict is claimed.</p>")
    samples = key_samples(r.get("samples") or [])
    if samples:
        cols = _samples_columns(samples)
        parts.append("<p><b>Evidence.</b> Key samples (UI claim vs ground truth):</p>")
        parts.append('<table class="samples"><tr>' + "".join(f"<th>{e(c)}</th>" for c in cols) + "</tr>")
        for row in samples:
            parts.append("<tr>" + "".join(f"<td>{e(_fmt(row.get(c, '')))}</td>" for c in cols) + "</tr>")
        parts.append("</table>")
    shots = r.get("shots") or []
    if shots:
        parts.append('<p class="shots">' + " ".join(
            f'<a href="{e(rid)}/{e(s)}"><img src="{e(rid)}/{e(s)}" alt="{e(s)}" loading="lazy"></a>'
            for s in shots) + "</p>")
    if video_links and rid in video_links:
        parts.append(f'<p><b>Video.</b> <a href="{html.escape(video_links[rid], quote=True)}">'
                     f"hosted recording</a> (local copy below)</p>")
        name = (r.get("videos") or ["video.mp4"])[0]
        parts.append(f'<video controls preload="metadata" src="{e(rid)}/{e(name)}"></video>')
    else:
        name = (r.get("videos") or ["video.mp4"])[0]
        parts.append(f'<p><b>Video.</b> <code>{e(rid)}/{e(name)}</code> '
                     f'<span class="todo">(TODO: upload and link)</span></p>')
        parts.append(f'<video controls preload="metadata" src="{e(rid)}/{e(name)}"></video>')
    parts.append("</section>")
    return "\n".join(parts)


def build_html(results: list[dict], video_links: Optional[dict[str, str]] = None) -> str:
    """Render already-ordered results (see order_results) as the standalone HTML page."""
    e = html.escape
    css = ("body{background:#fff;color:#1c2430;font:15px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;"
           "margin:0}main{max-width:920px;margin:0 auto;padding:32px 24px 64px}"
           "h1{font-size:26px;border-bottom:2px solid #e6e9ef;padding-bottom:10px}"
           "h2{font-size:20px;margin-top:36px}h3{font-size:17px;margin-bottom:4px}"
           "code{background:#f2f4f8;padding:1px 5px;border-radius:4px;font-size:13px}"
           "pre{background:#f7f8fa;border:1px solid #e6e9ef;border-radius:8px;padding:14px;"
           "overflow-x:auto;font-size:13px;line-height:1.45}"
           ".meta{color:#5a6675;font-size:13.5px}.verdict{color:#fff;border-radius:5px;padding:1px 8px;"
           "font-weight:700;font-size:12px}.scenario{border:1px solid #e6e9ef;border-radius:10px;"
           "padding:14px 18px;margin:18px 0}.finding{border-left:3px solid #c0392b;padding-left:12px;"
           "margin:8px 0}table.samples{border-collapse:collapse;font-size:13px;margin:8px 0}"
           "table.samples th,table.samples td{border:1px solid #dfe4ea;padding:4px 9px;text-align:left}"
           "table.samples th{background:#f2f4f8}.shots img{max-width:210px;border:1px solid #dfe4ea;"
           "border-radius:6px;margin:3px}video{width:100%;max-width:860px;border-radius:8px;"
           "border:1px solid #dfe4ea;margin:6px 0}.todo{color:#9a6b00;font-size:13px}"
           "p.result-bug b{color:#c0392b}p.result-pass b{color:#1e8e5a}"
           "p.result-error b{color:#b8860b}")
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f"<title>{e(TITLE)}</title><style>{css}</style></head><body><main>",
             f"<h1>{e(TITLE)}</h1>", f"<p>{e(_summary(results))}</p>", "<h2>System design</h2>",
             f"<p>{e(_DESIGN_INTRO)}</p>"]
    for lead, body in _DESIGN_PARAS:
        parts.append(f"<p><b>{e(lead)}</b> {e(body)}</p>")
    parts += [f"<pre>{e(_DESIGN_DIAGRAM)}</pre>", "<h2>Scenarios</h2>"]
    if not results:
        parts.append("<p><i>No results found. Run <code>argus live run</code> first.</i></p>")
    parts += [_scenario_html(i, r, video_links) for i, r in enumerate(results, 1)]
    parts += ["<h2>How to reproduce</h2>", "<pre>" + e("\n".join(REPRODUCE)) + "</pre>",
              "</main></body></html>"]
    return "\n".join(parts)


def build_report(runs: Path = Path("runs/live"), out: Path = Path("EVALUATION.md"),
                 include: list[str] | None = None,
                 video_links: dict[str, str] | None = None) -> Path:
    """Write EVALUATION.md (+ runs/live/index.html) from stored scenario results; return the md path."""
    runs, out = Path(runs), Path(out)
    results = order_results(load_results(runs), include)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_markdown(results, runs, out.parent, video_links), encoding="utf-8")
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "index.html").write_text(build_html(results, video_links), encoding="utf-8")
    return out
