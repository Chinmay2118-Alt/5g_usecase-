// Miniature disaster map on a command table. Everything inside `this.map` uses
// the server's map frame (metres, Y up, x/z in [-120, 120], +Z towards the
// operator), scaled down onto the table. The map is rebuilt whenever the
// server sends a new 'hello' (the experimenter switched scenario).
import * as THREE from '../vendor/three.module.js';
import { makeHazard } from './hazards.js';

export const TABLE = 1.2;                 // metres of table for the 240 m map

const STATUS_COLOR = { waiting: 0xff3b30, onboard: 0xffcc00, rescued: 0x34c759, airlifted: 0x8e8e93 };

function mat(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.05, ...extra });
}

export function label(text, color = '#fff', bg = 'rgba(0,0,0,0.6)', w = 256) {
  const c = document.createElement('canvas');
  c.width = w; c.height = 64;
  const g = c.getContext('2d');
  const draw = (t, col) => {
    g.clearRect(0, 0, w, 64);
    g.fillStyle = bg; g.beginPath(); g.roundRect(0, 4, w, 56, 12); g.fill();
    g.font = 'bold 30px system-ui, sans-serif'; g.fillStyle = col;
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(t, w / 2, 33);
  };
  draw(text, color);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.renderOrder = 20;
  s.userData.set = (t, col = color) => {
    if (s.userData.t === t && s.userData.c === col) return;
    s.userData.t = t; s.userData.c = col; draw(t, col); tex.needsUpdate = true;
  };
  return s;
}

export class FloodMap {
  constructor(rig) {
    this.rig = rig;
    this.m = null;
    this.level = 0;
    this.targetLevel = 0;
    this.pathKey = '';
    this.hudOnly = [];        // hidden while the rover camera renders
  }

