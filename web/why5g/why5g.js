// "Why 5G?" page: comparison table, latency-budget and stopping-distance charts,
// and live results from /api/results (refreshed every 3 s).
const $ = (id) => document.getElementById(id);
const V = 8.0, BRAKE = 15.0, GAP = 6.5, INFER = 30;          // m/s, m/s², m to the person, ms
const COLORS = { p5g_mec: '#4ade80', public_5g: '#38bdf8', lte: '#a78bfa', wifi_busy: '#f97316', satellite: '#f87171' };
// How each emulated network's extra delay splits into radio/access vs internet-to-cloud (illustrative)
const SPLIT = { p5g_mec: [0, 0], public_5g: [10, 25], lte: [30, 40], wifi_busy: [20, 5], satellite: [560, 40] };

// Comparison table (typical published ranges; vary by deployment). r = rating: g / m / b
const ROWS = [
  ['Typical round trip', ['5–20 ms', 'g'], ['25–50 ms', 'm'], ['40–80 ms', 'm'], ['5–100+ ms, spiky', 'b'], ['~600 ms+', 'b']],
  ['Jitter', ['low (scheduled radio)', 'g'], ['medium', 'm'], ['medium–high', 'm'], ['high under load', 'b'], ['medium', 'm']],
  ['Uplink for video', ['high, configurable', 'g'], ['medium', 'm'], ['low–medium', 'b'], ['shared, contended', 'm'], ['low', 'b']],
  ['Guaranteed QoS / slicing', ['yes (5QI, slices)', 'g'], ['limited', 'm'], ['limited', 'm'], ['best effort', 'b'], ['best effort', 'b']],
  ['Mobility across a site', ['seamless handover', 'g'], ['seamless', 'g'], ['seamless', 'g'], ['roaming gaps', 'b'], ['fixed dish', 'b']],
  ['Coverage', ['site-wide, multi-cell', 'g'], ['wide area', 'g'], ['wide area', 'g'], ['~50 m per AP', 'b'], ['global', 'g']],
  ['Data stays on site', ['yes (local UPF → MEC)', 'g'], ['no', 'b'], ['no', 'b'], ['yes', 'g'], ['no', 'b']],
  ['Spectrum', ['licensed, dedicated', 'g'], ['licensed, shared', 'm'], ['licensed, shared', 'm'], ['unlicensed', 'b'], ['licensed', 'm']],
];
const HEAD = ['', 'Private 5G + MEC', 'Public 5G → cloud', '4G LTE → cloud', 'Wi-Fi (busy)', 'Satellite (GEO)'];

let profiles = [], baseRtt = null;

function drawTable() {
  const cls = { g: 'good', m: 'mid', b: 'bad' };
  $('cmp').innerHTML = `<tr>${HEAD.map((h, i) => `<th class="${i === 1 ? 'hl' : ''}">${h}</th>`).join('')}</tr>` +
    ROWS.map(([name, ...cells]) => `<tr><th>${name}</th>${cells.map(([t, r], i) =>
      `<td class="${cls[r]} ${i === 0 ? 'hl' : ''}">${t}</td>`).join('')}</tr>`).join('');
}

function pipelineMs(p) {
  const base = baseRtt ?? 12;
  return { base, radio: SPLIT[p.id]?.[0] ?? 0, inet: SPLIT[p.id]?.[1] ?? 0, jitter: p.jitter_ms, infer: INFER };
}

function hbars(canvas, rows, maxV, unit, marker) {
  const g = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  g.clearRect(0, 0, W, H);
  const left = 170, right = 20, top = 26, rowH = (H - top - 34) / rows.length;
  const X = (v) => left + Math.min(1, v / maxV) * (W - left - right);
  g.font = '13px system-ui'; g.fillStyle = '#64748b'; g.strokeStyle = '#2a303a';
  for (let k = 0; k <= 4; k++) {
    const v = maxV * k / 4, x = X(v);
    g.beginPath(); g.moveTo(x, top - 6); g.lineTo(x, H - 30); g.stroke();
    g.fillText(`${Math.round(v * 10) / 10}${unit}`, x - 12, H - 12);
  }
  rows.forEach((r, i) => {
    const y = top + i * rowH + rowH * 0.18, h = rowH * 0.64;
    g.fillStyle = '#e2e8f0'; g.font = `${r.hl ? 'bold ' : ''}14px system-ui`;
    g.fillText(r.label, 10, y + h / 2 + 5);
    let acc = 0;
    for (const [v, col] of r.parts) {
      g.fillStyle = col; g.fillRect(X(acc), y, Math.max(1, X(acc + v) - X(acc)), h);
      acc += v;
    }
    g.fillStyle = r.textColor || '#e2e8f0'; g.font = 'bold 13px system-ui';
    const tx = Math.min(X(acc) + 6, W - 110);
    g.fillText(r.text, tx, y + h / 2 + 5);
  });
  if (marker) {
    const x = X(marker.v);
    g.strokeStyle = '#fbbf24'; g.setLineDash([6, 5]); g.lineWidth = 2;
    g.beginPath(); g.moveTo(x, top - 10); g.lineTo(x, H - 30); g.stroke(); g.setLineDash([]); g.lineWidth = 1;
    g.fillStyle = '#fbbf24'; g.font = 'bold 13px system-ui'; g.fillText(marker.label, Math.min(x + 6, W - 120), top - 10);
  }
}

