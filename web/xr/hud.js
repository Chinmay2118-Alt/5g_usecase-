// Head-locked HUD drawn to a canvas texture (DOM is invisible inside immersive VR).
import * as THREE from '../vendor/three.module.js';

const W = 1024, H = 400;

function rttColor(ms) {
  if (ms == null) return '#a0aec0';
  if (ms < 60) return '#4ade80';
  if (ms < 150) return '#facc15';
  return '#f87171';
}

export class Hud {
  constructor(camera) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W; this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(0.40, 0.40 * H / W),
      new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, depthTest: false }));
    mesh.renderOrder = 100;
    mesh.position.set(0, -0.17, -0.6);
    mesh.rotation.x = -0.25;
    camera.add(mesh);
    this.mesh = mesh;
    this.notice = null;
    this.noticeUntil = 0;
  }

  showNotice(text) {
    this.notice = text;
    this.noticeUntil = performance.now() + 3000;
  }

  draw({ net, state, input, source }) {
    const g = this.ctx;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(10,14,22,0.72)';
    g.beginPath(); g.roundRect(0, 0, W, H, 24); g.fill();

    const robot = state?.robot?.state ?? '—';
    const sim = state?.sim;
    const p50 = net.percentile(0.5), p95 = net.percentile(0.95);

    // Banner
    let banner = null, bannerColor = '#b91c1c';
    if (net.replaced) { banner = 'ANOTHER OPERATOR HAS CONTROL — press A to take over'; bannerColor = '#374151'; }
    else if (net.linkLost) banner = 'LINK LOST — ROBOT HOLDING';
    else if (sim?.intruder) banner = 'PERSON IN WORKSPACE — AI STOP';
    else if (robot === 'HOLDING') { banner = 'ROBOT HOLDING — release grip, press A to re-arm'; bannerColor = '#b45309'; }
    else if (robot === 'DISARMED') { banner = 'DISARMED — release grip, press A to arm'; bannerColor = '#374151'; }
    if (banner) {
      g.fillStyle = bannerColor;
      g.beginPath(); g.roundRect(16, 14, W - 32, 78, 16); g.fill();
      g.fillStyle = '#fff'; g.font = 'bold 40px system-ui, sans-serif';
      g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(banner, W / 2, 54);
      g.textAlign = 'left';
    }

    g.textBaseline = 'alphabetic';
    g.font = 'bold 46px ui-monospace, monospace';
    g.fillStyle = rttColor(p50);
    g.fillText(`RTT ${p50 == null ? '—' : p50.toFixed(0)} ms`, 32, 156);
    g.font = '30px ui-monospace, monospace';
    g.fillStyle = rttColor(p95);
    g.fillText(`p95 ${p95 == null ? '—' : p95.toFixed(0)}`, 360, 156);
    g.fillStyle = '#cbd5e0';
    const added = state?.net?.added_rtt_ms ?? 0;
    g.fillText(`added ${added.toFixed(0)} ms`, 560, 156);

    g.fillText(`state ${net.stateHz} Hz   cmd ${net.cmdHz} Hz   ${source}`, 32, 210);
    const rc = robot === 'ARMED' ? '#4ade80' : robot === 'HOLDING' ? '#fbbf24' : '#a0aec0';
    g.fillStyle = rc; g.font = 'bold 34px system-ui, sans-serif';
    g.fillText(`ROBOT ${robot}`, 32, 266);
    g.fillStyle = input?.clutch ? '#4ade80' : '#718096';
    g.fillText(input?.clutch ? '● CLUTCH' : '○ clutch', 380, 266);
    g.fillStyle = input?.gripper ? '#fbbf24' : '#718096';
    g.fillText(input?.gripper ? '● GRIP' : '○ grip', 620, 266);

    if (sim) {
      g.fillStyle = sim.done ? '#4ade80' : '#e2e8f0';
      g.font = 'bold 34px system-ui, sans-serif';
      const t = sim.task_s == null ? '—' : `${sim.task_s.toFixed(1)} s`;
      g.fillText(`Bin ${sim.score}/${sim.target}   Time ${t}${sim.done ? '   DONE' : ''}`, 32, 322);
    }
    if (this.notice && performance.now() < this.noticeUntil) {
      g.fillStyle = '#93c5fd'; g.font = '28px system-ui, sans-serif';
      g.fillText(this.notice, 32, 372);
    }
    this.tex.needsUpdate = true;
  }
}
