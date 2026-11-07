// Top-down map drawing shared by the dashboard and the cockpit's mini-map tablet.
// North (-Z) is up, +X is right, matching the server's map frame.

export function decodeLayer(b64, n) {
  const raw = atob(b64), out = new Uint8Array(n);
  for (let k = 0; k < n; k++) out[k] = (raw.charCodeAt(k >> 2) >> ((k & 3) * 2)) & 3;
  return out;
}

function segDist(px, pz, ax, az, bx, bz) {
  const L2 = (bx - ax) ** 2 + (bz - az) ** 2;
  const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / L2));
  return Math.hypot(px - (ax + (bx - ax) * t), pz - (az + (bz - az) * t));
}

export class TopDown {
  constructor(m) {
    this.m = m;
    this.fire = null;
    this.fireV = -1;
    this.base = null;
    this.baseKey = '';
    this.collapsed = new Set(m.hazard?.collapsed || []);
    if (m.layer) this.setLayer({ v: m.layer_v, data: m.layer, scenario: m.hazard?.type });
  }

  setLayer(layer) {
    if (!layer || layer.scenario !== this.m.hazard?.type || layer.v === this.fireV) return;
    this.fire = decodeLayer(layer.data, this.m.n * this.m.n);
    this.fireV = layer.v;
  }

  cornerH(ix, iz) { return this.m.heights[iz * (this.m.n + 1) + ix]; }

  buildBase(s) {
    const m = this.m;
    const c = document.createElement('canvas');
    c.width = m.n; c.height = m.n;
    const g = c.getContext('2d');
    const img = g.createImageData(m.n, m.n);
    const half = m.size / 2, type = m.hazard?.type;
    const slides = s.hz?.slides || [], piles = s.hz?.piles || [];
    for (let j = 0; j < m.n; j++) for (let i = 0; i < m.n; i++) {
      const k = j * m.n + i;
      const h = (this.cornerH(i, j) + this.cornerH(i + 1, j) + this.cornerH(i, j + 1) + this.cornerH(i + 1, j + 1)) / 4;
      const x = -half + (i + 0.5) * m.cell, z = -half + (j + 0.5) * m.cell;
      const depth = s.level - h;
      const road = m.roads.x.some((rx) => Math.abs(x - rx) < m.roads.w / 2) || m.roads.z.some((rz) => Math.abs(z - rz) < m.roads.w / 2);
      let rgb;
      if (depth > m.deep) rgb = [30, 58, 138];
      else if (depth > m.shallow) rgb = [96, 165, 250];
      else if (road) rgb = [92, 98, 108];
      else { const q = Math.min(1, Math.max(0, (h - 1) / 10)); rgb = [70 + 60 * q, 95 + 50 * q, 55 + 30 * q]; }
      if (type === 'wildfire' && this.fire) {
        if (this.fire[k] === 1) rgb = [255, 106, 0];
        else if (this.fire[k] === 2) rgb = [28, 25, 23];
      } else if (type === 'landslide') {
        for (const [ax, az, bx, bz, w] of slides) {
          const d = segDist(x, z, ax, az, bx, bz);
          if (d <= w / 2 * 0.55) { rgb = [91, 65, 40]; break; }
          if (d <= w / 2) rgb = [138, 106, 69];
        }
      } else if (type === 'earthquake') {
        for (const [px, pz, r] of piles) {
          const d = Math.hypot(x - px, z - pz);
          if (d <= r) { rgb = [139, 125, 107]; break; }
          if (d <= r + 3) rgb = [184, 168, 143];
        }
      }
      img.data.set([...rgb, 255], k * 4);
    }
    g.putImageData(img, 0, 0);
    return c;
  }

