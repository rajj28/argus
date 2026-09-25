/* Argus live console — consumes the job SSE stream (WEB_SPEC) and renders it.
   Real mode: POST /api/jobs then EventSource /api/jobs/{id}/events.
   Mock mode (?mock=1): plays back assets/mock/events.json (a real recorded run) and
   labels the page MOCK DATA. Every value is bound to an event/API field. */
(function () {
  "use strict";

  const MOCK = new URLSearchParams(location.search).get("mock") === "1";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  /* ---------- helpers ---------- */
  function el(tag, cls, txt) { const n = document.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; }
  const int = (n) => (Number.isFinite(+n) ? Math.round(+n) : 0);
  const pct = (a, b) => (b > 0 ? Math.round((a / b) * 100) : 0);
  const fmtInt = (n) => int(n).toLocaleString("en-US");
  const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed(4);
  function fmtElapsed(ms) {
    const s = Math.max(0, ms) / 1000;
    if (s < 60) return s.toFixed(1) + "s";
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    return m + ":" + String(r).padStart(2, "0");
  }
  async function getJSON(apiPath, mockFile) {
    const url = MOCK ? "assets/mock/" + mockFile : apiPath;
    const r = await fetch(url, { headers: { accept: "application/json" } });
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json();
  }
  const TIER_LABEL = { 0: "T0 Replay", 1: "T1 Heal", 2: "T2 Reorder", 3: "T3 LLM", 4: "T4 Replan", 5: "T5 Vision" };
  function tierBadge(t) { const b = el("span", "tier tier-" + (t == null ? "x" : t)); b.textContent = t == null ? "—" : (TIER_LABEL[t] || "T" + t); return b; }
  function verdictPill(cat) { const p = el("span", "pill v-" + (cat || "neutral")); p.textContent = cat || "?"; return p; }

  /* ---------- app state ---------- */
  const state = { job: null, es: null, timer: null, startedAt: 0, runId: null, providers: [], enrich: {}, steps: 0 };

  /* ================================================================== */
  /* TOP BAR                                                            */
  /* ================================================================== */
  function renderTopbar(sky, llm) {
    const region = (document.querySelector('meta[name="argus-region"]') || {}).content || "";
    $("#chip-env").textContent = region ? "Azure · " + region : "Azure";

    const c = $("#chip-skyops"); c.innerHTML = "";
    if (sky && sky.version) {
      const chaosOn = sky.chaos && sky.chaos.seed != null;
      const dot = el("span", "dot " + (chaosOn ? "warn" : "ok")); c.appendChild(dot);
      const bugs = Array.isArray(sky.bugs) ? sky.bugs.length : 0;
      c.appendChild(el("span", null, "SkyOps v" + sky.version + " · chaos " + (chaosOn ? "on" : "off") + (bugs ? " · " + bugs + " bugs" : "")));
    } else { c.appendChild(el("span", "dot off")); c.appendChild(el("span", null, "SkyOps · unreachable")); }

    const skyUrl = (document.querySelector('meta[name="argus-skyops-url"]') || {}).content || "";
    const link = $("#link-skyops");
    if (skyUrl) { link.href = skyUrl; link.removeAttribute("aria-disabled"); link.title = "Open the SkyOps app"; link.target = "_blank"; link.rel = "noopener"; }

    const mesh = $("#mesh"); mesh.innerHTML = "";
    (llm || []).forEach((p) => {
      const remaining = Math.max(0, int(p.cap_per_hour) - int(p.calls_this_hour));
      const chip = el("span", "chip chip-mono");
      const dot = el("span", "dot " + (!p.available ? "off" : p.exhausted ? "warn" : "ok"));
      chip.appendChild(dot);
      chip.appendChild(el("span", null, p.name));
      chip.appendChild(el("span", "faint", remaining + "/" + int(p.cap_per_hour)));
      chip.title = p.name + ": " + int(p.calls_this_hour) + " calls this hour, " + remaining + " remaining" + (p.exhausted ? " (exhausted)" : "");
      mesh.appendChild(chip);
    });
    if (!(llm || []).length) mesh.appendChild(el("span", "chip", "LLM mesh · —"));
    state.providers = llm || [];
    renderProviderMeter();
  }
  function renderProviderMeter() {
    const host = $("#m-prov"); host.innerHTML = "";
    if (!state.providers.length) { host.appendChild(el("div", "prov", "No provider data")); return; }
    state.providers.forEach((p) => {
      const row = el("div", "prov");
      const left = el("span", "pn");
      left.appendChild(el("span", "dot " + (!p.available ? "off" : p.exhausted ? "warn" : "ok")));
      left.appendChild(el("span", null, p.name));
      row.appendChild(left);
      row.appendChild(el("span", "pv", int(p.calls_this_hour) + " / " + int(p.cap_per_hour) + " hr"));
      host.appendChild(row);
    });
  }

  /* ================================================================== */
  /* SCENARIOS                                                          */
  /* ================================================================== */
  function renderScenarios(list) {
    const host = $("#scenarios"); host.setAttribute("aria-busy", "false"); host.innerHTML = "";
    if (!list || !list.length) { host.appendChild(el("div", "empty", null, "No scenarios available.")); return; }
    list.forEach((s) => {
      const b = el("button", "scn-item");
      b.type = "button"; b.dataset.scenario = s.id;
      const t = el("div", "scn-t"); t.appendChild(el("span", null, s.title || s.id));
      if (s.uses_llm) t.appendChild(el("span", "scn-llm", "LLM"));
      b.appendChild(t);
      b.appendChild(el("div", "scn-d", s.description || ""));
      b.appendChild(el("div", "scn-c", s.expected_cost || ""));
      b.addEventListener("click", () => startJob(s.id, b));
      host.appendChild(b);
    });
  }
  function setScenariosEnabled(on) {
    $$("#scenarios .scn-item").forEach((b) => { b.disabled = !on; });
  }

  /* ================================================================== */
  /* JOB START                                                          */
  /* ================================================================== */
  async function startJob(scenario, btn) {
    if (state.job && (state.job.state === "running" || state.job.state === "queued")) return;
    $$("#scenarios .scn-item").forEach((b) => b.removeAttribute("aria-current"));
    if (btn) btn.setAttribute("aria-current", "true");
    resetRunView(scenario);
    setScenariosEnabled(false);

    if (MOCK) { startMockPlayback(scenario); return; }

    try {
      const r = await fetch("/api/jobs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ scenario }) });
      if (r.status === 429) {
        const j = await r.json().catch(() => ({}));
        $("#queue-note").textContent = "Rate limited — retry in " + (j.retry_after || "?") + "s";
        setState("idle"); setScenariosEnabled(true); return;
      }
      if (!r.ok) throw new Error("POST /api/jobs -> " + r.status);
      const j = await r.json();
      state.job = { id: j.job_id, scenario, state: "queued", queue: j.queue_position || 0 };
      $("#rh-id").textContent = j.job_id + (j.queue_position ? " · queue #" + j.queue_position : "");
      if (j.queue_position > 0) { $("#queue-note").textContent = "Queue position " + j.queue_position; setState("queued"); }
      openStream(j.job_id);
    } catch (e) {
      $("#queue-note").textContent = "Could not start job (API unreachable).";
      setState("failed"); setScenariosEnabled(true);
    }
  }

  function openStream(jobId) {
    if (state.es) { try { state.es.close(); } catch (_) {} }
    const es = new EventSource("/api/jobs/" + jobId + "/events");
    state.es = es;
    es.onmessage = (m) => { let evt; try { evt = JSON.parse(m.data); } catch (_) { return; } handleEvent(evt); };
    es.onerror = () => { if (state.job && state.job.state !== "done" && state.job.state !== "failed") finalize("failed"); };
  }

  /* ---------- mock playback ---------- */
  let mockTimer = null;
  async function startMockPlayback(scenario) {
    let events = [];
    try { events = await getJSON("/api/jobs/mock/events", "events.json"); } catch (e) { events = []; }
    state.job = { id: "job-mock-" + Date.now().toString(36), scenario, state: "queued", queue: 0 };
    $("#rh-id").textContent = state.job.id + " · mock";
    // enrich candidate tables from the recorded report
    try { enrichFromReport(await getJSON("/api/runs/latest", "runs_latest.json")); } catch (e) {}
    if (!events.length) { addLog("No recorded events to play (mock).", "dim"); finalize("failed"); return; }
    let i = 0;
    const pace = (evt) => ({ state: 220, log: 34, step: 80, shot: 110, verdict: 200, mcp: 140, meters: 120 }[evt.type] || 60);
    const tick = () => {
      if (i >= events.length) { finalize("done"); return; }
      const evt = events[i++];
      handleEvent(evt);
      mockTimer = setTimeout(tick, pace(evt));
    };
    tick();
  }

  /* ================================================================== */
  /* EVENT HANDLER (shared by real SSE + mock playback)                 */
  /* ================================================================== */
  function handleEvent(evt) {
    if (!evt || !evt.type) return;
    addEventRaw(evt);
    switch (evt.type) {
      case "state": onState(evt); break;
      case "log": addLog(evt.line, evt.stream === "stderr" ? "err" : null); break;
      case "shot": addShot(evt); break;
      case "step": addStep(evt); break;
      case "verdict": addVerdict(evt); break;
      case "mcp": addMcp(evt); break;
      case "meters": updateMeters(evt); break;
      default: break;
    }
  }

  function onState(evt) {
    const s = evt.state || "running";
    if (state.job) state.job.state = s;
    setState(s);
    if (s === "running") {
      $("#empty-state").hidden = true; $("#run-view").hidden = false;
      if (!state.startedAt) { state.startedAt = Date.now(); startTimer(); }
      setScenariosEnabled(false);
    } else if (s === "queued") {
      $("#queue-note").textContent = "Queued…";
    } else if (s === "done" || s === "failed") {
      finalize(s);
    }
  }
  function setState(s) {
    const b = $("#rh-state");
    b.className = "state-badge " + (s === "running" ? "running" : s === "done" ? "done" : s === "failed" ? "failed" : s === "queued" ? "queued" : "");
    b.textContent = s || "idle";
    document.querySelector(".wordmark").classList.toggle("is-live", s === "running" || s === "queued");
  }
  function finalize(s) {
    if (mockTimer) { clearTimeout(mockTimer); mockTimer = null; }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    if (state.es) { try { state.es.close(); } catch (_) {} state.es = null; }
    if (state.job) state.job.state = s;
    setState(s);
    $("#queue-note").textContent = s === "done" ? "Done — pick another scenario" : s === "failed" ? "Job ended — pick another scenario" : "One job at a time · FIFO queue";
    setScenariosEnabled(true);
    // real mode: enrich steps with candidate tables now that report.json exists
    if (!MOCK && s === "done" && state.runId) {
      getJSON("/api/runs/" + state.runId, null).then(enrichFromReport).then(rebuildStepDetails).catch(() => {});
    }
  }
  function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(() => {
      const e = Date.now() - state.startedAt;
      $("#rh-timer").textContent = fmtElapsed(e);
      $("#m-elapsed").textContent = fmtElapsed(e);
    }, 100);
  }

  /* ---------- reset run view for a new job ---------- */
  function resetRunView(scenario) {
    state.startedAt = 0; state.runId = null; state.enrich = {}; state.steps = 0;
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    $("#rh-title").textContent = scenarioLabel(scenario);
    $("#rh-id").textContent = "";
    $("#rh-timer").textContent = "0.0s";
    $("#filmstrip").innerHTML = "";
    $("#steplist").innerHTML = "";
    $("#verdicts").innerHTML = "";
    $("#term-out").textContent = "";
    $("#tab-mcp").innerHTML = "";
    $("#tab-events").innerHTML = "";
    ["#m-steps", "#m-replayed", "#m-healed", "#m-llm", "#m-tokens", "#m-cost", "#m-list", "#m-avoided"].forEach((s) => $(s).textContent = "—");
    $("#m-elapsed").textContent = "0.0s";
    $("#empty-state").hidden = true; $("#run-view").hidden = false;
    setState("queued");
  }
  function scenarioLabel(id) {
    const b = $$("#scenarios .scn-item").find((x) => x.dataset.scenario === id);
    return b ? (b.querySelector(".scn-t span").textContent) : (id || "Run view");
  }

  /* ---------- terminal / events / mcp ---------- */
  function addLog(line, cls) {
    const out = $("#term-out");
    const s = el("span", cls || null, line == null ? "" : line);
    out.appendChild(s); out.appendChild(document.createTextNode("\n"));
    out.parentElement.scrollTop = out.parentElement.scrollHeight;
  }
  function addEventRaw(evt) {
    const host = $("#tab-events");
    host.appendChild(el("div", "evt-line", JSON.stringify(evt)));
    host.scrollTop = host.scrollHeight;
  }
  function addMcp(evt) {
    const host = $("#tab-mcp");
    const line = el("div", "frame-line " + (evt.dir === "<-" ? "in" : "out"));
    line.appendChild(el("span", "dir", (evt.dir === "<-" ? "<- " : "-> ")));
    let txt;
    try { txt = JSON.stringify(evt.frame); } catch (_) { txt = String(evt.frame); }
    line.appendChild(document.createTextNode(txt));
    host.appendChild(line); host.scrollTop = host.scrollHeight;
  }

  /* ---------- filmstrip ---------- */
  function addShot(evt) {
    const strip = $("#filmstrip");
    const name = evt.name || ("shot-" + strip.children.length);
    if (!state.runId && evt.url) { const m = String(evt.url).match(/\/api\/runs\/([^/]+)\//); if (m) state.runId = m[1]; }
    const cap = (evt.test ? evt.test + " · " : "") + name;
    const src = MOCK ? "" : (evt.url || "");
    const b = el("button", "shot"); b.type = "button";
    const imgWrap = el("div", "shot-img");
    if (src) {
      const img = el("img"); img.alt = "Screenshot " + name; img.loading = "lazy"; img.src = src;
      img.onerror = function () { imgWrap.classList.add("no-capture"); imgWrap.textContent = name.replace(/\.[^.]+$/, ""); img.remove(); };
      imgWrap.appendChild(img);
    } else {
      imgWrap.classList.add("no-capture");
      imgWrap.appendChild(el("span", "shot-ph", name.replace(/\.[^.]+$/, "")));
    }
    b.appendChild(imgWrap);
    b.appendChild(el("div", "shot-cap", cap));
    b.addEventListener("click", () => openLightbox(src, cap));
    strip.appendChild(b);
    strip.scrollLeft = strip.scrollWidth;
  }

  /* ---------- step list ---------- */
  function addStep(evt) {
    const list = $("#steplist");
    state.steps++;
    const key = (evt.test || "") + "::" + (evt.step_id || "");
    const en = state.enrich[key] || {};
    const row = el("div", "step"); row.dataset.key = key;
    const idx = el("span", "idx", String(state.steps));
    const dot = el("span", "sdot s-" + (evt.status || "passed"));
    const intent = el("span", "s-intent", evt.intent || evt.step_id || "");
    const tier = tierBadge(evt.tier);
    const score = el("span", "s-score", evt.score != null ? Number(evt.score).toFixed(3) : (en.score != null ? Number(en.score).toFixed(3) : "—"));
    const dur = el("span", "s-dur", en.duration_ms != null ? (int(en.duration_ms) + "ms") : "—");
    row.append(idx, dot, intent, tier, score, dur);
    list.appendChild(row);
    if (en.candidates) makeExpandable(row, key);
  }
  function makeExpandable(row, key) {
    row.classList.add("has-detail");
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-expanded", "false");
    const detail = el("div", "step-detail"); detail.hidden = true;
    const toggle = () => {
      const open = row.getAttribute("aria-expanded") === "true";
      row.setAttribute("aria-expanded", open ? "false" : "true");
      detail.hidden = open;
      if (!open && !detail.childElementCount) detail.appendChild(candidateTable((state.enrich[key] || {}).candidates));
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    row.insertAdjacentElement("afterend", detail);
    row._detail = detail;
  }
  function candidateTable(cands) {
    const wrap = el("div");
    const top = (cands || []).slice(0, 5);
    const t = el("table", "cands");
    t.appendChild(el("caption", null, "top-" + top.length + " candidates · dual-view score breakdown"));
    const thead = el("thead"); const htr = el("tr");
    ["#", "candidate", "score", "name", "role", "context", "position", "semantic"].forEach((h) => htr.appendChild(el("th", null, h)));
    thead.appendChild(htr); t.appendChild(thead);
    const tb = el("tbody");
    const maxScore = Math.max(...top.map((c) => Number(c.score) || 0), 0.0001);
    top.forEach((c, i) => {
      const tr = el("tr"); if (i === 0 || Number(c.score) === maxScore) tr.className = "win";
      tr.appendChild(el("td", "n", String(i + 1)));
      const d = el("td", "desc", c.desc || c.ref || ""); tr.appendChild(d);
      const sc = el("td", "n"); const bar = el("span", "bar"); const fill = el("i"); fill.style.width = Math.round((Number(c.score) / maxScore) * 100) + "%"; bar.appendChild(fill);
      sc.appendChild(bar); sc.appendChild(el("span", null, " " + (Number(c.score) || 0).toFixed(3))); tr.appendChild(sc);
      const bd = c.breakdown || {};
      ["name", "role", "context", "position", "semantic_score"].forEach((k) => tr.appendChild(el("td", "n", bd[k] != null ? Number(bd[k]).toFixed(2) : "—")));
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t);
    return wrap;
  }
  function enrichFromReport(report) {
    if (!report || !report.results) return;
    state.runId = state.runId || report.run_id;
    (report.results || []).forEach((r) => {
      (r.steps || []).forEach((s) => {
        const key = (r.test_id || "") + "::" + (s.step_id || "");
        const rec = state.enrich[key] || {};
        rec.duration_ms = s.duration_ms;
        if (s.score != null) rec.score = s.score;
        (s.observations || []).forEach((o) => {
          if (o.kind === "locator_healed" && o.evidence && o.evidence.candidates) rec.candidates = o.evidence.candidates;
        });
        state.enrich[key] = rec;
      });
    });
  }
  function rebuildStepDetails() {
    $$("#steplist .step").forEach((row) => {
      const key = row.dataset.key, en = state.enrich[key] || {};
      if (en.duration_ms != null) { const d = row.querySelector(".s-dur"); if (d) d.textContent = int(en.duration_ms) + "ms"; }
      if (en.candidates && !row.classList.contains("has-detail")) makeExpandable(row, key);
    });
  }

  /* ---------- verdicts ---------- */
  function addVerdict(evt) {
    const host = $("#verdicts");
    const card = el("div", "vlive");
    const top = el("div", "vlive-top");
    top.appendChild(verdictPill(evt.category));
    top.appendChild(el("span", "vlive-name", evt.test || ""));
    card.appendChild(top);
    const body = el("div", "vlive-body");
    if (evt.rationale) body.appendChild(el("div", "rat", evt.rationale));
    (evt.changelog_refs || []).forEach((q) => {
      const cite = el("div", "cite");
      cite.appendChild(el("div", "cite-l", "release note · verified"));
      cite.appendChild(el("div", null, "“" + q + "”"));
      body.appendChild(cite);
    });
    if (evt.bug_report) body.appendChild(mdLite(evt.bug_report));
    card.appendChild(body);
    host.appendChild(card);
  }
  function mdLite(md) {
    const box = el("div", "bugrep");
    let list = null;
    String(md).split(/\r?\n/).forEach((ln) => {
      const t = ln.trim();
      if (!t) { list = null; return; }
      if (/^[-*] /.test(t)) {
        if (!list) { list = el("ul"); box.appendChild(list); }
        list.appendChild(el("li", null, t.slice(2)));
        return;
      }
      list = null;
      if (/^#{1,3} /.test(t)) { box.appendChild(el("div", "md-h", t.replace(/^#+\s*/, ""))); return; }
      const m = t.match(/^\*\*([^*]+):\*\*\s*(.*)$/);
      const p = el("p");
      if (m) { p.appendChild(el("span", "lbl", m[1] + ": ")); p.appendChild(document.createTextNode(m[2])); }
      else p.appendChild(document.createTextNode(t.replace(/\*\*/g, "")));
      box.appendChild(p);
    });
    return box;
  }

  /* ---------- meters ---------- */
  function updateMeters(m) {
    const steps = Object.values(m.tiers || {}).reduce((a, b) => a + int(b), 0) || (int(m.replayed_steps) + int(m.healed_steps));
    $("#m-steps").textContent = fmtInt(steps || m.tests || 0);
    $("#m-replayed").innerHTML = "";
    const rep = $("#m-replayed");
    rep.textContent = pct(int(m.replayed_steps), steps) + "% ";
    rep.appendChild(el("small", null, fmtInt(m.replayed_steps) + "/" + fmtInt(steps)));
    $("#m-healed").textContent = fmtInt(m.healed_steps);
    const llm = $("#m-llm"); llm.textContent = fmtInt(m.llm_calls);
    if (int(m.llm_calls_cached)) llm.appendChild(el("small", null, " +" + fmtInt(m.llm_calls_cached) + " cached"));
    $("#m-tokens").textContent = fmtInt(int(m.tokens_in) + int(m.tokens_out));
    $("#m-cost").textContent = fmtUsd(m.cost_usd);
    $("#m-list").textContent = fmtUsd(m.list_cost_usd);
    const avoided = Math.max(0, int(m.naive_tokens_estimate) - int(m.tokens_in) - int(m.tokens_out));
    const av = $("#m-avoided"); av.textContent = fmtInt(avoided);
    av.appendChild(el("small", null, " / " + fmtInt(m.naive_tokens_estimate)));
  }

  /* ---------- lightbox ---------- */
  function openLightbox(src, cap) {
    const lb = $("#lightbox"), img = $("#lb-img");
    img.alt = "Screenshot " + cap;
    img.onerror = function () { img.hidden = true; };
    if (src) { img.hidden = false; img.src = src; } else { img.hidden = true; }
    $("#lb-cap").textContent = src ? cap : cap + " — no capture stored in mock mode";
    lb.hidden = false;
    $("#lb-close").focus();
  }
  function closeLightbox() { $("#lightbox").hidden = true; }

  /* ---------- drawer + rail tabs ---------- */
  function wireTabs() {
    const dtabs = [$("#dtab-term"), $("#dtab-mcp"), $("#dtab-evt")];
    const panels = { "dtab-term": $("#tab-terminal"), "dtab-mcp": $("#tab-mcp"), "dtab-evt": $("#tab-events") };
    dtabs.forEach((t) => t.addEventListener("click", () => {
      dtabs.forEach((x) => x.setAttribute("aria-selected", String(x === t)));
      Object.entries(panels).forEach(([id, p]) => { p.hidden = id !== t.id; });
    }));
    $("#clear-out").addEventListener("click", () => { $("#term-out").textContent = ""; $("#tab-mcp").innerHTML = ""; $("#tab-events").innerHTML = ""; });

    const rtabs = $$(".railtab");
    rtabs.forEach((t) => t.addEventListener("click", () => {
      rtabs.forEach((x) => x.setAttribute("aria-selected", String(x === t)));
      const target = t.dataset.panel;
      ["rail-left", "rail-right"].forEach((id) => $("#" + id).classList.toggle("is-active", id === target));
      $("#center").classList.toggle("is-hidden", target !== "center");
    }));
  }

  /* ================================================================== */
  /* BOOT                                                               */
  /* ================================================================== */
  async function boot() {
    if (MOCK) {
      const spacer = document.querySelector(".topbar .spacer");
      const flag = el("span", "mock-flag", "MOCK DATA");
      (spacer ? spacer.parentNode : document.body).insertBefore(flag, spacer || null);
    }
    wireTabs();
    $("#lb-close").addEventListener("click", closeLightbox);
    $("#lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });

    let sky = null, llm = null, scenarios = null;
    try { sky = await getJSON("/api/skyops/state", "skyops_state.json"); } catch (e) {}
    try { llm = await getJSON("/api/llm", "llm.json"); } catch (e) {}
    try { scenarios = await getJSON("/api/scenarios", "scenarios.json"); } catch (e) {}
    renderTopbar(sky, llm);
    renderScenarios(scenarios);

    // In mock mode, auto-run the first scenario so the console is demonstrable on load.
    if (MOCK && scenarios && scenarios.length) {
      const first = $('#scenarios .scn-item[data-scenario="' + scenarios[0].id + '"]');
      setTimeout(() => startJob(scenarios[0].id, first), 250);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
