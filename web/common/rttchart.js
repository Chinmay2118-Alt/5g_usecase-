// RTT-over-time chart for the dashboards (p50, p95 and the injected delay).
export class RttChart {
  constructor(canvas, windowS = 120) {
    this.c = canvas;
    this.windowS = windowS;
    this.history = [];
    this.lastKey = '';
  }

  push(state) {
    const r = state.client_rtt || {};
    const key = `${r.p50}|${r.p95}|${r.n}|${r.last}`;
    if (r.p50 == null || key === this.lastKey) return;
    this.lastKey = key;
    this.history.push({ t: state.t_srv, p50: r.p50, p95: r.p95, added: state.net.added_rtt_ms });
    while (this.history.length && state.t_srv - this.history[0].t > this.windowS + 30) this.history.shift();
  }

  draw() {
    const c = this.c, g = c.getContext('2d');
    const W = c.width, H = c.height, pad = 34, h = this.history;
    g.clearRect(0, 0, W, H);
    if (!h.length) {
      g.fillStyle = '#64748b'; g.font = '14px system-ui';
      g.fillText('Waiting for the headset to report RTT…', pad, H / 2);
      return;
    }
    const tEnd = h[h.length - 1].t, tStart = tEnd - this.windowS;
    const vis = h.filter((p) => p.t >= tStart);
    const maxV = Math.max(100, ...vis.map((p) => Math.max(p.p95, p.added))) * 1.15;
    const X = (t) => pad + (t - tStart) / this.windowS * (W - pad - 8);
    const Y = (v) => H - 20 - v / maxV * (H - 34);
    g.strokeStyle = '#2a303a'; g.fillStyle = '#64748b'; g.font = '12px system-ui'; g.lineWidth = 1;
    for (let v = 0; v <= maxV; v += maxV > 400 ? 200 : 50) {
      g.beginPath(); g.moveTo(pad, Y(v)); g.lineTo(W - 8, Y(v)); g.stroke();
      g.fillText(String(v), 2, Y(v) + 4);
    }
    g.fillText(`ms · last ${this.windowS / 60} min`, W - 100, H - 4);
    const line = (key, color, w) => {
      g.strokeStyle = color; g.lineWidth = w; g.beginPath();
      vis.forEach((p, i) => (i ? g.lineTo(X(p.t), Y(p[key])) : g.moveTo(X(p.t), Y(p[key]))));
      g.stroke();
    };
    line('added', '#64748b', 2); line('p95', '#f472b6', 2); line('p50', '#38bdf8', 3);
  }
}
