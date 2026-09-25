/* Argus landing — binds every number to /api/overview + /api/runs/latest.
   No hard-coded metrics; missing data -> honest empty state. Mock mode via ?mock=1. */
(function () {
  "use strict";

  const MOCK = new URLSearchParams(location.search).get("mock") === "1";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  /* ---------- tiny helpers ---------- */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function el(tag, cls, txt) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }
  const int = (n) => (Number.isFinite(+n) ? Math.round(+n) : 0);
  const pct = (a, b) => (b > 0 ? Math.round((a / b) * 100) : 0);
  function fmtInt(n) { return int(n).toLocaleString("en-US"); }
  function fmtUsd(n) { return "$" + (Number(n) || 0).toFixed(4); }
  function relTime(iso) {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    const s = Math.round((Date.now() - t) / 1000);
    if (s < 0) return "on " + new Date(t).toISOString().slice(0, 10);
    if (s < 60) return s + "s ago";
    const m = Math.round(s / 60); if (m < 60) return m + "m ago";
    const h = Math.round(m / 60); if (h < 24) return h + "h ago";
    const d = Math.round(h / 24); if (d < 30) return d + "d ago";
    return "on " + new Date(t).toISOString().slice(0, 10);
  }
  const dateOf = (iso) => (iso ? String(iso).slice(0, 10) : "");

  async function getJSON(apiPath, mockFile) {
    const url = MOCK ? "assets/mock/" + mockFile : apiPath;
    const r = await fetch(url, { headers: { accept: "application/json" } });
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json();
  }

  /* ---------- verdict pill ---------- */
  function pill(category, count) {
    const p = el("span", "pill v-" + (category || "neutral"));
    p.appendChild(el("span", null, category || "?"));
    if (count != null) p.appendChild(el("span", "k", " " + count));
    return p;
  }
  const TIER_LABEL = { 0: "T0 Replay", 1: "T1 Heal", 2: "T2 Reorder", 3: "T3 LLM", 4: "T4 Replan", 5: "T5 Vision" };
  function tierBadge(t) {
    const b = el("span", "tier tier-" + (t == null ? "x" : t));
    b.textContent = t == null ? "—" : TIER_LABEL[t] || ("T" + t);
    return b;
  }

  /* ================================================================== */
  /* HERO — live product frame                                           */
  /* ================================================================== */
  function renderHero(run, ov) {
    const frame = $("#hero-frame");
    frame.setAttribute("aria-busy", "false");
    const live = (ov && ov.live) || {};
    if (!run || !run.totals) {
      frame.innerHTML = "";
      const e = el("div", "empty");
      e.appendChild(el("div", "empty-h", "No live run yet"));
      e.appendChild(el("div", null, "Start one from the live console — everything there is a real browser driven by Argus."));
      const a = el("a", "btn btn-primary btn-sm", "Watch it run live"); a.href = "live.html"; a.style.marginTop = "8px";
      e.appendChild(a);
      frame.appendChild(e);
      return;
    }
    const t = run.totals;
    const build = (run.label || "").match(/v?(\d\.\d)/);
    $("#hero-app").textContent = "SkyOps · Drone Operations Console";
    $("#hero-ago").textContent = (run.label ? run.label + " · " : "") + relTime(run.started_at);
    $("#hero-build").textContent = (build ? "build " + build[1] + " · " : "") + "run " + String(run.run_id).slice(-6);

    // KPIs
    const kpis = $("#hero-kpis");
    kpis.innerHTML = "";
    const cells = [
      [fmtInt(t.tests), "tests"],
      [fmtInt(sumSteps(t)), "steps"],
      [fmtInt(t.replayed_steps), "replayed t0"],
      [fmtInt(t.healed_steps), "healed"],
      [fmtInt(t.llm_calls), "llm calls"],
    ];
    cells.forEach(([n, l]) => {
      const k = el("div", "kpi");
      k.appendChild(el("div", "kpi-n", n));
      k.appendChild(el("div", "kpi-l", l));
      kpis.appendChild(k);
    });
    const vk = el("div", "kpi"); vk.style.gridColumn = "span 1";
    vk.appendChild(el("div", "kpi-l", "verdicts"));
    const vwrap = el("div", null); vwrap.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;margin-top:8px";
    Object.entries(t.verdicts || {}).forEach(([c, n]) => vwrap.appendChild(pill(c, n)));
    if (!Object.keys(t.verdicts || {}).length) vwrap.appendChild(el("span", "caption", "—"));
    vk.appendChild(vwrap); kpis.appendChild(vk);

    // first 6 real step rows
    const tbody = $("#hero-steps tbody");
    tbody.innerHTML = "";
    const rows = firstSteps(run, 6);
    if (!rows.length) {
      const tr = el("tr"); const td = el("td", null, "No step detail in this run."); td.colSpan = 5; tr.appendChild(td); tbody.appendChild(tr);
    } else {
      rows.forEach((s) => {
        const tr = el("tr");
        const tdI = el("td", "intent", s.intent || s.step_id); tr.appendChild(tdI);
        const tdT = el("td"); tdT.appendChild(tierBadge(s.tier)); tr.appendChild(tdT);
        const tdS = el("td"); const sp = el("span", "pill v-" + statusVerdict(s.status)); sp.textContent = s.status; tdS.appendChild(sp); tr.appendChild(tdS);
        tr.appendChild(el("td", "num", s.score != null ? s.score.toFixed(3) : "—"));
        tr.appendChild(el("td", "num", fmtInt(s.duration_ms)));
        tbody.appendChild(tr);
      });
    }
    $("#hero-label").textContent = "Latest real run · " + (run.started_at ? run.started_at.replace("T", " ").slice(0, 16) + "Z" : "—") +
      " · " + fmtInt(live.runs_total || 0) + " runs recorded";
    document.querySelector(".wordmark").classList.toggle("is-live", false);
  }
  function sumSteps(t) { return Object.values(t.tiers || {}).reduce((a, b) => a + int(b), 0) || int(t.replayed_steps) + int(t.healed_steps); }
  function firstSteps(run, n) {
    const out = [];
    for (const r of run.results || []) for (const s of r.steps || []) { out.push(s); if (out.length >= n) return out; }
    return out;
  }
  function statusVerdict(st) {
    return ({ passed: "PASS", healed: "COSMETIC_DRIFT", reordered: "COSMETIC_DRIFT", added: "INTENDED_CHANGE", skipped: "FEATURE_REMOVED", failed: "BUG" })[st] || "neutral";
  }

  /* ================================================================== */
  /* PROOF STRIP                                                         */
  /* ================================================================== */
  function renderProof(ov) {
    const host = $("#proof"); host.setAttribute("aria-busy", "false");
    const rec = (ov && ov.recorded) || {};
    host.innerHTML = "";
    const cells = [];

    // 1 — Gauntlet
    const g = (rec.gauntlet && rec.gauntlet.summary) || null;
    cells.push(proofCell({
      href: g ? "live.html" : null, title: g ? "Reproduce live (chaos / hidden-bug scenarios)" : null,
      n: g ? Math.round((g.heal_success_rate || 0) * 100) + "%" : "—",
      label: "Gauntlet heal success",
      sub: g ? Math.round((g.false_alarm_rate || 0) * 100) + "% false alarms · " + Math.round((g.bug_recall || 0) * 100) + "% bug recall (" + int(g.bug_trials || 0) + "/5) · " + fmtInt(g.steps_healed_total) + " steps healed" : "",
      cap: g ? "Offline gauntlet: " + int(g.mutation_trials) + " refactor + " + int(g.bug_trials) + " injected-bug trials, " + int(g.avg_llm_calls_per_run) + " LLM calls · " + dateOf(rec.gauntlet.measured_at) : "No gauntlet artifact",
    }));

    // 2 — 10th run LLM calls
    const tr = (rec.ten_runs && rec.ten_runs.runs) || [];
    const last = tr[tr.length - 1];
    cells.push(proofCell({
      href: tr.length ? "#ten-runs" : null, title: tr.length ? "See the 10-run curve" : null,
      n: last ? fmtInt(last.llm_calls) : "—",
      label: "LLM calls on the 10th run",
      sub: tr.length ? fmtInt(tr.reduce((a, r) => a + int(r.llm_calls), 0)) + " calls across all " + tr.length + " runs" : "",
      cap: tr.length ? "ten_runs v1.0→v1.2, LLM off, steady state · " + dateOf(rec.ten_runs.measured_at) : "No ten-runs artifact",
    }));

    // 3 — SauceDemo real-world
    const sd = rec.saucedemo && rec.saucedemo.users;
    let sdCorrect = null, sdBuggy = 0, sdBuggyUsers = [];
    if (sd && sd.verdicts) {
      const v = sd.verdicts;
      sdCorrect = v.standard_user ? Object.values(v.standard_user).filter((x) => x === "BUG").length : null;
      ["problem_user", "error_user"].forEach((u) => {
        if (v[u]) { const b = Object.values(v[u]).filter((x) => x === "BUG").length; if (b) { sdBuggy += b; sdBuggyUsers.push(u.replace("_user", "") + " " + b); } }
      });
    }
    cells.push(proofCell({
      href: null,
      n: sdCorrect != null ? String(sdCorrect) : "—",
      label: "False alarms on the correct build",
      sub: sd ? sdBuggy + " bugs caught on buggy logins (" + sdBuggyUsers.join(", ") + ")" : "",
      cap: sd ? "SauceDemo, " + Object.keys(sd.verdicts || {}).length + " users, 0 LLM calls · " + dateOf(rec.saucedemo.measured_at) : "No SauceDemo artifact",
    }));

    // 4 — Blind exam BEFORE -> AFTER
    const eb = rec.exam_before && rec.exam_before.overall, ea = rec.exam_after && rec.exam_after.overall;
    cells.push(proofCell({
      href: ea ? "#limits" : null, title: ea ? "See what it still misses" : null,
      before: eb ? Math.round((eb.accuracy || 0) * 100) : null,
      after: ea ? Math.round((ea.accuracy || 0) * 100) : null,
      n: eb && ea ? eb.correct + "/" + eb.total + " → " + ea.correct + "/" + ea.total : "—",
      label: "Blind hold-out exam",
      sub: eb && ea ? "LLM off " + Math.round(eb.accuracy * 100) + "% → LLM on " + Math.round(ea.accuracy * 100) + "% (honest: modest gain)" : "",
      cap: ea ? "MediQueue, 40 scored verdicts, unseen app · " + dateOf(rec.exam_after.measured_at) : "No exam artifact",
    }));

    cells.forEach((c) => host.appendChild(c));
  }
  function proofCell(o) {
    const node = o.href ? el("a", "proof-cell") : el("div", "proof-cell");
    if (o.href) { node.href = o.href; if (o.title) node.title = o.title; }
    if (o.before != null && o.after != null) {
      const n = el("div", "proof-n");
      n.appendChild(el("span", null, o.before + "%"));
      n.appendChild(el("span", "proof-arrow", " → "));
      const af = el("span", null, o.after + "%"); af.style.color = "var(--accent)"; n.appendChild(af);
      node.appendChild(n);
    } else {
      node.appendChild(el("div", "proof-n", o.n));
    }
    node.appendChild(el("div", "proof-l", o.label));
    if (o.sub) node.appendChild(el("div", "proof-c", o.sub));
    node.appendChild(el("div", "proof-c", o.cap));
    return node;
  }

  /* ================================================================== */
  /* CASCADE — inline SVG, real tier shares                              */
  /* ================================================================== */
  const CASCADE = [
    { t: "0", code: "T0", word: "Replay", cost: "$0" },
    { t: "1", code: "T1", word: "Heal", cost: "$0" },
    { t: "2", code: "T2", word: "Reorder", cost: "$0" },
    { t: "3", code: "T3", word: "LLM heal", cost: "~300 tok" },
    { t: "4", code: "T4", word: "Replan", cost: "$0 / ~2k" },
    { t: "5", code: "T5", word: "Vision", cost: "VLM only" },
  ];
  let cascadeTiers = null, cascadeTotal = 0, cascadeRuns = 0;
  const TIER_FILL = ["#0E8A5F", "#0A7C86", "#0A7C86", "#1F4DFF", "#1F4DFF", "#7A5AB8"];
  function drawCascade() {
    const host = $("#cascade"); if (!host || !cascadeTiers) return;
    host.setAttribute("aria-busy", "false");
    host.innerHTML = "";
    const pad = 24, W = Math.max(240, host.clientWidth - pad * 2);
    const wide = W >= 520;
    const shares = CASCADE.map((s) => int(cascadeTiers[s.t]));
    const maxShare = Math.max(1, ...shares);
    let H, parts = [];
    if (wide) {
      H = 184; const gap = W >= 760 ? 14 : 8, nw = (W - gap * 5) / 6, small = nw < 96;
      CASCADE.forEach((s, i) => {
        const x = i * (nw + gap), sh = shares[i], p = pct(sh, cascadeTotal);
        parts.push(`<g transform="translate(${x.toFixed(1)},0)">`);
        parts.push(`<rect x="0" y="0" width="${nw.toFixed(1)}" height="136" rx="10" fill="#F7F8FA" stroke="#E6E8EC"/>`);
        parts.push(`<text x="10" y="24" font-family="Geist Mono, monospace" font-size="12" font-weight="500" fill="#0B0D12">${esc(s.code)}</text>`);
        parts.push(`<text x="10" y="42" font-family="Schibsted Grotesk, sans-serif" font-size="${small ? 12 : 13}" font-weight="500" fill="#262A33">${esc(s.word)}</text>`);
        parts.push(`<text x="10" y="58" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">${esc(s.cost)}</text>`);
        const bw = Math.max(2, (nw - 20) * (sh / maxShare));
        parts.push(`<rect x="10" y="70" width="${(nw - 20).toFixed(1)}" height="8" rx="4" fill="#E6E8EC"/>`);
        parts.push(`<rect x="10" y="70" width="${bw.toFixed(1)}" height="8" rx="4" fill="${TIER_FILL[i]}"/>`);
        parts.push(`<text x="10" y="108" font-family="Geist Mono, monospace" font-size="${small ? 17 : 20}" font-weight="500" fill="#0B0D12">${p}%</text>`);
        parts.push(`<text x="10" y="124" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">${fmtInt(sh)} steps</text>`);
        parts.push(`</g>`);
        if (i < 5) {
          const ax = x + nw + 1, ay = 68, al = gap - 2;
          parts.push(`<path d="M${ax.toFixed(1)} ${ay} l${Math.max(1, al - 4).toFixed(1)} 0" stroke="#D5D9E0" stroke-width="1.5"/>`);
          parts.push(`<path d="M${(ax + Math.max(1, al - 4)).toFixed(1)} ${ay - 3} l4 3 l-4 3 z" fill="#D5D9E0"/>`);
        }
      });
      parts.push(`<text x="0" y="${H - 12}" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">$0 —————————————————————————————— tokens ——————————————————————————————&gt;</text>`);
    } else {
      const rowH = 40; H = CASCADE.length * rowH + 8;
      CASCADE.forEach((s, i) => {
        const y = i * rowH + 4, sh = shares[i], p = pct(sh, cascadeTotal);
        const trackW = Math.max(40, W - 150), bw = Math.max(2, trackW * (sh / maxShare));
        parts.push(`<text x="0" y="${y + 14}" font-family="Geist Mono, monospace" font-size="12" font-weight="500" fill="#0B0D12">${esc(s.code)}</text>`);
        parts.push(`<text x="0" y="${y + 28}" font-family="Schibsted Grotesk, sans-serif" font-size="12" fill="#5C6370">${esc(s.word)}</text>`);
        parts.push(`<rect x="96" y="${y + 8}" width="${trackW}" height="8" rx="4" fill="#E6E8EC"/>`);
        parts.push(`<rect x="96" y="${y + 8}" width="${bw.toFixed(1)}" height="8" rx="4" fill="${TIER_FILL[i]}"/>`);
        parts.push(`<text x="${(104 + trackW).toFixed(1)}" y="${y + 16}" font-family="Geist Mono, monospace" font-size="12" fill="#262A33">${p}%</text>`);
        if (i < CASCADE.length - 1) parts.push(`<path d="M4 ${y + rowH - 2} l0 4" stroke="#D5D9E0" stroke-width="1.5"/>`);
      });
    }
    host.innerHTML = `<svg class="cascade-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Resolution cascade with real step shares">${parts.join("")}</svg>`;
    $("#cascade-cap").textContent = "Bars show each tier's real share across " + fmtInt(cascadeRuns) +
      " recorded runs (" + fmtInt(cascadeTotal) + " steps). Tier 4 includes T4a deterministic field completion ($0) and T4 LLM replan (~2k tokens); only T3–T5 spend tokens.";
  }

  /* ================================================================== */
  /* VERDICT CARDS — INTENDED_CHANGE + BUG from a recorded run           */
  /* ================================================================== */
  function renderVerdictCards(run) {
    const host = $("#verdict-cards"); host.setAttribute("aria-busy", "false"); host.innerHTML = "";
    const results = (run && run.results) || [];
    const intended = results.find((r) => r.verdict && r.verdict.category === "INTENDED_CHANGE" && (r.verdict.changelog_refs || []).length);
    const bug = results.find((r) => r.verdict && r.verdict.category === "BUG" && r.bug_report);

    host.appendChild(intended ? intendedCard(intended) : missingCard("INTENDED_CHANGE", "No intended change with a citation in the latest run."));
    host.appendChild(bug ? bugCard(bug, run) : missingCard("BUG", "No bug with a repro in the latest run."));
  }
  function missingCard(cat, msg) {
    const c = el("div", "card vcard");
    const top = el("div", "vcard-top"); top.appendChild(pill(cat)); c.appendChild(top);
    const b = el("div", "vcard-body"); b.appendChild(el("p", "muted t-14", msg)); c.appendChild(b);
    return c;
  }
  function intendedCard(r) {
    const v = r.verdict;
    const c = el("div", "card vcard");
    const top = el("div", "vcard-top");
    top.appendChild(pill("INTENDED_CHANGE"));
    top.appendChild(el("span", "console-title", r.test_name || r.test_id));
    const act = el("span", "chip chip-mono"); act.style.marginLeft = "auto"; act.textContent = v.action || "—"; top.appendChild(act);
    c.appendChild(top);
    const b = el("div", "vcard-body");
    b.appendChild(el("p", "t-14", v.rationale || ""));
    (v.changelog_refs || []).slice(0, 2).forEach((q) => {
      const quote = el("div", "quote");
      quote.appendChild(el("span", "q-l", "release note · verified verbatim"));
      quote.appendChild(el("span", null, "“" + q + "”"));
      b.appendChild(quote);
    });
    const meta = el("p", "caption"); meta.textContent = "decided by " + (v.decided_by || "rules") + " · confidence " + (v.confidence != null ? v.confidence.toFixed(2) : "—") + " · test updated to v" + (r.updated_to_version || (r.test_version + 1));
    b.appendChild(meta);
    c.appendChild(b);
    return c;
  }
  function bugCard(r, run) {
    const v = r.verdict;
    const c = el("div", "card vcard");
    const top = el("div", "vcard-top");
    top.appendChild(pill("BUG"));
    top.appendChild(el("span", "console-title", r.test_name || r.test_id));
    c.appendChild(top);
    const b = el("div", "vcard-body");
    b.appendChild(el("p", "t-14", v.rationale || ""));

    // repro steps parsed from the real bug_report markdown
    const steps = parseRepro(r.bug_report || "");
    if (steps.length) {
      const ol = el("ol");
      steps.forEach((s) => ol.appendChild(el("li", null, s)));
      const wrap = el("div", "repro"); const lbl = el("div", "eyebrow"); lbl.textContent = "REPRODUCE"; lbl.style.marginBottom = "6px";
      wrap.appendChild(lbl); wrap.appendChild(ol); b.appendChild(wrap);
    } else {
      const pre = el("div", "bugrep", r.bug_report || ""); b.appendChild(pre);
    }
    // evidence thumbnail
    const shot = firstShot(r);
    const th = el("div", "thumb");
    const src = shot && !MOCK ? "/api/runs/" + run.run_id + "/shots/" + shot : "";
    if (src) {
      const img = el("img"); img.alt = "Evidence screenshot " + shot; img.loading = "lazy"; img.src = src;
      img.onerror = function () { th.textContent = "screenshot " + shot + " — not available offline"; img.remove(); };
      th.appendChild(img);
    } else {
      th.textContent = shot ? "evidence: " + shot + " — capture not shipped with mock data" : "no screenshot in this run";
    }
    b.appendChild(th);
    c.appendChild(b);
    return c;
  }
  function parseRepro(md) {
    const out = [];
    const lines = String(md).split(/\r?\n/);
    let on = false;
    for (const ln of lines) {
      if (/reproduce/i.test(ln)) { on = true; continue; }
      if (on) {
        const m = ln.match(/^\s*\d+\.\s+(.*)$/);
        if (m) out.push(m[1].trim());
        else if (out.length && ln.trim() === "") break;
        else if (out.length && /^\s*\*\*/.test(ln)) break;
      }
    }
    return out;
  }
  function firstShot(r) {
    for (const s of r.steps || []) if (s.screenshot) return s.screenshot.split("/").pop();
    return null;
  }

  /* ================================================================== */
  /* 10-RUN CHART — inline SVG                                           */
  /* ================================================================== */
  let tenRuns = null;
  function drawTenRuns() {
    const host = $("#tenrun-chart"); if (!host || !tenRuns || !tenRuns.length) return;
    host.setAttribute("aria-busy", "false"); host.innerHTML = "";
    const W = Math.max(280, host.clientWidth), H = 260;
    const mL = 40, mR = 16, mT = 16, mB = 34;
    const pw = W - mL - mR, ph = H - mT - mB;
    const n = tenRuns.length;
    const x = (i) => mL + (pw * (i + 0.5)) / n;
    const yPct = (p) => mT + ph - (ph * p) / 100;
    const bw = Math.min(28, (pw / n) * 0.5);
    let g = [];
    // gridlines + y labels (T0 %)
    [0, 25, 50, 75, 100].forEach((p) => {
      const y = yPct(p);
      g.push(`<line x1="${mL}" y1="${y.toFixed(1)}" x2="${(W - mR).toFixed(1)}" y2="${y.toFixed(1)}" stroke="#E6E8EC" stroke-width="1"/>`);
      g.push(`<text x="${mL - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">${p}</text>`);
    });
    // release markers (v1.1 at run 3, v1.2 at run 6 -> where version changes)
    const marks = [];
    for (let i = 1; i < n; i++) if (tenRuns[i].version !== tenRuns[i - 1].version) marks.push({ i, v: tenRuns[i].version });
    marks.forEach((m) => {
      const mx = mL + (pw * m.i) / n;
      g.push(`<line x1="${mx.toFixed(1)}" y1="${mT}" x2="${mx.toFixed(1)}" y2="${(mT + ph).toFixed(1)}" stroke="#D5D9E0" stroke-width="1" stroke-dasharray="4 4"/>`);
      g.push(`<text x="${(mx + 5).toFixed(1)}" y="${mT + 12}" font-family="Geist Mono, monospace" font-size="10" fill="#5C6370">v${esc(m.v)}</text>`);
    });
    // bars: LLM calls (scaled to max>=1 so 0 shows as a flat baseline tick)
    const maxCalls = Math.max(1, ...tenRuns.map((r) => int(r.llm_calls)));
    tenRuns.forEach((r, i) => {
      const c = int(r.llm_calls);
      const h = c > 0 ? Math.max(3, (c / maxCalls) * 40) : 0;
      const bx = x(i) - bw / 2, by = mT + ph - h;
      if (c > 0) g.push(`<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="#D63B3B"/>`);
      else g.push(`<rect x="${bx.toFixed(1)}" y="${(mT + ph - 2).toFixed(1)}" width="${bw.toFixed(1)}" height="2" rx="1" fill="#0E8A5F"/>`);
    });
    // line: % steps replayed at T0
    const pts = tenRuns.map((r, i) => [x(i), yPct(pct(int(r.t0_replayed), int(r.steps)))]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    g.push(`<path d="${d}" fill="none" stroke="#1F4DFF" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`);
    pts.forEach((p, i) => g.push(`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3" fill="#fff" stroke="#1F4DFF" stroke-width="2"><title>run ${tenRuns[i].run}: ${pct(int(tenRuns[i].t0_replayed), int(tenRuns[i].steps))}% replayed at T0, ${int(tenRuns[i].llm_calls)} LLM calls</title></circle>`));
    // x labels
    tenRuns.forEach((r, i) => {
      if (n > 12 && i % 2) return;
      g.push(`<text x="${x(i).toFixed(1)}" y="${(mT + ph + 18).toFixed(1)}" text-anchor="middle" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">${int(r.run)}</text>`);
    });
    g.push(`<text x="${mL}" y="${H - 4}" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">run #</text>`);
    g.push(`<text x="${mL - 34}" y="${mT - 4}" font-family="Geist Mono, monospace" font-size="10" fill="#8A919E">%T0</text>`);
    host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Ten runs: LLM calls stay at zero while the share of steps replayed at T0 stays high">${g.join("")}</svg>`;

    $("#tenrun-legend").innerHTML =
      `<span><i style="background:#0E8A5F"></i>LLM calls = 0 (every run)</span>` +
      `<span><i style="background:#1F4DFF"></i>% steps replayed at T0</span>` +
      `<span><i style="background:#D5D9E0"></i>release marker</span>`;
    const naive = tenRuns.reduce((a, r) => a + int(r.naive_tokens_estimate), 0);
    const actual = tenRuns.reduce((a, r) => a + int(r.llm_calls), 0);
    $("#tenrun-cap").textContent = "Across " + n + " runs Argus made " + actual + " LLM calls. A naive LLM-per-action agent would have spent ~" +
      fmtInt(naive) + " tokens on the same runs (estimate). Source: " + ((tenRunsSrc && tenRunsSrc) || "ten_runs.json");
  }
  let tenRunsSrc = "";

  /* ================================================================== */
  /* TERMINAL + MCP — reconstructed from the latest real RunReport       */
  /* ================================================================== */
  function renderTerminal(run) {
    const host = $("#terminal"); host.setAttribute("aria-busy", "false"); host.innerHTML = "";
    if (!run || !run.totals) { host.appendChild(el("div", "dim", "No recorded run to display.")); return; }
    const pre = el("pre");
    const add = (text, cls) => { const s = el("span", cls || null); s.textContent = text; pre.appendChild(s); pre.appendChild(document.createTextNode("\n")); };
    add("$ argus run --build 1.2 --home /data/.argus-live", "cmd");
    add("──────────────── Argus run  " + (run.label || "") + " ────────────────", "dim");
    const TIER = { 0: "T0", 1: "T1", 2: "T2", 3: "T3", 4: "T4", 5: "T5" };
    const COLOR = { PASS: "ok", COSMETIC_DRIFT: "info", INTENDED_CHANGE: "info", FEATURE_REMOVED: "dim", BUG: "err", NEEDS_REVIEW: "warn", INFRA: "warn", PRECONDITION_FAILURE: "warn" };
    (run.results || []).forEach((r) => {
      const v = r.verdict || {};
      const tc = {};
      (r.steps || []).forEach((s) => { if (s.tier != null) { const k = TIER[s.tier] || s.tier; tc[k] = (tc[k] || 0) + 1; } });
      const calls = (r.llm_calls || []).filter((c) => !c.cached).length;
      const extra = r.updated_to_version ? " -> v" + r.updated_to_version : "";
      const cat = (v.category || "").padEnd(16, " ");
      const line = el("span");
      const c1 = el("span", COLOR[v.category] || null, cat);
      line.appendChild(c1);
      line.appendChild(document.createTextNode(" " + (r.test_name || r.test_id).padEnd(40, " ") + " " + JSON.stringify(tc).replace(/"/g, "") + "  llm=" + calls + "  " + ((r.duration_ms || 0) / 1000).toFixed(1) + "s" + extra));
      pre.appendChild(line); pre.appendChild(document.createTextNode("\n"));
      if (v.category !== "PASS" && v.rationale) add("      " + v.rationale.slice(0, 200), "dim");
      (v.changelog_refs || []).slice(0, 2).forEach((q) => { const s = el("span"); s.appendChild(el("span", "info", "      cites: ")); s.appendChild(document.createTextNode('"' + q.slice(0, 110) + '"')); pre.appendChild(s); pre.appendChild(document.createTextNode("\n")); });
    });
    const t = run.totals, steps = sumSteps(t);
    const saved = Math.max(0, int(t.naive_tokens_estimate) - int(t.tokens_in) - int(t.tokens_out));
    add("", null);
    add("tests                " + t.tests + "    verdicts        " + Object.entries(t.verdicts || {}).map(([k, v]) => k + ":" + v).join(", "), "dim");
    add("steps replayed (T0)  " + t.replayed_steps + "/" + steps + "    self-healed     " + t.healed_steps, "dim");
    add("LLM calls            " + t.llm_calls + " (+" + t.llm_calls_cached + " cached)    tokens          " + fmtInt(int(t.tokens_in) + int(t.tokens_out)), "dim");
    add("cost                 " + fmtUsd(t.cost_usd) + " (list " + fmtUsd(t.list_cost_usd) + ")    tokens avoided  " + fmtInt(saved) + " (" + pct(saved, int(t.naive_tokens_estimate)) + "%)", "dim");
    add("duration             " + ((t.duration_ms || 0) / 1000).toFixed(1) + "s    run             " + run.run_id, "dim");
    (run.results || []).forEach((r) => { if (r.bug_report) { add("", null); r.bug_report.split(/\r?\n/).forEach((l) => add(l, /BUG/.test(l) ? "err" : "dim")); } });
    host.appendChild(pre);
  }
  const MCP_TOOLS = [
    ["run_tests", "Run the full suite and return verdicts per test (JSON)."],
    ["verify_change", "Verify a change you just made; description is your intent (like a PR description)."],
    ["explore_and_generate", "For an app with no tests: explore it and generate a baselined suite."],
    ["last_report", "Verdicts of the most recent run."],
  ];
  function renderMCP(run) {
    const tools = $("#mcp-tools"); tools.innerHTML = "";
    MCP_TOOLS.forEach(([n, d]) => { const t = el("div", "tool"); t.appendChild(el("code", null, n)); t.appendChild(el("span", null, d)); tools.appendChild(t); });
    const host = $("#mcp-json"); host.setAttribute("aria-busy", "false");
    if (!run) { host.textContent = "// no recorded run"; return; }
    const t = run.totals;
    const digest = {
      run_id: run.run_id, verdicts: t.verdicts, llm_calls: t.llm_calls, healed_steps: t.healed_steps,
      tests: (run.results || []).map((r) => ({ test: r.test_name, verdict: r.verdict.category, confidence: r.verdict.confidence, why: (r.verdict.rationale || "").slice(0, 120), changelog_refs: r.verdict.changelog_refs || [], bug_report: r.bug_report ? r.bug_report.slice(0, 80) + "…" : null })),
    };
    host.textContent = "// tools/call verify_change -> result\n" + JSON.stringify(digest, null, 2);
  }

  /* ================================================================== */
  /* LIMITS — from the blind exam failure list                          */
  /* ================================================================== */
  function renderLimits(ov) {
    const host = $("#limits-list"); host.setAttribute("aria-busy", "false"); host.innerHTML = "";
    const after = (ov && ov.recorded && ov.recorded.exam_after) || {};
    const failures = after.failures || [];
    const bullets = [];
    if (failures.length) {
      // group by weakness
      const by = {};
      failures.forEach((f) => { const w = f.weakness || "unclassified"; (by[w] = by[w] || []).push(f); });
      const ranked = Object.entries(by).sort((a, b) => b[1].length - a[1].length);
      const WEAK = {
        "target not found on the page": ["Elements hidden behind a once-per-session overlay or rendered below the fold of a virtualised list can be missed.", "Argus then reports NEEDS_REVIEW instead of guessing."],
        "overlay / confirmation dialog": ["A click-blocking intro or confirmation overlay can hide the real target on first contact.", "It needs a dismiss-then-retry pass."],
        "UI lies (backend failure masked)": ["When the UI reports success while the save silently fails, the oracle must fire; if the element can't be located, the bug slips through.", "Step contracts catch the 5xx, but only once the step is reached."],
      };
      ranked.slice(0, 3).forEach(([w, fs]) => {
        const ex = fs[0];
        const known = WEAK[w];
        bullets.push(known ? known[0] + " (" + fs.length + " case" + (fs.length > 1 ? "s" : "") + ", e.g. " + ex.release + "/" + ex.intent + ")"
          : w + " (" + fs.length + " case" + (fs.length > 1 ? "s" : "") + ")");
      });
      const budget = failures.filter((f) => /Budget|Unavailable/i.test(f.rationale || "")).length;
      if (budget) bullets.push("When the free-tier LLM budget is exhausted mid-run, ambiguous steps fall back to NEEDS_REVIEW for a human (" + budget + " of " + failures.length + " exam misses).");
    }
    const gen = after.criteria && after.criteria.Generation;
    if (gen && (gen.coverage === 0 || (gen.baseline_success && gen.baseline_success.authored === 0))) {
      bullets.push("Zero-knowledge generation (explore + generate) baselined " + int(gen.authored) + " of " + int(gen.intents) + " journeys on this hold-out — authoring still needs the login wall and delayed renders handled.");
    }
    if (!bullets.length) bullets.push("No exam failure data available.");
    bullets.slice(0, 5).forEach((b) => { const li = el("li"); li.appendChild(el("span", "marker", "×")); li.appendChild(el("span", null, b)); host.appendChild(li); });
  }

  /* ================================================================== */
  /* repo links + footer stamp                                          */
  /* ================================================================== */
  function wireRepo(ov) {
    const meta = document.querySelector('meta[name="argus-repo"]');
    const url = meta && meta.content && meta.content.trim();
    $$("[data-repo-link]").forEach((a) => {
      if (url) { a.href = url; a.removeAttribute("aria-disabled"); a.title = "Source repository"; a.target = "_blank"; a.rel = "noopener"; }
    });
    const live = (ov && ov.live) || {};
    const stamp = $("#foot-stamp");
    if (stamp) stamp.textContent = "Recorded data generated " + dateOf(ov && ov.generated_at) + " · " + fmtInt(live.runs_total || 0) + " live runs · last " + relTime(live.last_run_at);
  }

  /* ================================================================== */
  /* BOOT                                                               */
  /* ================================================================== */
  function emptyAll(msg) {
    ["#hero-frame", "#proof", "#cascade", "#verdict-cards", "#tenrun-chart", "#terminal", "#mcp-json", "#limits-list"].forEach((s) => {
      const h = $(s); if (h) h.setAttribute("aria-busy", "false");
    });
    const p = $("#proof"); if (p) { p.innerHTML = ""; const e = el("div", "empty"); e.style.gridColumn = "1/-1"; e.appendChild(el("div", "empty-h", "No recorded data")); e.appendChild(el("div", null, msg)); p.appendChild(e); }
  }

  async function boot() {
    if (MOCK) {
      document.body.classList.add("is-mock");
      const cta = document.querySelector(".nav-cta");
      const flag = el("span", "mock-flag", "MOCK DATA");
      (cta ? cta.parentNode : document.body).insertBefore(flag, cta || null);
    }
    let ov = null, run = null;
    try { ov = await getJSON("/api/overview", "overview.json"); } catch (e) { ov = null; }
    try { run = await getJSON("/api/runs/latest", "runs_latest.json"); } catch (e) { run = null; }
    if (!ov && !run) { emptyAll("The API is unreachable. Start the web server, or add ?mock=1 to preview with recorded data."); wireRepo(null); return; }

    const rec = (ov && ov.recorded) || {};
    // hero
    renderHero(run, ov);
    // proof
    renderProof(ov);
    // cascade
    cascadeTiers = (ov && ov.live && ov.live.tiers) || null;
    cascadeTotal = cascadeTiers ? Object.values(cascadeTiers).reduce((a, b) => a + int(b), 0) : 0;
    cascadeRuns = (ov && ov.live && ov.live.runs_total) || 0;
    if (cascadeTiers && cascadeTotal) drawCascade(); else { const h = $("#cascade"); h.setAttribute("aria-busy", "false"); h.innerHTML = ""; h.appendChild(el("div", "empty", null, "No tier data yet — run the suite to populate the cascade.")); }
    // verdicts
    renderVerdictCards(run);
    // ten runs
    tenRuns = (rec.ten_runs && rec.ten_runs.runs) || null;
    tenRunsSrc = (rec.ten_runs && rec.ten_runs.source) || "";
    if (tenRuns && tenRuns.length) drawTenRuns(); else { const h = $("#tenrun-chart"); h.setAttribute("aria-busy", "false"); h.innerHTML = ""; h.appendChild(el("div", "empty", null, "No ten-run data recorded.")); }
    // terminal + mcp
    renderTerminal(run); renderMCP(run);
    // limits
    renderLimits(ov);
    // repo + footer
    wireRepo(ov);

    // responsive redraw
    let raf = null;
    window.addEventListener("resize", () => { if (raf) cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { drawCascade(); drawTenRuns(); }); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