  ensureTable() {
    if (this.table) return;
    this.table = new THREE.Mesh(new THREE.BoxGeometry(TABLE + 0.12, 0.06, TABLE + 0.12), mat(0x2b2f36));
    this.table.position.y = -0.03;
    this.rig.add(this.table);
    for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.7, 0.05), mat(0x1e2126));
      leg.position.set(x * (TABLE / 2), -0.41, z * (TABLE / 2));
      this.rig.add(leg);
    }
  }

  // Remove the current map (scenario switch). The table, rover camera screen and HUD stay.
  clear() {
    if (!this.map) return;
    this.map.parent?.remove(this.map);       // on the table, or in the scene while in the cockpit
    this.map.traverse((o) => {
      o.geometry?.dispose?.();
      const mt = o.material;
      if (mt) (Array.isArray(mt) ? mt : [mt]).forEach((x) => { x.map?.dispose?.(); x.dispose(); });
    });
    this.hudOnly = this.hudOnly.filter((o) => o !== this.reticle && o !== this.rain);
    this.m = null; this.map = null; this.hz = null; this.pathKey = '';
  }

  build(m) {
    if (this.m) this.clear();
    this.m = m;
    this.type = m.hazard?.type || 'flood';
    const S = TABLE / m.size;
    this.S = S;
    const n = m.n, size = m.size, half = size / 2;
    let minH = Infinity, maxH = -Infinity;
    for (const h of m.heights) { minH = Math.min(minH, h); maxH = Math.max(maxH, h); }
    this.minH = minH; this.maxH = maxH;
    this.ensureTable();

    const map = new THREE.Group();
    map.scale.setScalar(S);
    map.position.y = (-minH + 0.3) * S;
    this.rig.add(map);
    this.map = map;
    this.mapBase = map.position.clone();
    const collapsed = new Set(m.hazard?.collapsed || []);

    // Terrain (vertex order matches the server's corner heights)
    const geo = new THREE.PlaneGeometry(size, size, n, n);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    const colors = new Float32Array(pos.count * 3);
    const col = new THREE.Color();
    const onRoad = (x, z) => m.roads.x.some((rx) => Math.abs(x - rx) < m.roads.w / 2) ||
                             m.roads.z.some((rz) => Math.abs(z - rz) < m.roads.w / 2);
    for (let k = 0; k < pos.count; k++) {
      const h = m.heights[k];
      pos.setY(k, h);
      const x = pos.getX(k), z = pos.getZ(k);
      const inCamp = Math.hypot(x - m.camp[0], z - m.camp[1]) < m.camp[2];
      if (inCamp) col.setHex(0xc8b88f);
      else if (onRoad(x, z)) col.setHex(h > 2.4 && m.bridges.some(([bx]) => Math.abs(x - bx) < 4) ? 0x9aa0a8 : 0x50555c);
      else if (h < 1.0) col.setHex(0x5a4a38);                       // river mud
      else col.setHSL(0.25 - Math.min(1, (h - 1) / 10) * 0.08, 0.35, 0.28 + Math.min(1, (h - 1) / 10) * 0.12);
      colors.set([col.r, col.g, col.b], k * 3);
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    this.colorAttr = geo.attributes.color;
    this.baseColors = colors.slice();
    geo.computeVertexNormals();
    this.terrain = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95 }));
    map.add(this.terrain);

    // Skirt around the terrain so the map looks like a solid block
    const skirtMat = mat(0x3b3226);
    for (const side of [0, 1, 2, 3]) {
      const pts = [];
      for (let i = 0; i <= n; i++) {
        const [ix, iz] = side === 0 ? [i, 0] : side === 1 ? [i, n] : side === 2 ? [0, i] : [n, i];
        pts.push([-half + ix * m.cell, m.heights[iz * (n + 1) + ix], -half + iz * m.cell]);
      }
      const g = new THREE.BufferGeometry();
      const v = [];
      for (let i = 0; i < n; i++) {
        const [a, b] = [pts[i], pts[i + 1]];
        const lo = minH - 0.3;
        v.push(a[0], a[1], a[2], b[0], b[1], b[2], b[0], lo, b[2], a[0], a[1], a[2], b[0], lo, b[2], a[0], lo, a[2]);
      }
      g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3));
      g.computeVertexNormals();
      map.add(new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0x3b3226, side: THREE.DoubleSide })));
    }

    // Water: one plane at the water level (the river, or the flood)
    this.level = this.targetLevel = m.water_level ?? 0.3;
    this.water = new THREE.Mesh(new THREE.PlaneGeometry(size, size).rotateX(-Math.PI / 2),
      new THREE.MeshStandardMaterial({ color: 0x2f6fb0, transparent: true, opacity: 0.72, roughness: 0.15, metalness: 0.2 }));
    this.water.position.y = this.level;
    map.add(this.water);

    // Buildings
    const inst = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), mat(0xffffff), m.buildings.length);
    const o = new THREE.Object3D();
    m.buildings.forEach(([x, z, w, d, h], i) => {
      const base = this.groundAt(x, z) - 1.0;
      o.position.set(x, base + (h + 1.0) / 2, z);
      o.scale.set(w, h + 1.0, d);
      if (collapsed.has(i)) o.scale.set(0, 0, 0);        // drawn as rubble by hazards.js
      o.updateMatrix();
      inst.setMatrixAt(i, o.matrix);
      inst.setColorAt(i, col.setHSL(0.07 + (i % 5) * 0.02, 0.15, 0.55 + (i % 3) * 0.08));
    });
    map.add(inst);
    this.buildingsMesh = inst;

    // Rescue camp
    const [cx, cz, cr] = m.camp;
    const cy = this.groundAt(cx, cz);
    const pad = new THREE.Mesh(new THREE.CircleGeometry(5, 32).rotateX(-Math.PI / 2), mat(0xeeeeee));
    pad.position.set(cx, cy + 0.15, cz);
    const cross1 = new THREE.Mesh(new THREE.BoxGeometry(6, 0.3, 1.6), mat(0xe5484d));
    const cross2 = cross1.clone(); cross2.rotation.y = Math.PI / 2;
    cross1.position.copy(pad.position); cross2.position.copy(pad.position);
    map.add(pad, cross1, cross2);
    for (const [dx, dz] of [[-9, 4], [7, -7], [6, 8]]) {
      const tent = new THREE.Mesh(new THREE.ConeGeometry(3, 3.5, 4), mat(0x3a7d44));
      tent.position.set(cx + dx, this.groundAt(cx + dx, cz + dz) + 1.75, cz + dz);
      tent.rotation.y = Math.PI / 4;
      map.add(tent);
    }
    const ring = new THREE.Mesh(new THREE.RingGeometry(cr - 0.8, cr, 48).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0x34c759, transparent: true, opacity: 0.6 }));
    ring.position.set(cx, cy + 0.4, cz);
    map.add(ring);
    const campLabel = label('RESCUE CAMP', '#7dffa8');
    campLabel.position.set(cx, cy + 14, cz);
    campLabel.scale.set(24, 6, 1);
    map.add(campLabel);
    this.rescuedLabel = label('0 rescued', '#7dffa8', 'rgba(0,0,0,0.6)', 420);
    this.rescuedLabel.position.set(cx, cy + 8.5, cz);
    this.rescuedLabel.scale.set(33, 5, 1);
    map.add(this.rescuedLabel);

    // Victims: person on the roof + a beacon you can see from anywhere
    this.victims = m.victims.map(([x, z, h0], i) => {
      const inRubble = collapsed.has(m.victim_buildings?.[i]);
      const h = inRubble ? Math.max(2.5, h0 * 0.35) : h0;   // on top of the rubble heap
      const roof = this.groundAt(x, z) + h;
      const g = new THREE.Group();
      g.position.set(x, roof, z);
      const person = new THREE.Mesh(new THREE.CapsuleGeometry(0.6, 1.4, 4, 8), mat(0xff9500));
      person.position.y = 1.3; person.scale.setScalar(1.6);
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(1.0, 1.0, 30, 12, 1, true),
        new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 0.35, depthWrite: false }));
      beam.position.y = 15;
      const tag = label(`SOS ${i + 1}`, '#ffffff', 'rgba(200,30,30,0.85)', 160);
      tag.position.y = 33; tag.scale.set(16, 6.4, 1);
      const prog = new THREE.Mesh(new THREE.RingGeometry(9, 10.5, 40, 1, 0, 0.01).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0xffcc00, side: THREE.DoubleSide }));
      prog.position.y = 0.5 - h;     // ring on the ground around the house
      prog.visible = false;
      g.add(person, beam, tag, prog);
      map.add(g);
      return { g, person, beam, tag, prog, x, z };
    });

    // Rover
    this.rover = this.makeRover();
    map.add(this.rover);
    this.pin = new THREE.Group();
    const cone = new THREE.Mesh(new THREE.ConeGeometry(2.2, 5, 16), mat(0x00d1ff, { emissive: 0x004455 }));
    cone.rotation.x = Math.PI; cone.position.y = 2.5;
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 12, 6), mat(0x00d1ff));
    stem.position.y = 11;
    this.pin.add(cone, stem);
    map.add(this.pin);

    // Route, goal, hazard, pointing reticle
    this.pathLine = new THREE.Line(new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0x00e5ff }));
    map.add(this.pathLine);
    this.goal = new THREE.Group();
    const gring = new THREE.Mesh(new THREE.RingGeometry(2.2, 3.2, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0x00e5ff, side: THREE.DoubleSide }));
    const gpole = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 10, 6), mat(0x00e5ff));
    gpole.position.y = 5;
    this.goal.add(gring, gpole);
    this.goal.visible = false;
    map.add(this.goal);

    this.hazard = new THREE.Group();
    const hp = new THREE.Mesh(new THREE.CapsuleGeometry(0.35, 1.1, 4, 8), mat(0xff2d55));
    hp.position.y = 0.9;
    const hbox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(2.2, 2.6, 2.2)),
      new THREE.LineBasicMaterial({ color: 0xff2d55 }));
    hbox.position.y = 1.3;
    const htag = label('PERSON — AI STOP', '#fff', 'rgba(220,30,60,0.9)', 320);
    htag.position.y = 5; htag.scale.set(10, 2, 1);
    this.hazard.add(hp, hbox, htag);
    this.hazard.visible = false;
    map.add(this.hazard);

    this.reticle = new THREE.Mesh(new THREE.RingGeometry(2.5, 4, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, depthTest: false }));
    this.reticle.renderOrder = 30;
    this.reticle.visible = false;
    map.add(this.reticle);

    // Rain over the table (purely atmospheric)
    const drops = 1200, rp = new Float32Array(drops * 3);
    for (let i = 0; i < drops; i++) rp.set([(Math.random() - 0.5) * size, Math.random() * 60, (Math.random() - 0.5) * size], i * 3);
    const rg = new THREE.BufferGeometry();
    rg.setAttribute('position', new THREE.BufferAttribute(rp, 3));
    // point size is in world (table) metres; hidden from the rover camera
    this.rain = new THREE.Points(rg, new THREE.PointsMaterial({ color: 0xa9c7ff, size: 0.0035, transparent: true, opacity: 0.55 }));
    this.rain.visible = this.type === 'flood' || this.type === 'landslide';
    map.add(this.rain);
    this.hudOnly.push(this.reticle, this.rain);
    this.hz = makeHazard(this, m);
  }

  // ---- helpers for hazards.js ----
  tintVertex(v, r, g, b) { const c = this.colorAttr.array; c[v * 3] = r; c[v * 3 + 1] = g; c[v * 3 + 2] = b; }
  restoreVertex(v) {
    const c = this.colorAttr.array, b = this.baseColors;
    c[v * 3] = b[v * 3]; c[v * 3 + 1] = b[v * 3 + 1]; c[v * 3 + 2] = b[v * 3 + 2];
  }
  colorsChanged() { this.colorAttr.needsUpdate = true; }
  setBuildingTint(indices, hex) {
    const col = new THREE.Color(hex);
    for (const i of indices) this.buildingsMesh.setColorAt(i, col);
    if (this.buildingsMesh.instanceColor) this.buildingsMesh.instanceColor.needsUpdate = true;
  }

  // Cockpit view: you are inside the rover, so hide its body and the map pin; rain at life size
  setCockpit(on) {
    this.cockpit = on;
    if (this.rover) this.rover.visible = !on;
    if (this.pin) this.pin.visible = !on;
    if (this.rain) this.rain.material.size = on ? 0.05 : 0.0035;
  }

  applyLayer(layer) {
    if (this.hz && layer && layer.scenario === this.type) this.hz.applyLayer(layer);
  }

  makeRover() {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.8, 3.0), mat(0xf5a623));
    body.position.y = 1.0;
    const cab = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.7, 1.2), mat(0xf2f2f2));
    cab.position.set(0, 1.75, 0.6);
    const glass = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.45, 0.05), mat(0x203040, { metalness: 0.6, roughness: 0.2 }));
    glass.position.set(0, 1.8, 1.22);
    const stripe = new THREE.Mesh(new THREE.BoxGeometry(1.82, 0.15, 3.02), mat(0xe5484d));
    stripe.position.y = 0.95;
    this.lightR = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.18, 0.3), new THREE.MeshBasicMaterial({ color: 0xff2020 }));
    this.lightB = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.18, 0.3), new THREE.MeshBasicMaterial({ color: 0x2060ff }));
    this.lightR.position.set(-0.35, 2.2, 0.6); this.lightB.position.set(0.35, 2.2, 0.6);
    g.add(body, cab, glass, stripe, this.lightR, this.lightB);
    this.wheels = [];
    for (const x of [-1.0, 1.0]) for (const z of [-1.05, 0, 1.05]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.48, 0.48, 0.35, 14), mat(0x222222));
      wheel.rotation.z = Math.PI / 2;
      wheel.position.set(x, 0.48, z);
      g.add(wheel);
      this.wheels.push(wheel);
    }
    return g;
  }

  groundAt(x, z) {
    const m = this.m, n = m.n, half = m.size / 2, r = n + 1;
    const fx = Math.min(Math.max((x + half) / m.cell, 0), n - 1e-6);
    const fz = Math.min(Math.max((z + half) / m.cell, 0), n - 1e-6);
    const ix = Math.floor(fx), iz = Math.floor(fz), tx = fx - ix, tz = fz - iz;
    const h = m.heights;
    const a = h[iz * r + ix] * (1 - tx) + h[iz * r + ix + 1] * tx;
    const b = h[(iz + 1) * r + ix] * (1 - tx) + h[(iz + 1) * r + ix + 1] * tx;
    return a * (1 - tz) + b * tz;
  }

  surfaceAt(x, z) { return Math.max(this.groundAt(x, z), this.level); }

  // Ray (world space) -> {x, z} on the map surface, or null.
  raycast(origin, dir) {
    if (!this.m) return null;
    const inv = this.map.matrixWorld.clone().invert();
    const o = origin.clone().applyMatrix4(inv);
    const d = dir.clone().transformDirection(inv);
    const half = this.m.size / 2, top = this.maxH + 12;
    if (d.y >= -1e-4 && o.y > top) return null;
    let t = o.y > top ? (top - o.y) / d.y : 0;
    const step = 0.6 / Math.max(0.2, Math.hypot(d.x, d.z) + Math.abs(d.y));
    for (let k = 0; k < 2000; k++, t += step) {
      const x = o.x + d.x * t, y = o.y + d.y * t, z = o.z + d.z * t;
      if (y < this.minH - 2) return null;
      if (Math.abs(x) > half || Math.abs(z) > half) { if (k > 0 && (d.y > 0)) return null; continue; }
      if (y <= this.surfaceAt(x, z)) return { x, z };
    }
    return null;
  }

  showReticle(hit) {
    if (!this.reticle) return;
    this.reticle.visible = !!hit;
    if (hit) this.reticle.position.set(hit.x, this.surfaceAt(hit.x, hit.z) + 0.6, hit.z);
  }

  update(state, t) {
    if (!this.m) return;
    const s = state.sim;
    if (s.scenario && s.scenario !== this.type) return;   // a state from before a scenario switch
    this.targetLevel = s.level;
    const r = s.rover;
    this.rover.position.set(r.x, Math.max(r.y, this.groundAt(r.x, r.z)), r.z);
    this.rover.rotation.y = r.yaw;
    this.roverSpeed = r.v;
    this.pin.position.set(r.x, this.rover.position.y + 4, r.z);

    // route
    const key = `${s.path_id}:${s.path.length}:${s.path[0]?.join(',')}`;
    if (key !== this.pathKey) {
      this.pathKey = key;
      const pts = [];
      if (s.path.length) {
        pts.push(new THREE.Vector3(r.x, this.surfaceAt(r.x, r.z) + 0.8, r.z));
        for (const [x, z] of s.path.slice(1)) {
          const prev = pts[pts.length - 1];
          // densify so the line follows the terrain
          const steps = Math.max(1, Math.round(Math.hypot(x - prev.x, z - prev.z) / 4));
          for (let i = 1; i <= steps; i++) {
            const px = prev.x + (x - prev.x) * i / steps, pz = prev.z + (z - prev.z) * i / steps;
            pts.push(new THREE.Vector3(px, this.surfaceAt(px, pz) + 0.8, pz));
          }
        }
      }
      this.pathLine.geometry.dispose();
      this.pathLine.geometry = new THREE.BufferGeometry().setFromPoints(pts);
    }
    this.goal.visible = !!s.goal;
    if (s.goal) this.goal.position.set(s.goal[0], this.surfaceAt(s.goal[0], s.goal[1]) + 0.3, s.goal[1]);

    s.victims.forEach((v, i) => {
      const o = this.victims[i];
      if (!o) return;
      const c = STATUS_COLOR[v.status];
      o.beam.material.color.setHex(c);
      o.beam.visible = v.status === 'waiting';
      o.person.visible = v.status === 'waiting';
      o.tag.visible = v.status === 'waiting';
      o.beam.material.opacity = 0.25 + 0.15 * Math.sin(t * 0.006 + i);
      o.prog.visible = v.load > 0 && v.status === 'waiting';
      if (o.prog.visible) {
        o.prog.geometry.dispose();
        o.prog.geometry = new THREE.RingGeometry(9, 10.5, 40, 1, 0, Math.max(0.01, v.load * Math.PI * 2)).rotateX(-Math.PI / 2);
      }
    });
    this.rescuedLabel.userData.set(`${s.score}/${s.target} rescued` + (s.airlifted ? ` · ${s.airlifted} airlifted` : ''));
    this.hz?.update(s);

    this.hazard.visible = !!s.hazard;
    if (s.hazard) this.hazard.position.set(s.hazard[0], this.groundAt(s.hazard[0], s.hazard[1]), s.hazard[1]);
  }

  animate(dt, t) {
    if (!this.m) return;
    this.level += (this.targetLevel - this.level) * Math.min(1, dt * 1.5);
    this.water.position.y = this.level;
    const blink = Math.floor(t / 250) % 2 === 0;
    this.lightR.material.color.setHex(blink ? 0xff2020 : 0x401010);
    this.lightB.material.color.setHex(blink ? 0x102040 : 0x2060ff);
    for (const w of this.wheels) w.rotation.x += (this.roverSpeed || 0) * dt / 0.48;
    this.hz?.animate(dt, t);
    if (!this.rain.visible) return;
    const p = this.rain.geometry.attributes.position;
    for (let i = 0; i < p.count; i++) {
      let y = p.getY(i) - dt * 40;
      if (y < this.minH) y += 60;
      p.setY(i, y);
    }
    p.needsUpdate = true;
  }

  // Map scale tricks for the two views: big rover + pin on the table, true size in the rover camera
  tableView(on) {
    if (!this.rover) return;
    this.rover.scale.setScalar(on ? 3 : 1);
    this.pin.visible = on;
    for (const v of this.victims) v.tag.visible = on && v.person.visible;
    for (const o of this.hudOnly) {
      if (on) { o.visible = o.userData.wasVisible ?? o.visible; }
      else { o.userData.wasVisible = o.visible; o.visible = false; }
    }
  }
}