  // Draw the whole map into a W x W area of `g`. opts: { wind, reticle: {x,z}, big }
  draw(g, W, s, opts = {}) {
    const m = this.m;
    const key = `${s.level}|${this.fireV}|${(s.hz?.slides || []).length}|${(s.hz?.piles || []).length}`;
    if (!this.base || key !== this.baseKey) { this.base = this.buildBase(s); this.baseKey = key; }
    const half = m.size / 2, sc = W / m.size, k = opts.big || 1;
    const P = (x, z) => [(x + half) * sc, (z + half) * sc];
    g.imageSmoothingEnabled = false;
    g.drawImage(this.base, 0, 0, W, W);
    m.buildings.forEach(([x, z, w, d], i) => {
      const [px, pz] = P(x - w / 2, z - d / 2);
      if (this.collapsed.has(i)) {
        g.fillStyle = '#8b7d6b';
        g.beginPath(); g.ellipse(px + w * sc / 2, pz + d * sc / 2, (w / 2 + 1.5) * sc, (d / 2 + 1.5) * sc, 0.3, 0, 7); g.fill();
        return;
      }
      g.fillStyle = '#3f3a36'; g.strokeStyle = '#1f1c1a'; g.lineWidth = 1;
      g.fillRect(px, pz, w * sc, d * sc); g.strokeRect(px, pz, w * sc, d * sc);
    });
    const [cx, cz] = P(m.camp[0], m.camp[1]);
    g.strokeStyle = '#22c55e'; g.lineWidth = 3 * k;
    g.beginPath(); g.arc(cx, cz, m.camp[2] * sc, 0, 7); g.stroke();
    g.fillStyle = '#22c55e'; g.font = `bold ${13 * k}px system-ui`; g.fillText('CAMP', cx - 18 * k, cz - m.camp[2] * sc - 6);

    if (s.path.length) {
      g.strokeStyle = '#00e5ff'; g.lineWidth = 3 * k; g.setLineDash([8 * k, 6 * k]); g.beginPath();
      g.moveTo(...P(s.rover.x, s.rover.z));
      for (const [x, z] of s.path.slice(1)) g.lineTo(...P(x, z));
      g.stroke(); g.setLineDash([]);
    }
    if (s.goal) { const [gx, gz] = P(...s.goal); g.strokeStyle = '#00e5ff'; g.lineWidth = 3 * k; g.beginPath(); g.arc(gx, gz, 7 * k, 0, 7); g.stroke(); }

    s.victims.forEach((v, i) => {
      const [x, z] = m.victims[i];
      const [px, pz] = P(x, z);
      if (v.status === 'rescued') return;
      g.fillStyle = v.status === 'onboard' ? '#facc15' : v.status === 'airlifted' ? '#8e8e93' : '#ef4444';
      g.beginPath(); g.arc(px, pz, 8 * k, 0, 7); g.fill();
      g.fillStyle = '#fff'; g.font = `bold ${11 * k}px system-ui`;
      g.fillText(v.status === 'airlifted' ? '✈' : String(i + 1), px - 4 * k, pz + 4 * k);
      if (v.load > 0 && v.status === 'waiting') {
        g.strokeStyle = '#facc15'; g.lineWidth = 3 * k;
        g.beginPath(); g.arc(px, pz, 13 * k, -Math.PI / 2, -Math.PI / 2 + v.load * Math.PI * 2); g.stroke();
      }
    });
    if (s.hazard) {
      const [hx, hz] = P(...s.hazard);
      g.fillStyle = '#ff2d55'; g.beginPath(); g.arc(hx, hz, 6 * k, 0, 7); g.fill();
      g.font = `bold ${12 * k}px system-ui`; g.fillText('PERSON', hx + 8 * k, hz + 4 * k);
    }
    if (opts.wind && m.hazard?.type === 'wildfire' && s.hz?.wind) {
      const [wx, wz] = s.hz.wind;
      g.strokeStyle = '#fde68a'; g.fillStyle = '#fde68a'; g.lineWidth = 3 * k;
      const ox = W - 60 * k, oz = 50 * k, L = 25 * k;
      g.beginPath(); g.moveTo(ox - wx * L, oz - wz * L); g.lineTo(ox + wx * L, oz + wz * L); g.stroke();
      g.beginPath(); g.arc(ox + wx * L, oz + wz * L, 5 * k, 0, 7); g.fill();
      g.font = `${12 * k}px system-ui`; g.fillText('wind', ox - 14 * k, oz + 42 * k);
    }
    if (opts.reticle) {
      const [rx, rz] = P(opts.reticle.x, opts.reticle.z);
      g.strokeStyle = '#ffffff'; g.lineWidth = 3 * k;
      g.beginPath(); g.arc(rx, rz, 10 * k, 0, 7); g.moveTo(rx - 16 * k, rz); g.lineTo(rx + 16 * k, rz);
      g.moveTo(rx, rz - 16 * k); g.lineTo(rx, rz + 16 * k); g.stroke();
    }
    // rover (triangle pointing along its heading: forward = (sin yaw, cos yaw))
    const [rx, rz] = P(s.rover.x, s.rover.z);
    g.save(); g.translate(rx, rz); g.rotate(-s.rover.yaw);
    g.fillStyle = '#f59e0b'; g.strokeStyle = '#000'; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(0, 11 * k); g.lineTo(-7 * k, -7 * k); g.lineTo(7 * k, -7 * k); g.closePath(); g.fill(); g.stroke();
    g.restore();
  }
}
