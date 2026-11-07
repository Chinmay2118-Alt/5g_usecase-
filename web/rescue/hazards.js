// Per-disaster visuals on the map table (the server decides what is passable;
// these only draw it). Created by FloodMap.build() for the current scenario.
import * as THREE from '../vendor/three.module.js';

function rng(seed) {                       // small deterministic PRNG for stable decoration
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export function decodeLayer(b64, n) {
  const raw = atob(b64);
  const out = new Uint8Array(n);
  for (let k = 0; k < n; k++) out[k] = (raw.charCodeAt(k >> 2) >> ((k & 3) * 2)) & 3;
  return out;
}

export function makeHazard(fmap, m) {
  switch (m.hazard?.type) {
    case 'earthquake': return new Quake(fmap, m);
    case 'wildfire': return new Fire(fmap, m);
    case 'landslide': return new Slide(fmap, m);
    default: return new Hazard(fmap, m);
  }
}

class Hazard {
  constructor(fmap, m) { this.fmap = fmap; this.m = m; }
  update(_sim) {}
  applyLayer(_layer) {}
  animate(_dt, _t) {}
}

// ---------------------------------------------------------------- earthquake
class Quake extends Hazard {
  constructor(fmap, m) {
    super(fmap, m);
    const R = rng(42);
    const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95 });
    const col = new THREE.Color();
    const o = new THREE.Object3D();
    // Rubble heaps where houses collapsed
    const collapsed = m.hazard.collapsed || [];
    this.heaps = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), mat, Math.max(1, collapsed.length * 9));
    let n = 0;
    for (const bi of collapsed) {
      const [x, z, w, d, h] = m.buildings[bi];
      const g = fmap.groundAt(x, z);
      for (let i = 0; i < 9; i++) {
        const s = 1.4 + R() * 2.6;
        o.position.set(x + (R() - 0.5) * (w + 2), g + s * 0.3 + R() * h * 0.25, z + (R() - 0.5) * (d + 2));
        o.rotation.set(R() * 1.2, R() * 3.1, R() * 1.2);
        o.scale.set(s, s * (0.4 + R() * 0.5), s * (0.6 + R() * 0.6));
        o.updateMatrix();
        this.heaps.setMatrixAt(n, o.matrix);
        this.heaps.setColorAt(n, col.setHSL(0.07, 0.12, 0.38 + R() * 0.2));
        n++;
      }
    }
    this.heaps.count = n;
    fmap.map.add(this.heaps);
    // Debris piles on roads (grow with aftershocks)
    this.piles = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), mat.clone(), 60 * 7);
    this.piles.count = 0;
    fmap.map.add(this.piles);
    this.pileKey = '';
    this.quakeT = null;
    this.shakeUntil = 0;
  }

  update(sim) {
    const piles = sim.hz?.piles || [];
    const key = piles.length + ':' + (piles[0]?.join(',') ?? '');
    if (key !== this.pileKey) {
      this.pileKey = key;
      const o = new THREE.Object3D(), col = new THREE.Color();
      let n = 0;
      piles.forEach(([x, z, r], pi) => {
        const R = rng(1000 + pi);
        for (let i = 0; i < 7 && n < 60 * 7; i++) {
          const a = R() * Math.PI * 2, d = R() * r * 0.8, s = 1.2 + R() * 2.2;
          const px = x + Math.cos(a) * d, pz = z + Math.sin(a) * d;
          o.position.set(px, this.fmap.groundAt(px, pz) + s * 0.3, pz);
          o.rotation.set(R(), R() * 3, R());
          o.scale.set(s, s * 0.6, s * 0.8);
          o.updateMatrix();
          this.piles.setMatrixAt(n, o.matrix);
          this.piles.setColorAt(n, col.setHSL(0.08, 0.15, 0.35 + R() * 0.2));
          n++;
        }
      });
      this.piles.count = n;
      this.piles.instanceMatrix.needsUpdate = true;
      if (this.piles.instanceColor) this.piles.instanceColor.needsUpdate = true;
    }
    // Table shake when a new aftershock happens
    const q = sim.hz?.quake_t;
    if (q != null && q !== this.quakeT) {
      if (this.quakeT !== null || sim.t - q < 2) this.shakeUntil = performance.now() + 1500;
      this.quakeT = q;
    }
  }

  animate(_dt, t) {
    const base = this.fmap.mapBase;
    if (!base) return;
    if (t < this.shakeUntil) {
      const k = (this.shakeUntil - t) / 1500 * 0.006;
      this.fmap.map.position.set(base.x + (Math.random() - 0.5) * k, base.y + (Math.random() - 0.5) * k * 0.5, base.z + (Math.random() - 0.5) * k);
    } else {
      this.fmap.map.position.copy(base);
    }
  }
}

