// Audience / experimenter dashboard. Receives state at 10 Hz over /ws/dashboard.
const $ = (id) => document.getElementById(id);
const DELAYS = [0, 50, 100, 200, 400];

let ws = null, geom = null, state = null;
const history = [];      // {t, p50, p95, added}
let lastRttKey = '';
let wasDone = false;
const results = [];

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/dashboard`);
  ws.onopen = () => { $('conn').textContent = 'dashboard connected'; };
  ws.onclose = () => { $('conn').textContent = 'reconnecting…'; setTimeout(connect, 1000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'hello') geom = m.geometry;
    else if (m.type === 'state') onState(m);
  };
}

function send(action, extra = {}) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'control', action, ...extra }));
}

function rttClass(ms) { return ms == null ? '' : ms < 60 ? 'var(--ok)' : ms < 150 ? 'var(--warn)' : 'var(--bad)'; }

function onState(m) {
  state = m;
  const r = m.client_rtt || {};
  const key = `${r.p50}|${r.p95}|${r.n}|${r.last}`;
  if (r.p50 != null && key !== lastRttKey) {
    lastRttKey = key;
    history.push({ t: m.t_srv, p50: r.p50, p95: r.p95, added: m.net.added_rtt_ms });
    while (history.length && m.t_srv - history[0].t > 180) history.shift();
  }

  const live = m.net.operator && !m.net.link_cut;
  $('p50').innerHTML = live && r.p50 != null ? `${r.p50.toFixed(0)} <small>ms</small>` : '—';
  $('p50').style.color = live ? rttClass(r.p50) : '';
  $('p95').innerHTML = live && r.p95 != null ? `${r.p95.toFixed(0)} <small>ms</small>` : '—';
  $('p95').style.color = live ? rttClass(r.p95) : '';
  $('added').innerHTML = `${m.net.added_rtt_ms.toFixed(0)} <small>ms</small>`;
  $('hz').innerHTML = `${m.net.cmd_hz} <small>Hz</small>`;
  const rs = m.robot.state;
  $('robot').textContent = rs;
  $('robot').style.color = rs === 'ARMED' ? 'var(--ok)' : rs === 'HOLDING' ? 'var(--warn)' : 'var(--muted)';
  const s = m.sim;
  $('task').innerHTML = `${s.score}/${s.target} <small>${s.task_s == null ? '' : s.task_s.toFixed(1) + ' s'}${s.done ? ' ✓' : ''}</small>`;
  const hl = m.robot.hold_latency_ms;
  $('reason').textContent = `Robot state reason: ${m.robot.reason}` +
    (hl != null ? ` · last watchdog trip ${hl.toFixed(0)} ms after the last command` : '') +
    ` · operator ${m.net.operator ? 'connected' : 'not connected'}`;
  $('run').textContent = `Run: ${m.run.id}${m.run.participant ? ' · participant ' + m.run.participant : ''}`;

  const b = $('banner');
  if (m.net.link_cut || !m.net.operator) { b.className = 'banner show bad'; b.textContent = m.net.link_cut ? 'LINK CUT — ROBOT HOLDING' : 'NO OPERATOR CONNECTED'; }
  else if (s.intruder) { b.className = 'banner show bad'; b.textContent = 'PERSON IN WORKSPACE — AI STOP'; }
  else if (rs === 'HOLDING') { b.className = 'banner show warn'; b.textContent = `ROBOT HOLDING (${m.robot.reason})`; }
  else b.className = 'banner';

  document.querySelectorAll('#delays button').forEach((el) =>
    el.classList.toggle('on', Number(el.dataset.v) === m.net.added_rtt_ms));
  $('cut').classList.toggle('on', m.net.link_cut);

  if (s.done && !wasDone) {
    results.unshift({ run: m.run.id, pid: m.run.participant || '—', added: m.net.added_rtt_ms,
                      p50: r.p50, time: s.task_s });
    renderResults();
  }
  wasDone = s.done;
  drawTop();
  drawChart();
}

function renderResults() {
  $('results').innerHTML = results.map((x) =>
    `<tr><td>${x.run}</td><td>${x.pid}</td><td>${x.added} ms</td><td>${x.p50 == null ? '—' : x.p50.toFixed(0) + ' ms'}</td><td>${x.time.toFixed(1)} s</td></tr>`).join('');
}

// --- top view: x right, z down (towards the operator) ---------------------
function drawTop() {
  const c = $('top'), g = c.getContext('2d');
  const W = c.width, H = c.height;
  g.clearRect(0, 0, W, H);
  if (!geom || !state) return;
  const x0 = -0.5, x1 = 0.5, z0 = -0.15, z1 = 0.58;
  const sc = Math.min(W / (x1 - x0), H / (z1 - z0));
  const px = (x) => (x - x0) * sc + (W - (x1 - x0) * sc) / 2;
  const pz = (z) => (z - z0) * sc + (H - (z1 - z0) * sc) / 2;

  g.fillStyle = '#3b2f24'; g.fillRect(px(-0.5), pz(-0.175), 1.0 * sc, 0.75 * sc);
  const ws = geom.workspace;
  g.strokeStyle = 'rgba(245,217,10,0.6)'; g.setLineDash([6, 5]); g.lineWidth = 2;
  g.strokeRect(px(ws.x[0]), pz(ws.z[0]), (ws.x[1] - ws.x[0]) * sc, (ws.z[1] - ws.z[0]) * sc);
  g.setLineDash([]);
  const bn = geom.bin;
  g.strokeStyle = '#a0aec0'; g.lineWidth = 3;
  g.strokeRect(px(bn.x - bn.half), pz(bn.z - bn.half), bn.half * 2 * sc, bn.half * 2 * sc);
  g.fillStyle = '#a0aec0'; g.font = '14px system-ui'; g.fillText('BIN', px(bn.x) - 13, pz(bn.z - bn.half) - 6);

  const cols = { red: '#e5484d', green: '#30a46c', blue: '#3e63dd' };
  const cs = geom.cube * sc;
  for (const cb of state.sim.cubes) {
    g.fillStyle = cols[cb.color] || '#fff';
    g.fillRect(px(cb.pos[0]) - cs / 2, pz(cb.pos[2]) - cs / 2, cs, cs);
    if (cb.held) { g.strokeStyle = '#fbbf24'; g.lineWidth = 2; g.strokeRect(px(cb.pos[0]) - cs / 2 - 3, pz(cb.pos[2]) - cs / 2 - 3, cs + 6, cs + 6); }
  }
  // robot base + arm projection
  const [tx, ty, tz] = state.sim.tip;
  g.fillStyle = '#2d3748'; g.beginPath(); g.arc(px(0), pz(0), 0.06 * sc, 0, 7); g.fill();
  g.strokeStyle = '#f08c00'; g.lineWidth = 10; g.lineCap = 'round';
  g.beginPath(); g.moveTo(px(0), pz(0)); g.lineTo(px(tx), pz(tz)); g.stroke();
  g.lineWidth = 3; g.strokeStyle = '#00d1ff';
  g.beginPath(); g.arc(px(tx), pz(tz), 6 + ty * 60, 0, 7); g.stroke();
  g.fillStyle = state.sim.grip < 0.5 ? '#fbbf24' : '#e2e8f0';
  g.beginPath(); g.arc(px(tx), pz(tz), 5, 0, 7); g.fill();
  if (state.sim.intruder) {
    g.fillStyle = 'rgba(248,113,113,0.35)'; g.fillRect(px(-0.25), pz(0.15), 0.5 * sc, 0.3 * sc);
    g.fillStyle = '#fecaca'; g.font = 'bold 18px system-ui'; g.fillText('PERSON', px(-0.05), pz(0.31));
  }
  g.fillStyle = '#94a3b8'; g.font = '13px system-ui';
  g.fillText(`tip height ${(ty * 100).toFixed(1)} cm`, 10, H - 10);
  g.fillText('▼ operator side', W - 120, H - 10);
}

function drawChart() {
  const c = $('chart'), g = c.getContext('2d');
  const W = c.width, H = c.height, pad = 34;
  g.clearRect(0, 0, W, H);
  if (!history.length) { g.fillStyle = '#64748b'; g.font = '14px system-ui'; g.fillText('Waiting for the headset to report RTT…', pad, H / 2); return; }
  const tEnd = history[history.length - 1].t, tStart = tEnd - 120;
  const maxV = Math.max(100, ...history.map((h) => Math.max(h.p95, h.added))) * 1.15;
  const X = (t) => pad + (t - tStart) / 120 * (W - pad - 8);
  const Y = (v) => H - 20 - v / maxV * (H - 34);
  g.strokeStyle = '#2a303a'; g.fillStyle = '#64748b'; g.font = '12px system-ui'; g.lineWidth = 1;
  for (let v = 0; v <= maxV; v += maxV > 400 ? 200 : 50) {
    g.beginPath(); g.moveTo(pad, Y(v)); g.lineTo(W - 8, Y(v)); g.stroke();
    g.fillText(String(v), 2, Y(v) + 4);
  }
  g.fillText('ms · last 2 min', W - 100, H - 4);
  const line = (key, color, w) => {
    g.strokeStyle = color; g.lineWidth = w; g.beginPath();
    history.filter((h) => h.t >= tStart).forEach((h, i) => (i ? g.lineTo(X(h.t), Y(h[key])) : g.moveTo(X(h.t), Y(h[key]))));
    g.stroke();
  };
  line('added', '#64748b', 2); line('p95', '#f472b6', 2); line('p50', '#38bdf8', 3);
}

// --- controls --------------------------------------------------------------
for (const v of DELAYS) {
  const b = document.createElement('button');
  b.textContent = `${v} ms`; b.dataset.v = v;
  b.onclick = () => send('set_delay_ms', { value: v });
  $('delays').appendChild(b);
}
$('cut').onclick = () => send('cut_link', { value: true });
$('restore').onclick = () => send('cut_link', { value: false });
$('intruder').onclick = () => send('intruder', { value: true });
$('stop').onclick = () => send('stop', { source: 'dashboard' });
$('reset').onclick = () => send('reset_task');
$('start').onclick = () => send('start_run', { participant: $('pid').value.trim(), label: `rtt${state?.net?.added_rtt_ms ?? 0}` });

connect();
