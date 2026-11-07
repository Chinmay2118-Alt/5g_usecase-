// Command dashboard for the rescue-rover scenarios (10 Hz state over /ws/dashboard).
import { RttChart } from '../common/rttchart.js';
import { TopDown } from '../common/topdown.js';

const $ = (id) => document.getElementById(id);
const DELAYS = [0, 50, 100, 200, 400];
const LEGEND = {
  flood: [['#1e3a8a', 'deep water (impassable)'], ['#60a5fa', 'shallow water (slow)']],
  earthquake: [['#8b7d6b', 'rubble (impassable)'], ['#b8a88f', 'debris (slow)'], ['#60a5fa', 'river']],
  wildfire: [['#ff6a00', 'fire (impassable)'], ['#1c1917', 'burnt'], ['#60a5fa', 'river']],
  landslide: [['#5b4128', 'thick mud (impassable)'], ['#8a6a45', 'mud (slow)'], ['#60a5fa', 'river']],
};
const chart = new RttChart($('chart'));
let ws = null, m = null, state = null, td = null, profilesById = {};
let wasDone = false;
const results = [];

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/dashboard`);
  ws.onopen = () => { $('conn').textContent = 'dashboard connected'; };
  ws.onclose = () => { $('conn').textContent = 'reconnecting…'; setTimeout(connect, 1000); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'hello' && msg.map) onHello(msg);
    else if (msg.type === 'state') onState(msg);
  };
}
const send = (action, extra = {}) => ws?.readyState === WebSocket.OPEN &&
  ws.send(JSON.stringify({ type: 'control', action, ...extra }));
const rttColor = (v) => (v == null ? '' : v < 60 ? 'var(--ok)' : v < 150 ? 'var(--warn)' : 'var(--bad)');

function onHello(msg) {
  m = msg.map;
  td = new TopDown(m);
  wasDone = false;
  const type = m.hazard?.type || 'flood';
  $('title').textContent = `${msg.title || 'Rescue'} rescue — command dashboard`;
  $('areaTitle').textContent = `${msg.title || 'Disaster'} area (240 m × 240 m)`;
  $('hazardName').textContent = msg.title || 'Hazard';
  // scenario dropdown
  const sel = $('scenario');
  sel.innerHTML = (msg.scenarios || [{ id: msg.scenario, title: msg.title }])
    .map((s) => `<option value="${s.id}">${s.title}</option>`).join('');
  sel.value = msg.scenario;
  // hazard buttons for this scenario
  const box = $('hazardBtns');
  box.innerHTML = '';
  for (const c of msg.controls || []) {
    const b = document.createElement('button');
    b.textContent = c.label;
    b.onclick = () => send('hazard', { value: c.action });
    box.appendChild(b);
  }
  // network profile buttons (same list as the "Why 5G" page)
  profilesById = Object.fromEntries((msg.profiles || []).map((p) => [p.id, p]));
  const pb = $('profiles');
  pb.innerHTML = '';
  for (const p of msg.profiles || []) {
    const b = document.createElement('button');
    b.textContent = p.label; b.dataset.id = p.id; b.title = p.note;
    b.onclick = () => send('set_profile', { value: p.id });
    pb.appendChild(b);
  }
  $('legendHazard').innerHTML = (LEGEND[type] || []).map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join('');
}

function onState(st) {
  if (!m || (st.sim.scenario && m.hazard && st.sim.scenario !== m.hazard.type)) return;
  state = st;
  if (st.layer) td.setLayer(st.layer);
  chart.push(st);
  const s = st.sim, r = st.client_rtt || {}, live = st.net.operator && !st.net.link_cut;
  $('rescued').innerHTML = `${s.score}<small>/${s.target}${s.airlifted ? ` · ${s.airlifted} airlifted` : ''}</small>`;
  $('onboard').innerHTML = `${s.onboard}<small>/${s.capacity}</small>`;
  $('time').innerHTML = s.task_s == null ? '—' : `${s.task_s.toFixed(0)} <small>s${s.done ? ' ✓' : ''}</small>`;
  const mode = st.robot.state === 'ARMED' ? s.mode : st.robot.state;
  $('mode').textContent = mode;
  $('mode').style.color = { AUTO: 'var(--accent)', MANUAL: 'var(--ok)', HOLDING: 'var(--warn)' }[mode] || 'var(--muted)';
  $('speed').innerHTML = `${(Math.abs(s.rover.v) * 3.6).toFixed(0)} <small>km/h ${s.terrain_label ? '· ' + s.terrain_label.toLowerCase() : ''}</small>`;
  $('level').innerHTML = s.stage + (s.hz?.heat != null ? ` · rover heat ${s.hz.heat.toFixed(0)}%` : '');
  $('p50').innerHTML = live && r.p50 != null ? `${r.p50.toFixed(0)} <small>ms</small>` : '—';
  $('p50').style.color = live ? rttColor(r.p50) : '';
  $('p95').innerHTML = live && r.p95 != null ? `${r.p95.toFixed(0)} <small>ms</small>` : '—';
  $('p95').style.color = live ? rttColor(r.p95) : '';
  const prof = profilesById[st.net.profile?.id];
  const emu = (prof && !prof.live ? prof.rtt_ms : 0) + st.net.added_rtt_ms;
  $('added').innerHTML = emu ? `+${emu.toFixed(0)} <small>ms ${prof && !prof.live ? '· ' + prof.label.split(' ')[0] : ''}</small>`
    : `0 <small>ms · real link</small>`;
  $('hz').innerHTML = `${st.net.cmd_hz} <small>Hz</small>`;
  const hl = st.robot.hold_latency_ms;
  $('reason').textContent = `Safety: ${st.robot.state} (${st.robot.reason})` +
    (hl != null ? ` · last watchdog trip ${hl.toFixed(0)} ms after the last command` : '') +
    ` · operator ${st.net.operator ? 'connected' : 'not connected'}`;
  $('run').textContent = `Run: ${st.run.id}${st.run.participant ? ' · participant ' + st.run.participant : ''}`;

  const b = $('banner');
  if (st.net.link_cut || !st.net.operator) { b.className = 'banner show bad'; b.textContent = st.net.link_cut ? 'LINK CUT — ROVER HOLDING' : 'NO OPERATOR CONNECTED'; }
  else if (s.hazard) { b.className = 'banner show bad'; b.textContent = 'PERSON AHEAD — AI STOP'; }
  else if (st.robot.state === 'HOLDING' && st.robot.reason === 'stop_overheat') { b.className = 'banner show bad'; b.textContent = 'ROVER OVERHEATED — stopped itself'; }
  else if (st.robot.state === 'HOLDING') { b.className = 'banner show warn'; b.textContent = `ROVER HOLDING (${st.robot.reason})`; }
  else if (s.done) { b.className = 'banner show good'; b.textContent = `MISSION COMPLETE — ${s.score} rescued${s.airlifted ? `, ${s.airlifted} airlifted` : ''} in ${s.task_s.toFixed(0)} s`; }
  else b.className = 'banner';

  document.querySelectorAll('#delays button').forEach((el) => el.classList.toggle('on', Number(el.dataset.v) === st.net.added_rtt_ms));
  $('cut').classList.toggle('on', st.net.link_cut);
  document.querySelectorAll('#profiles button').forEach((el) => el.classList.toggle('on', el.dataset.id === st.net.profile?.id));
  drawStopCard(s.stop_test, s.ai_pending, st.net.profile);
  if (s.done && !wasDone) {
    // collision counts come from the server's results (it counts every "blocked" bump)
    fetch('/api/results').then((x) => x.json()).then((d) => {
      const missions = d.results.filter((x) => x.kind === 'mission').reverse();
      $('results').innerHTML = missions.map((x) =>
        `<tr><td>${x.scenario}</td><td>${x.network}</td><td>${st.run.participant || '—'}</td><td>${x.extra_ms} ms</td>` +
        `<td>${x.rtt_p50 == null ? '—' : x.rtt_p50.toFixed(0) + ' ms'}</td><td>${x.rescued}/${x.target}</td>` +
        `<td>${x.blocked}</td><td>${x.time_s?.toFixed(0)} s</td></tr>`).join('');
    }).catch(() => {});
  }
  wasDone = s.done;
  drawMap();
  chart.draw();
}

// ---- AI-stop result card ---------------------------------------------------
function drawStopCard(t, pending, prof) {
  const el = $('stopcard');
  if (pending) {
    el.className = 'stopcard show info';
    el.innerHTML = `<b>Person on the road…</b> AI stop on its way over <b>${prof?.label ?? ''}</b>`;
    return;
  }
  if (!t || t.age > 20) { el.className = 'stopcard'; return; }
  if (t.result === 'stationary') {
    el.className = 'stopcard show info';
    el.innerHTML = 'The rover was standing still. Drive at full speed, then click <b>Person crosses road</b> again.';
    return;
  }
  const hit = t.result === 'would have hit';
  el.className = `stopcard show ${hit ? 'bad' : 'ok'}`;
  el.innerHTML = `<b>${hit ? '✗ Would have hit the person' : `✓ Stopped ${t.margin_m.toFixed(1)} m before the person`}</b><br>` +
    `${profilesById[t.profile]?.label ?? ''}: AI stop arrived after <b>${t.reaction_ms} ms</b> at ${t.speed_kmh} km/h → drove <b>${t.blind_m.toFixed(1)} m blind</b> ` +
    `before braking, ${t.travelled_m.toFixed(1)} m in total.`;
}

// ---- top-down map (shared with the cockpit tablet) ---------------------------
function drawMap() {
  const c = $('map');
  if (!td || !state) return;
  const g = c.getContext('2d');
  td.draw(g, c.width, state.sim, { wind: true });
  g.fillStyle = '#e2e8f0'; g.font = '12px system-ui';
  g.fillText("N ▲ (far side of the operator's table)", 8, 16);
}

for (const v of DELAYS) {
  const b = document.createElement('button');
  b.textContent = `${v} ms`; b.dataset.v = v;
  b.onclick = () => send('set_delay_ms', { value: v });
  $('delays').appendChild(b);
}
$('scenario').onchange = (e) => send('set_scenario', { value: e.target.value });
$('cut').onclick = () => send('cut_link', { value: true });
$('restore').onclick = () => send('cut_link', { value: false });
$('person').onclick = () => send('person_on_road');
$('stop').onclick = () => send('stop', { source: 'dashboard' });
$('reset').onclick = () => send('reset_task');
$('start').onclick = () => send('start_run', { participant: $('pid').value.trim(), label: `${state?.sim?.scenario ?? ''}-rtt${state?.net?.added_rtt_ms ?? 0}` });
connect();