// ---------------------------------------------------------------- wildfire
class Fire extends Hazard {
  constructor(fmap, m) {
    super(fmap, m);
    const n = m.n, cells = n * n;
    // height of each cell's top: roofs for building cells, ground otherwise
    this.top = new Float32Array(cells);
    this.bldg = new Int32Array(cells).fill(-1);
    for (let k = 0; k < cells; k++) {
      const [x, z] = this.center(k);
      this.top[k] = fmap.groundAt(x, z);
    }
    m.buildings.forEach(([x, z, w, d, h], bi) => {
      for (let k = 0; k < cells; k++) {
        const [cx, cz] = this.center(k);
        if (Math.abs(cx - x) <= w / 2 + 0.2 && Math.abs(cz - z) <= d / 2 + 0.2) {
          this.top[k] = fmap.groundAt(x, z) + h;
          this.bldg[k] = bi;
        }
      }
    });
    this.flames = new THREE.InstancedMesh(new THREE.ConeGeometry(1.0, 3.2, 6),
      new THREE.MeshBasicMaterial({ color: 0xff7a1a, transparent: true, opacity: 0.85 }), 5000);
    this.flames.count = 0;
    this.cores = new THREE.InstancedMesh(new THREE.ConeGeometry(0.55, 2.0, 5),
      new THREE.MeshBasicMaterial({ color: 0xffe066 }), 5000);
    this.cores.count = 0;
    fmap.map.add(this.flames, this.cores);
    this.burning = [];
    this.state = null;
    this.v = -1;
    if (m.layer) this.applyLayer({ v: m.layer_v, data: m.layer, scenario: 'wildfire' });
  }

  center(k) {
    const n = this.m.n, half = this.m.size / 2, c = this.m.cell;
    return [-half + ((k % n) + 0.5) * c, -half + (Math.floor(k / n) + 0.5) * c];
  }

  applyLayer(layer) {
    if (!layer || layer.v === this.v) return;
    this.v = layer.v;
    const n = this.m.n;
    const st = decodeLayer(layer.data, n * n);
    this.state = st;
    this.burning = [];
    for (let k = 0; k < st.length; k++) if (st[k] === 1) this.burning.push(k);
    // Terrain: burnt = charcoal, burning = glowing dark red; others back to base colour
    const f = this.fmap;
    const r = n + 1;
    for (let iz = 0; iz <= n; iz++) for (let ix = 0; ix <= n; ix++) {
      let worst = 0;
      for (const [ci, cj] of [[ix - 1, iz - 1], [ix, iz - 1], [ix - 1, iz], [ix, iz]]) {
        if (ci < 0 || cj < 0 || ci >= n || cj >= n) continue;
        const s = st[cj * n + ci];
        if (s === 1) worst = 2; else if (s === 2 && worst < 1) worst = 1;
      }
      const v = iz * r + ix;
      if (worst === 2) f.tintVertex(v, 0.35, 0.09, 0.03);
      else if (worst === 1) f.tintVertex(v, 0.09, 0.08, 0.07);
      else f.restoreVertex(v);
    }
    f.colorsChanged();
    // Burnt houses turn black
    const burntB = new Set();
    for (let k = 0; k < st.length; k++) if (st[k] === 2 && this.bldg[k] >= 0) burntB.add(this.bldg[k]);
    f.setBuildingTint(burntB, 0x1b1b1b);
  }

