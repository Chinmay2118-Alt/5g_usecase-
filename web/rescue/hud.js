// Head-locked HUD for the rescue rover (canvas texture; DOM is invisible in VR).
import * as THREE from '../vendor/three.module.js';

const W = 1024, H = 460;
const rttColor = (ms) => (ms == null ? '#a0aec0' : ms < 60 ? '#4ade80' : ms < 150 ? '#facc15' : '#f87171');
const MODE_COLOR = { AUTO: '#38bdf8', MANUAL: '#4ade80', IDLE: '#cbd5e0', HOLD: '#fbbf24' };

export class RescueHud {
  constructor(camera) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W; this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.40, 0.40 * H / W),
      new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, depthTest: false }));
    this.mesh.renderOrder = 100;
    this.mesh.position.set(0, -0.2, -0.62);
    this.mesh.rotation.x = -0.3;
    camera.add(this.mesh);
    this.notice = null;
    this.noticeUntil = 0;
  }

  showNotice(text) { this.notice = text; this.noticeUntil = performance.now() + 4000; }

  draw({ net, state, input, source, hit }) {
    const g = this.ctx;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(10,14,22,0.74)';
    g.beginPath(); g.roundRect(0, 0, W, H, 24); g.fill();
    const robot = state?.robot?.state ?? '—';
    const reason = state?.robot?.reason ?? '';
    const s = state?.sim;
    const hz = s?.hz || {};
    const p50 = net.percentile(0.5), p95 = net.percentile(0.95);

    let banner = null, bc = '#b91c1c';
    if (net.replaced) { banner = 'ANOTHER OPERATOR HAS CONTROL — press A (or R) to take over'; bc = '#374151'; }
    else if (net.linkLost) banner = 'LINK LOST — ROVER HOLDING';
    else if (s?.hazard) banner = s.ai_pending ? 'PERSON AHEAD — AI stop on its way…' : 'PERSON AHEAD — AI STOP';
    else if (s?.stop_test && s.stop_test.age < 6 && s.stop_test.result !== 'stationary') {
      const t = s.stop_test;
      banner = t.result === 'would have hit' ? `WOULD HAVE HIT — AI stop took ${t.reaction_ms} ms`
        : `STOPPED ${t.margin_m.toFixed(1)} m SHORT — AI stop in ${t.reaction_ms} ms`;
      bc = t.result === 'would have hit' ? '#b91c1c' : '#15803d';
    }
    else if (robot === 'HOLDING' && reason === 'stop_overheat') banner = 'OVERHEATED — re-arm (A) and drive away from the fire';
    else if (hz.overheated) { banner = 'ROVER HOT — half speed, move away from the fire'; bc = '#c2410c'; }
    else if (robot === 'HOLDING') { banner = 'ROVER HOLDING — release grip, press A to re-arm'; bc = '#b45309'; }
    else if (robot === 'DISARMED') { banner = 'DISARMED — release grip, press A to arm'; bc = '#374151'; }
    else if (s?.done) {
      banner = `MISSION COMPLETE — ${s.score} rescued` + (s.airlifted ? `, ${s.airlifted} airlifted` : '') + ` in ${s.task_s.toFixed(0)} s`;
      bc = '#15803d';
    }
    if (banner) {
      g.fillStyle = bc; g.beginPath(); g.roundRect(16, 14, W - 32, 76, 16); g.fill();
      g.fillStyle = '#fff'; g.font = `bold ${banner.length > 44 ? 32 : 38}px system-ui, sans-serif`;
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(banner, W / 2, 53);
      g.textAlign = 'left';
    }
    g.textBaseline = 'alphabetic';

    g.font = 'bold 46px ui-monospace, monospace'; g.fillStyle = rttColor(p50);
    g.fillText(`RTT ${p50 == null ? '—' : p50.toFixed(0)} ms`, 30, 154);
    g.font = '30px ui-monospace, monospace'; g.fillStyle = rttColor(p95);
    g.fillText(`p95 ${p95 == null ? '—' : p95.toFixed(0)}`, 360, 154);
    g.fillStyle = '#cbd5e0';
    g.fillText(`added ${(state?.net?.added_rtt_ms ?? 0).toFixed(0)} ms`, 560, 154);
    g.font = '28px system-ui, sans-serif';
    const prof = state?.net?.profile;
    g.fillStyle = prof && !prof.live ? '#fbbf24' : '#cbd5e0';
    g.fillText(`${s?.title ?? ''} · ${prof ? prof.label : ''}${prof && !prof.live ? ' (emulated)' : ''} · ${source}`, 30, 198);

    if (s) {
      const mode = robot === 'ARMED' ? s.mode : 'HOLD';
      g.font = 'bold 38px system-ui, sans-serif'; g.fillStyle = MODE_COLOR[mode] ?? '#fff';
      g.fillText(mode, 30, 252);
      g.fillStyle = '#e2e8f0';
      g.fillText(`${(Math.abs(s.rover.v) * 3.6).toFixed(0)} km/h`, 230, 252);
      const terr = s.blocked ? ['BLOCKED', '#f87171'] : s.terrain_label ? [s.terrain_label, '#facc15'] : ['clear', '#94a3b8'];
      g.fillStyle = terr[1]; g.font = 'bold 30px system-ui, sans-serif';
      g.fillText(terr[0], 450, 252);
      g.fillStyle = input?.clutch ? '#4ade80' : '#64748b';
      g.fillText(input?.clutch ? '● MANUAL GRIP' : '○ grip', 740, 252);

      g.font = 'bold 34px system-ui, sans-serif'; g.fillStyle = '#e2e8f0';
      const t = s.task_s == null ? '—' : `${s.task_s.toFixed(0)} s`;
      g.fillText(`Rescued ${s.score}/${s.target}` + (s.airlifted ? ` (+${s.airlifted} airlifted)` : '') +
        `   On board ${s.onboard}/${s.capacity}   Time ${t}`, 30, 306);

      // Heat bar (wildfire)
      let y = 350;
      if (hz.heat != null) {
        g.font = '26px system-ui, sans-serif'; g.fillStyle = '#cbd5e0';
        g.fillText('Heat', 30, y);
        g.fillStyle = '#1f2937'; g.fillRect(100, y - 22, 300, 24);
        const hcol = hz.heat >= 80 ? '#ef4444' : hz.heat >= 50 ? '#f97316' : '#facc15';
        g.fillStyle = hcol; g.fillRect(100, y - 22, 3 * Math.min(100, hz.heat), 24);
        g.fillStyle = '#cbd5e0'; g.fillText(`${hz.heat.toFixed(0)}%`, 412, y);
        g.fillText(`wind → ${hz.compass} · fire ${hz.speed}`, 500, y);
        y += 44;
      }
      const loading = s.victims.find((v) => v.load > 0 && v.status === 'waiting');
      g.font = '28px system-ui, sans-serif';
      if (loading) {
        g.fillStyle = '#fbbf24';
        g.fillText(`Loading survivor ${loading.id + 1}… ${(loading.load * 100).toFixed(0)}%  (stay still)`, 30, y);
      } else {
        g.fillStyle = '#93c5fd';
        g.fillText((hz.heat != null ? '' : `${s.stage}   `) + (s.goal ? '· route active ' : '') + (hit ? '· trigger = send rover here' : ''), 30, y);
      }
      y += 42;
      if (this.notice && performance.now() < this.noticeUntil && y < H) {
        g.fillStyle = '#e0f2fe'; g.font = '28px system-ui, sans-serif';
        g.fillText(this.notice, 30, Math.min(y, H - 14));
      }
    }
    this.tex.needsUpdate = true;
  }
}