function drawCharts() {
  if (!profiles.length) return;
  const rows = profiles.map((p) => {
    const b = pipelineMs(p);
    const total = b.base + b.radio + b.inet + b.jitter + b.infer;
    return { p, b, total };
  });
  // latency budget (ms), log-ish cap so satellite does not flatten the rest
  const cap = 260;
  hbars($('budget'), rows.map(({ p, b, total }) => ({
    label: p.label.replace(' (this lab)', ''), hl: p.live,
    parts: [[b.base, '#4ade80'], [b.radio, '#a78bfa'], [b.inet, '#f87171'], [b.jitter, '#f97316'], [b.infer, '#64748b']],
    text: `${Math.round(total)} ms${total > cap ? ' →' : ''}`,
  })), cap, ' ms');
  const g = $('budget').getContext('2d');
  [['real 5G (measured)', '#4ade80'], ['access/radio', '#a78bfa'], ['internet', '#f87171'], ['jitter/stalls', '#f97316'], ['AI', '#64748b']]
    .reduce((x, [t, c]) => {
      g.font = '11px system-ui';
      g.fillStyle = c; g.fillRect(x, 6, 10, 10); g.fillStyle = '#94a3b8'; g.fillText(t, x + 14, 15);
      return x + 24 + g.measureText(t).width;
    }, 10);

  // stopping distance (m)
  const brake = V * V / (2 * BRAKE);
  hbars($('distance'), rows.map(({ p, total }) => {
    const blind = V * total / 1000, all = blind + brake, hit = all > GAP - 1.0;
    return {
      label: p.label.replace(' (this lab)', ''), hl: p.live,
      parts: [[blind, COLORS[p.id] || '#38bdf8'], [brake, '#334155']],
      text: hit ? `${all.toFixed(1)} m ✗ hit` : `${all.toFixed(1)} m ✓`, textColor: hit ? '#f87171' : '#4ade80',
    };
  }), 9, ' m', { v: GAP, label: 'person' });
}

function group(results, kind) {
  const out = {};
  for (const r of results.filter((x) => x.kind === kind)) (out[r.network] ||= []).push(r);
  return out;
}
const avg = (a) => a.reduce((s, x) => s + x, 0) / a.length;

function drawResults(results) {
  const stops = group(results.filter((r) => r.result !== 'stationary'), 'stop_test');
  const sRows = Object.entries(stops).map(([net, rs]) => {
    const hits = rs.filter((r) => r.result === 'would have hit').length;
    const margins = rs.map((r) => r.margin_m);
    return `<tr><td>${net}</td><td>${rs.length}</td><td>${Math.round(avg(rs.map((r) => r.reaction_ms)))} ms</td>` +
      `<td>${avg(rs.map((r) => r.blind_m)).toFixed(2)} m</td><td class="${Math.min(...margins) < 1 ? 'bad' : 'good'}">${Math.min(...margins).toFixed(1)} m</td>` +
      `<td class="${hits ? 'bad' : 'good'}">${hits}</td></tr>`;
  });
  $('stops').innerHTML = '<tr><th>AI-stop tests</th><th>n</th><th>avg reaction</th><th>avg blind</th><th>worst margin</th><th>hits</th></tr>' +
    (sRows.join('') || '<tr><td colspan="6" class="empty">No stop tests yet: drive fast, then “Person crosses road”.</td></tr>');
  const ms = group(results, 'mission');
  const mRows = Object.entries(ms).map(([net, rs]) =>
    `<tr><td>${net}</td><td>${rs.length}</td><td>${Math.round(avg(rs.map((r) => r.time_s)))} s</td>` +
    `<td>${rs.map((r) => `${r.rescued}/${r.target}`).join(', ')}</td><td>${avg(rs.map((r) => r.blocked)).toFixed(1)}</td>` +
    `<td>${rs[0].rtt_p50 == null ? '—' : Math.round(avg(rs.map((r) => r.rtt_p50 ?? 0))) + ' ms'}</td></tr>`);
  $('missions').innerHTML = '<tr><th>Missions</th><th>n</th><th>avg time</th><th>rescued</th><th>avg collisions</th><th>RTT</th></tr>' +
    (mRows.join('') || '<tr><td colspan="6" class="empty">No completed missions yet.</td></tr>');
}

async function refresh() {
  try {
    const d = await (await fetch('/api/results', { cache: 'no-store' })).json();
    profiles = d.profiles;
    baseRtt = d.base_rtt_ms;
    $('liveText').textContent = baseRtt == null
      ? 'Live link: waiting for the headset to report its round-trip time…'
      : `Live private-5G round trip (headset ↔ edge): ${baseRtt.toFixed(0)} ms · network on the dashboard: ${d.profile.label}`;
    $('svgRtt').textContent = baseRtt == null ? 'RTT — ms' : `measured RTT ${baseRtt.toFixed(0)} ms`;
    drawCharts();
    drawResults(d.results);
  } catch (e) {
    $('liveText').textContent = 'Server not reachable';
  }
}

drawTable();
refresh();
setInterval(refresh, 3000);
