// Operator link: WebSocket to the server, 50 Hz commands, RTT from acks.
// RTT uses only this device's clock (t_op is echoed back), so no clock sync is needed.

const CMD_HZ = 50;
const LINK_LOST_MS = 300;

export class Net {
  constructor({ onState, onNotice, onHello }) {
    this.onState = onState;
    this.onNotice = onNotice;
    this.onHello = onHello;
    this.ws = null;
    this.seq = 0;
    this.connected = false;
    this.rtts = [];            // last 100 samples (ms)
    this.lastRtt = null;
    this.lastAckAt = 0;
    this.lastStateAt = 0;
    this.stateTimes = [];
    this.sentTimes = [];
    this.getInput = () => null;  // set by app: returns {clutch,vx,vy,vz,gripper} or null to stay silent
    this.connect();
    setInterval(() => this.tick(), 1000 / CMD_HZ);
    setInterval(() => this.reportRtt(), 1000);
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws/operator`);
    this.ws = ws;
    ws.onopen = () => { this.connected = true; this.replaced = false; this.seq = 0; this.rtts = []; };
    ws.onclose = (ev) => {
      this.connected = false;
      if (ev.code === 4001) { this.replaced = true; return; }   // another operator took over: don't fight back
      if (this.ws === ws) setTimeout(() => this.connect(), 1000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      const now = performance.now();
      if (m.type === 'ack') {
        const rtt = now - m.t_op;
        this.lastRtt = rtt;
        this.lastAckAt = now;
        this.rtts.push(rtt);
        if (this.rtts.length > 100) this.rtts.shift();
      } else if (m.type === 'state') {
        this.lastStateAt = now;
        this.stateTimes.push(now);
        while (this.stateTimes.length && now - this.stateTimes[0] > 1000) this.stateTimes.shift();
        this.onState(m, now);
      } else if (m.type === 'notice') {
        this.onNotice(m.text);
      } else if (m.type === 'hello') {
        this.onHello?.(m.geometry, m);
      }
    };
  }

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  control(action) {
    if (this.replaced && action === 'arm') { this.replaced = false; this.connect(); return; }  // take control back
    this.send({ type: 'control', action });
  }

  tick() {
    const inp = this.getInput();
    if (!inp) return;                  // page hidden etc.: stay silent -> robot watchdog holds
    const now = performance.now();
    this.sentTimes.push(now);
    while (this.sentTimes.length && now - this.sentTimes[0] > 1000) this.sentTimes.shift();
    this.send({ type: 'command', seq: this.seq++, t_op: now, ...inp });
  }

  percentile(p) {
    if (!this.rtts.length) return null;
    const s = [...this.rtts].sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor(p * s.length))];
  }

  reportRtt() {
    if (!this.rtts.length) return;
    const r = (v) => v == null ? null : Math.round(v * 10) / 10;
    this.send({ type: 'rtt', p50: r(this.percentile(0.5)), p95: r(this.percentile(0.95)),
                last: r(this.lastRtt), n: this.rtts.length });
  }

  get linkLost() {
    return !this.connected || performance.now() - this.lastAckAt > LINK_LOST_MS;
  }
  get stateHz() { return this.stateTimes.length; }
  get cmdHz() { return this.sentTimes.length; }
}