  animate(_dt, t) {
    const o = new THREE.Object3D();
    const list = this.burning, cnt = Math.min(list.length, 5000);
    for (let i = 0; i < cnt; i++) {
      const k = list[i];
      const [x, z] = this.center(k);
      const flick = 0.75 + 0.35 * Math.sin(t * 0.012 + k * 1.7) + 0.15 * Math.sin(t * 0.031 + k);
      const big = this.bldg[k] >= 0 ? 1.6 : 1.0;
      o.position.set(x, this.top[k] + 1.5 * flick * big, z);
      o.rotation.set(0, k, 0);
      o.scale.set(big, flick * big, big);
      o.updateMatrix();
      this.flames.setMatrixAt(i, o.matrix);
      o.position.y = this.top[k] + 1.0 * flick * big;
      o.scale.set(big, flick * big * 0.9, big);
      o.updateMatrix();
      this.cores.setMatrixAt(i, o.matrix);
    }
    this.flames.count = cnt;
    this.cores.count = cnt;
    this.flames.instanceMatrix.needsUpdate = true;
    this.cores.instanceMatrix.needsUpdate = true;
  }
}

// ---------------------------------------------------------------- landslide
function segDist(px, pz, ax, az, bx, bz) {
  const L2 = (bx - ax) ** 2 + (bz - az) ** 2;
  const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / L2));
  return Math.hypot(px - (ax + (bx - ax) * t), pz - (az + (bz - az) * t));
}

class Slide extends Hazard {
  constructor(fmap, m) {
    super(fmap, m);
    this.key = '';
    this.boulders = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(1, 0),
      new THREE.MeshStandardMaterial({ color: 0x6b5b4b, roughness: 1 }), 4 * 40);
    this.boulders.count = 0;
    fmap.map.add(this.boulders);
  }

  update(sim) {
    const slides = sim.hz?.slides || [];
    const key = String(slides.length);
    if (key === this.key) return;
    this.key = key;
    const f = this.fmap, m = this.m, n = m.n, half = m.size / 2, r = n + 1;
    for (let iz = 0; iz <= n; iz++) for (let ix = 0; ix <= n; ix++) {
      const x = -half + ix * m.cell, z = -half + iz * m.cell, v = iz * r + ix;
      let best = 0;
      for (const [ax, az, bx, bz, w] of slides) {
        const d = segDist(x, z, ax, az, bx, bz);
        if (d <= w / 2 * 0.55) best = 2; else if (d <= w / 2 && best < 1) best = 1;
      }
      if (best === 2) f.tintVertex(v, 0.30, 0.20, 0.11);
      else if (best === 1) f.tintVertex(v, 0.42, 0.31, 0.19);
      else f.restoreVertex(v);
    }
    f.colorsChanged();
    const o = new THREE.Object3D();
    let cnt = 0;
    slides.forEach(([ax, az, bx, bz, w], si) => {
      const R = rng(77 + si);
      for (let i = 0; i < 40; i++) {
        const t = R(), off = (R() - 0.5) * w * 0.6;
        const len = Math.hypot(bx - ax, bz - az) || 1;
        const nx = -(bz - az) / len, nz = (bx - ax) / len;
        const x = ax + (bx - ax) * t + nx * off, z = az + (bz - az) * t + nz * off;
        const s = 0.5 + R() * 1.4;
        o.position.set(x, f.groundAt(x, z) + s * 0.5, z);
        o.rotation.set(R() * 3, R() * 3, R() * 3);
        o.scale.setScalar(s);
        o.updateMatrix();
        this.boulders.setMatrixAt(cnt++, o.matrix);
      }
    });
    this.boulders.count = cnt;
    this.boulders.instanceMatrix.needsUpdate = true;
  }
}
