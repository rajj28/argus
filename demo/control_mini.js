/* Argus demo control overlay — a small, draggable, minimizable panel injected into the cockpit page as a
   presentation aid. It (a) injects changing conditions via the starter kit's control API, and (b) runs a live
   truth monitor that spells out what the UI claims next to the ground truth, so an audience can SEE the bug the
   moment it is caused. It does NOT modify the product under test. Inject via bookmarklet or console. */
(() => {
  const API = (window.__ARGUS_API__ || 'http://localhost:4000/api');
  const old = document.getElementById('argus-demo-ctl');
  if (old) { old.style.display = 'block'; old.__restore && old.__restore(); return; }
  const el = (t, s, h) => { const e = document.createElement(t); if (s) e.style.cssText = s; if (h != null) e.innerHTML = h; return e; };
  const num = (s) => { const m = /-?\d+(\.\d+)?/.exec(s || ''); return m ? parseFloat(m[0]) : null; };
  const q = (s) => document.querySelector(s);
  const txt = (s) => (q(s)?.innerText || '').replace(/\s+/g, ' ').trim();

  const wrap = el('div', `position:fixed;left:18px;bottom:18px;z-index:2147483647;width:270px;
    font:12px/1.42 ui-monospace,SFMono-Regular,Consolas,monospace;color:#e8ecf2;
    background:rgba(13,17,24,.96);border:1px solid rgba(255,255,255,.16);border-radius:12px;
    box-shadow:0 16px 44px rgba(0,0,0,.5);backdrop-filter:blur(6px);user-select:none;overflow:hidden;transition:box-shadow .25s,border-color .25s`);
  wrap.id = 'argus-demo-ctl';

  const bar = el('div', `display:flex;align-items:center;gap:8px;padding:9px 11px;cursor:grab;
    background:rgba(255,255,255,.05);border-bottom:1px solid rgba(255,255,255,.10)`);
  const dot = el('span', 'width:8px;height:8px;border-radius:2px;background:#3ddc97;box-shadow:0 0 8px #3ddc97');
  const title = el('b', 'font-weight:700;letter-spacing:.02em;flex:1', 'Argus · live check');
  const min = el('button', 'all:unset;cursor:pointer;color:#9aa4b2;font-size:15px;padding:0 4px;line-height:1', '–');
  bar.append(dot, title, min);

  const body = el('div', 'padding:10px 11px;display:flex;flex-direction:column;gap:10px');

  // --- live monitor ---
  const banner = el('div', 'padding:6px 9px;border-radius:7px;font-weight:700;font-size:11.5px;text-align:center;background:#15452f;color:#8ef0c0', '✓ UI matches ground truth');
  const rowsBox = el('div', 'display:flex;flex-direction:column;gap:4px');
  const monRow = (label) => {
    const r = el('div', 'display:grid;grid-template-columns:60px 1fr auto;gap:6px;align-items:baseline;font-size:11px');
    const l = el('span', 'color:#7f8794', label); const v = el('span', 'color:#e8ecf2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis');
    const s = el('span', 'font-weight:700'); r.append(l, v, s); return { r, v, s };
  };
  const mVideo = monRow('Video'), mSource = monRow('Source'), mTele = monRow('Telemetry');
  rowsBox.append(mVideo.r, mSource.r, mTele.r);
  body.append(banner, rowsBox, el('div', 'height:1px;background:rgba(255,255,255,.08)'));

  // --- controls ---
  const grp = (t) => { const g = el('div'); g.appendChild(el('div', 'color:#7f8794;font-size:10px;letter-spacing:.09em;text-transform:uppercase;margin-bottom:5px', t)); const row = el('div', 'display:flex;flex-wrap:wrap;gap:5px'); g.appendChild(row); return [g, row]; };
  const status = el('div', 'color:#8ab4ff;font-size:11px;min-height:14px', 'ready');
  const btn = (label, kind) => {
    const c = kind === 'warn' ? ['rgba(245,184,65,.15)', 'rgba(245,184,65,.4)'] : kind === 'good' ? ['rgba(61,220,151,.15)', 'rgba(61,220,151,.4)'] : ['rgba(255,255,255,.08)', 'rgba(255,255,255,.16)'];
    const b = el('button', `all:unset;cursor:pointer;padding:5px 9px;border-radius:7px;font-size:11px;background:${c[0]};border:1px solid ${c[1]};color:#e8ecf2`, label);
    b.onmousedown = () => b.style.transform = 'scale(.96)'; b.onmouseup = () => b.style.transform = ''; return b;
  };
  const flash = (m, ok = true) => { status.textContent = m; status.style.color = ok ? '#3ddc97' : '#ff8a8a'; clearTimeout(flash._t); flash._t = setTimeout(() => { status.textContent = 'ready'; status.style.color = '#8ab4ff'; }, 3200); };
  const call = async (method, path, obj, label) => { try { const r = await fetch(API + path, { method, headers: obj ? { 'content-type': 'application/json' } : undefined, body: obj ? JSON.stringify(obj) : undefined }); flash(`✓ ${label} (HTTP ${r.status})`, r.ok); } catch (e) { flash(`✗ ${label}: ${e.message}`, false); } };

  const [dg, drow] = grp('Drone 1');
  drow.append(Object.assign(btn('Take off', 'good'), { onclick: () => call('POST', '/control/command', { deviceId: 'drone-1', type: 'takeoff' }, 'take off') }),
              Object.assign(btn('Land'), { onclick: () => call('POST', '/control/command', { deviceId: 'drone-1', type: 'land' }, 'land') }));
  const [fg, frow] = grp('Inject fault');
  [['Freeze video', { kind: 'video-freeze', deviceId: 'drone-1', seconds: 20 }], ['Drop telemetry', { kind: 'socket-drop', value: 100 }],
   ['Delay 6s', { kind: 'socket-delay', value: 6000 }], ['Sim offline', { kind: 'sim-offline', seconds: 25 }]]
    .forEach(([l, b]) => frow.append(Object.assign(btn(l, 'warn'), { onclick: () => call('POST', '/control/fault', b, l) })));
  const [rg, rrow] = grp('Recover');
  rrow.append(Object.assign(btn('Clear faults', 'good'), { onclick: () => call('DELETE', '/control/fault', null, 'cleared faults') }),
              Object.assign(btn('Reset'), { onclick: async () => { await call('POST', '/control/sim', { action: 'reset' }, 'reset'); call('POST', '/control/sim', { action: 'start' }, 'start'); } }));
  body.append(dg, fg, rg, status);
  wrap.append(bar, body);
  document.documentElement.appendChild(wrap);

  // --- monitor loop: UI claim vs ground truth, once a second ---
  let prevFrames = null, prevT = 0, lastDist = null, distStableSince = Date.now();
  const set = (row, uiText, ok, badText) => { row.v.textContent = uiText; row.s.textContent = ok ? '✓' : '✗'; row.s.style.color = ok ? '#3ddc97' : '#ff6b6b'; row.v.style.color = ok ? '#e8ecf2' : '#ffb4b4'; return ok ? null : badText; };
  const tick = async () => {
    let faults = [], sim = 'connected';
    try { const f = await fetch(API + '/control/fault').then(r => r.json()); faults = (f.faults || []).map(x => x.kind); } catch (e) {}
    try { sim = (await fetch(API + '/health').then(r => r.json())).simulator; } catch (e) {}
    const now = performance.now();
    const vEl = q('video'); const pq = vEl && vEl.getVideoPlaybackQuality ? vEl.getVideoPlaybackQuality() : null;
    const frames = pq ? pq.totalVideoFrames : null; let fps = null;
    if (frames != null && prevFrames != null && now - prevT > 400) fps = Math.max(0, (frames - prevFrames) / ((now - prevT) / 1000));
    if (frames != null) { prevFrames = frames; prevT = now; }
    const vLabel = txt('[data-testid="video-state"]') || 'video'; const badge = txt('[data-testid="socket-status"]') || '';
    const dist = num(txt('[data-testid="telemetry-home-distance"]')); const flight = txt('[data-testid="status-flight"]');
    if (dist !== lastDist) { lastDist = dist; distStableSince = Date.now(); }
    const feedFault = faults.some(k => ['socket-drop', 'socket-delay', 'sim-offline'].includes(k));
    const videoFault = faults.some(k => k.startsWith('video-'));
    const bugs = [];
    // Video: "live" but not decoding frames
    const vLive = /live/i.test(vLabel);
    bugs.push(set(mVideo, `${vLabel}${fps != null ? ' · ' + fps.toFixed(0) + ' fps' : ''}`,
      !(vLive && fps != null && fps < 2 && videoFault), 'video FROZEN but labelled "live"'));
    // Source: simulator offline but UI still says connected
    const connBadge = /connect/i.test(badge) && !/re-?connect|disconnect/i.test(badge);
    bugs.push(set(mSource, badge || 'n/a', !(sim !== 'connected' && connBadge), 'simulator OFFLINE but shown "connected"'));
    // Telemetry: values frozen while a feed fault is active and badge says connected
    const frozen = feedFault && connBadge && (Date.now() - distStableSince > 2500) && /in_flight|taking/i.test(flight);
    bugs.push(set(mTele, dist != null ? `${dist} m · ${flight || '—'}` : (flight || '—'), !frozen, 'telemetry FROZEN but shown live'));
    const hit = bugs.filter(Boolean);
    if (hit.length) {
      banner.textContent = '⚠ BUG: ' + hit[0]; banner.style.background = '#5c1d24'; banner.style.color = '#ffd0d0';
      wrap.style.borderColor = 'rgba(255,107,107,.6)'; wrap.style.boxShadow = '0 0 0 1px rgba(255,107,107,.5),0 16px 44px rgba(0,0,0,.5)';
      dot.style.background = '#ff6b6b'; dot.style.boxShadow = '0 0 8px #ff6b6b';
    } else {
      banner.textContent = '✓ UI matches ground truth'; banner.style.background = '#15452f'; banner.style.color = '#8ef0c0';
      wrap.style.borderColor = 'rgba(255,255,255,.16)'; wrap.style.boxShadow = '0 16px 44px rgba(0,0,0,.5)';
      dot.style.background = '#3ddc97'; dot.style.boxShadow = '0 0 8px #3ddc97';
    }
  };
  wrap.__mon = setInterval(tick, 1000); tick();

  // minimize
  let minimized = false;
  wrap.__restore = () => { minimized = false; body.style.display = 'flex'; wrap.style.width = '270px'; min.textContent = '–'; title.textContent = 'Argus · live check'; };
  min.onclick = (e) => { e.stopPropagation(); minimized = !minimized; body.style.display = minimized ? 'none' : 'flex'; wrap.style.width = minimized ? 'auto' : '270px'; min.textContent = minimized ? '▢' : '–'; title.textContent = minimized ? 'Argus' : 'Argus · live check'; };
  // drag
  let sx, sy, ox, oy, drag = false;
  bar.addEventListener('mousedown', (e) => { if (e.target === min) return; drag = true; bar.style.cursor = 'grabbing'; const r = wrap.getBoundingClientRect(); sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; wrap.style.bottom = 'auto'; wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px'; e.preventDefault(); });
  window.addEventListener('mousemove', (e) => { if (!drag) return; wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px'; wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px'; });
  window.addEventListener('mouseup', () => { drag = false; bar.style.cursor = 'grab'; });
})();
