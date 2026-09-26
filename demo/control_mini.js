/* Argus demo control overlay — a small, draggable, minimizable panel injected into the cockpit page
   purely as a presentation aid. It is NOT part of the product under test; it only calls the starter kit's
   own control API (http://localhost:4000/api) so a live audience can see cause (fault) and effect (cockpit)
   in one view. Inject via bookmarklet or console; injecting twice just re-opens it. */
(() => {
  const API = (window.__ARGUS_API__ || 'http://localhost:4000/api');
  const existing = document.getElementById('argus-demo-ctl');
  if (existing) { existing.style.display = 'block'; existing.__restore && existing.__restore(); return; }

  const el = (t, s, h) => { const e = document.createElement(t); if (s) e.style.cssText = s; if (h != null) e.innerHTML = h; return e; };
  const wrap = el('div', `position:fixed;left:18px;bottom:18px;z-index:2147483647;width:250px;
    font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;color:#e8ecf2;
    background:rgba(13,17,24,.96);border:1px solid rgba(255,255,255,.16);border-radius:12px;
    box-shadow:0 16px 44px rgba(0,0,0,.5);backdrop-filter:blur(6px);user-select:none;overflow:hidden`);
  wrap.id = 'argus-demo-ctl';

  const bar = el('div', `display:flex;align-items:center;gap:8px;padding:9px 11px;cursor:grab;
    background:rgba(255,255,255,.05);border-bottom:1px solid rgba(255,255,255,.10)`);
  bar.innerHTML = `<span style="width:8px;height:8px;border-radius:2px;background:#3ddc97;box-shadow:0 0 8px #3ddc97"></span>
    <b style="font-weight:700;letter-spacing:.02em;flex:1">Demo Controls</b>`;
  const min = el('button', 'all:unset;cursor:pointer;color:#9aa4b2;font-size:15px;padding:0 4px;line-height:1', '–');
  bar.appendChild(min);

  const body = el('div', 'padding:10px 11px;display:flex;flex-direction:column;gap:9px');
  const grp = (title) => { const g = el('div'); g.appendChild(el('div', 'color:#7f8794;font-size:10px;letter-spacing:.09em;text-transform:uppercase;margin-bottom:5px', title)); const row = el('div', 'display:flex;flex-wrap:wrap;gap:5px'); g.appendChild(row); return [g, row]; };
  const status = el('div', 'color:#8ab4ff;font-size:11px;min-height:15px;padding-top:2px', 'ready');

  const btn = (label, kind) => {
    const bg = kind === 'danger' ? 'rgba(214,59,59,.16)' : kind === 'warn' ? 'rgba(245,184,65,.15)'
      : kind === 'good' ? 'rgba(61,220,151,.15)' : 'rgba(255,255,255,.08)';
    const bc = kind === 'danger' ? 'rgba(214,59,59,.45)' : kind === 'warn' ? 'rgba(245,184,65,.4)'
      : kind === 'good' ? 'rgba(61,220,151,.4)' : 'rgba(255,255,255,.16)';
    const b = el('button', `all:unset;cursor:pointer;padding:5px 9px;border-radius:7px;font-size:11px;
      background:${bg};border:1px solid ${bc};color:#e8ecf2;transition:transform .08s`, label);
    b.onmouseenter = () => b.style.background = bg.replace(/[\d.]+\)$/, '.28)');
    b.onmouseleave = () => b.style.background = bg;
    b.onmousedown = () => b.style.transform = 'scale(.96)';
    b.onmouseup = () => b.style.transform = '';
    return b;
  };

  const flash = (msg, ok = true) => { status.textContent = msg; status.style.color = ok ? '#3ddc97' : '#ff8a8a';
    clearTimeout(flash._t); flash._t = setTimeout(() => { status.textContent = 'ready'; status.style.color = '#8ab4ff'; }, 3200); };
  const call = async (method, path, bodyObj, label) => {
    try {
      const r = await fetch(API + path, { method, headers: bodyObj ? { 'content-type': 'application/json' } : undefined,
        body: bodyObj ? JSON.stringify(bodyObj) : undefined });
      flash(`✓ ${label} (HTTP ${r.status})`, r.ok); return r.ok;
    } catch (e) { flash(`✗ ${label}: ${e.message}`, false); return false; }
  };

  // Drones
  const [dg, drow] = grp('Drone 1');
  drow.appendChild(Object.assign(btn('Take off', 'good'), { onclick: () => call('POST', '/control/command', { deviceId: 'drone-1', type: 'takeoff' }, 'Drone 1 take off') }));
  drow.appendChild(Object.assign(btn('Land'), { onclick: () => call('POST', '/control/command', { deviceId: 'drone-1', type: 'land' }, 'Drone 1 land') }));

  // Faults
  const [fg, frow] = grp('Inject fault');
  const faults = [
    ['Freeze video', 'warn', { kind: 'video-freeze', deviceId: 'drone-1', seconds: 15 }],
    ['Drop telemetry', 'warn', { kind: 'socket-drop', value: 100 }],
    ['Delay 6s', 'warn', { kind: 'socket-delay', value: 6000 }],
    ['Sim offline', 'warn', { kind: 'sim-offline', seconds: 20 }],
    ['Kick sockets', 'warn', { kind: 'socket-kick' }],
  ];
  faults.forEach(([label, k, body]) => frow.appendChild(Object.assign(btn(label, k), { onclick: () => call('POST', '/control/fault', body, label) })));

  const [rg, rrow] = grp('Recover');
  rrow.appendChild(Object.assign(btn('Clear all faults', 'good'), { onclick: () => call('DELETE', '/control/fault', null, 'cleared faults') }));
  rrow.appendChild(Object.assign(btn('Reset sim'), { onclick: async () => { await call('POST', '/control/sim', { action: 'reset' }, 'reset'); call('POST', '/control/sim', { action: 'start' }, 'start'); } }));

  body.append(dg, fg, rg, status);
  wrap.append(bar, body);
  document.documentElement.appendChild(wrap);

  // minimize -> small pill
  let minimized = false;
  const restore = () => { minimized = false; body.style.display = 'flex'; wrap.style.width = '250px'; min.textContent = '–'; bar.querySelector('b').textContent = 'Demo Controls'; };
  wrap.__restore = restore;
  min.onclick = (e) => { e.stopPropagation(); minimized = !minimized;
    body.style.display = minimized ? 'none' : 'flex'; wrap.style.width = minimized ? 'auto' : '250px';
    min.textContent = minimized ? '▢' : '–'; bar.querySelector('b').textContent = minimized ? 'Demo' : 'Demo Controls'; };

  // drag by title bar
  let sx, sy, ox, oy, drag = false;
  bar.addEventListener('mousedown', (e) => { if (e.target === min) return; drag = true; bar.style.cursor = 'grabbing';
    const r = wrap.getBoundingClientRect(); sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    wrap.style.bottom = 'auto'; wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px'; e.preventDefault(); });
  window.addEventListener('mousemove', (e) => { if (!drag) return;
    wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px'; wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px'; });
  window.addEventListener('mouseup', () => { drag = false; bar.style.cursor = 'grab'; });
})();
