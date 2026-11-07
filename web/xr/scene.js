// 3D scene: table, arm, cubes, bin, workspace box, detection overlay.
// Everything inside `world` uses the server's sim frame (metres, Y up,
// robot base at origin, +Z towards the operator), so state maps 1:1.
import * as THREE from '../vendor/three.module.js';

const COLORS = { red: 0xe5484d, green: 0x30a46c, blue: 0x3e63dd };
const STALE_OVERLAY_MS = 200;

function mat(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.1, ...extra });
}

function boxAt(w, h, d, color, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(color));
  m.position.set(x, y, z);
  return m;
}

function textSprite(text, color = '#ffffff', bg = 'rgba(0,0,0,0.55)') {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  g.fillStyle = bg; g.fillRect(0, 0, 256, 64);
  g.font = 'bold 30px system-ui, sans-serif';
  g.fillStyle = color; g.textBaseline = 'middle';
  g.fillText(text, 10, 33);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
  s.scale.set(0.12, 0.03, 1);
  s.renderOrder = 10;
  s.userData = { canvas: c, ctx: g, tex, text };
  return s;
}

function setSpriteText(s, text, color, bg) {
  if (s.userData.text === text && s.userData.color === color) return;
  const g = s.userData.ctx;
  g.clearRect(0, 0, 256, 64);
  g.fillStyle = bg; g.fillRect(0, 0, 256, 64);
  g.fillStyle = color; g.fillText(text, 10, 33);
  s.userData.tex.needsUpdate = true;
  s.userData.text = text; s.userData.color = color;
}

export class World {
  constructor(scene) {
    this.scene = scene;
    this.world = new THREE.Group();
    scene.add(this.world);
    this.geom = null;
    this.cubes = new Map();
    this.detBoxes = [];
    this.lastDetAt = 0;

    scene.add(new THREE.HemisphereLight(0xdde6ff, 0x30343c, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(0.6, 2.0, 1.0);
    scene.add(sun);

    // Room floor for orientation in VR
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(8, 8), mat(0x22262e));
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);
    const grid = new THREE.GridHelper(8, 16, 0x3a404c, 0x2c313a);
    grid.position.y = 0.002;
    scene.add(grid);
  }

  build(geom) {
    if (this.geom) return;
    this.geom = geom;
    const w = this.world;

    // Table (top surface at y = 0)
    w.add(boxAt(1.0, 0.03, 0.75, 0x8a6a4a, 0, -0.015, 0.2));
    for (const [x, z] of [[-0.46, -0.13], [0.46, -0.13], [-0.46, 0.54], [0.46, 0.54]]) {
      w.add(boxAt(0.04, 0.72, 0.04, 0x5b4632, x, -0.39, z));
    }
    // "Hazard zone" floor marking
    const hz = new THREE.Mesh(new THREE.PlaneGeometry(0.7, 0.4),
      new THREE.MeshBasicMaterial({ color: 0xf5d90a, transparent: true, opacity: 0.08 }));
    hz.rotation.x = -Math.PI / 2; hz.position.set(0, 0.001, 0.27);
    w.add(hz);

    // Workspace box (wireframe)
    const ws = geom.workspace;
    const sx = ws.x[1] - ws.x[0], sy = ws.y[1] - ws.y[0], sz = ws.z[1] - ws.z[0];
    const wsBox = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(sx, sy, sz)),
      new THREE.LineBasicMaterial({ color: 0xf5d90a, transparent: true, opacity: 0.35 }));
    wsBox.position.set((ws.x[0] + ws.x[1]) / 2, (ws.y[0] + ws.y[1]) / 2, (ws.z[0] + ws.z[1]) / 2);
    w.add(wsBox);

    // Bin
    const b = geom.bin, s = b.half * 2, t = 0.006;
    const bin = new THREE.Group();
    bin.add(boxAt(s, t, s, 0x4a5568, 0, t / 2, 0));
    bin.add(boxAt(s, b.wall, t, 0x718096, 0, b.wall / 2, -b.half));
    bin.add(boxAt(s, b.wall, t, 0x718096, 0, b.wall / 2, b.half));
    bin.add(boxAt(t, b.wall, s, 0x718096, -b.half, b.wall / 2, 0));
    bin.add(boxAt(t, b.wall, s, 0x718096, b.half, b.wall / 2, 0));
    bin.position.set(b.x, 0, b.z);
    w.add(bin);
    const binLabel = textSprite('BIN', '#f5d90a');
    binLabel.position.set(b.x, b.wall + 0.03, b.z);
    w.add(binLabel);

    // Arm: base(yaw) > shoulder(x) > elbow(x) > wrist(x) > gripper
    const armMat = mat(0xf08c00), jointMat = mat(0x2d3748);
    this.base = new THREE.Group(); w.add(this.base);
    const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.07, geom.shoulder_h, 24), jointMat);
    pedestal.position.y = geom.shoulder_h / 2; this.base.add(pedestal);
    this.shoulder = new THREE.Group(); this.shoulder.position.y = geom.shoulder_h; this.base.add(this.shoulder);
    const link = (len, r) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(r, r, len), armMat);
      m.position.z = len / 2; return m;
    };
    const joint = (r) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, r * 2.2, 20), jointMat);
      m.rotation.z = Math.PI / 2; return m;
    };
    this.shoulder.add(joint(0.035), link(geom.l1, 0.045));
    this.elbow = new THREE.Group(); this.elbow.position.z = geom.l1; this.shoulder.add(this.elbow);
    this.elbow.add(joint(0.03), link(geom.l2, 0.038));
    this.wrist = new THREE.Group(); this.wrist.position.z = geom.l2; this.elbow.add(this.wrist);
    this.wrist.add(joint(0.022));
    const palm = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.02, 0.03), jointMat);
    palm.position.z = geom.l3 - 0.035; this.wrist.add(palm);
    this.fingers = [-1, 1].map((side) => {
      const f = new THREE.Mesh(new THREE.BoxGeometry(0.008, 0.016, 0.04), mat(0xcbd5e0));
      f.position.set(side * 0.025, 0, geom.l3 - 0.015);
      f.userData.side = side;
      this.wrist.add(f);
      return f;
    });

    // Depth cue: drop line + ring under the gripper tip
    this.drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color: 0x00d1ff, transparent: true, opacity: 0.6 }));
    w.add(this.drop);
    this.ring = new THREE.Mesh(new THREE.RingGeometry(0.014, 0.02, 32),
      new THREE.MeshBasicMaterial({ color: 0x00d1ff, transparent: true, opacity: 0.8 }));
    this.ring.rotation.x = -Math.PI / 2;
    w.add(this.ring);
  }

  // Place the table in front of the viewer. head: THREE.Vector3 of the camera, yaw: heading.
  placeFor(head, yaw = 0) {
    // Table top ~0.45 m below eye level, robot base 0.85 m ahead, work area ~0.5 m ahead.
    const ahead = new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw)).multiplyScalar(0.85);
    this.world.position.set(head.x + ahead.x, head.y - 0.45, head.z + ahead.z);
    this.world.rotation.y = yaw;
  }

  update(state, now) {
    if (!this.geom) return;
    const sim = state.sim;
    const [yaw, sh, el, wr] = sim.joints;
    this.base.rotation.y = yaw;
    this.shoulder.rotation.x = -sh;
    this.elbow.rotation.x = el;
    this.wrist.rotation.x = wr;
    for (const f of this.fingers) f.position.x = f.userData.side * (0.012 + 0.016 * sim.grip);

    const [tx, ty, tz] = sim.tip;
    const pos = this.drop.geometry.attributes.position;
    pos.setXYZ(0, tx, ty, tz); pos.setXYZ(1, tx, 0.001, tz); pos.needsUpdate = true;
    this.ring.position.set(tx, 0.0015, tz);

    for (const c of sim.cubes) {
      let m = this.cubes.get(c.id);
      if (!m) {
        m = new THREE.Mesh(new THREE.BoxGeometry(this.geom.cube, this.geom.cube, this.geom.cube),
          mat(COLORS[c.color] ?? 0xffffff, { emissive: 0x000000 }));
        this.world.add(m);
        this.cubes.set(c.id, m);
      }
      m.position.set(...c.pos);
      m.material.emissive.setHex(c.held ? 0x553300 : c.in_bin ? 0x113311 : 0x000000);
    }

    if (state.det) this.updateDetections(state.det, now);
    // Grey out an overlay that is older than 200 ms (stale AI result)
    const stale = now - this.lastDetAt > STALE_OVERLAY_MS;
    for (const d of this.detBoxes) {
      if (!d.box.visible) continue;
      d.box.material.color.setHex(stale ? 0x777777 : d.color);
    }
  }

  updateDetections(dets, now) {
    this.lastDetAt = now;
    while (this.detBoxes.length < dets.length) {
      const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
        new THREE.LineBasicMaterial({ color: 0x00ff88 }));
      const label = textSprite('');
      this.world.add(box, label);
      this.detBoxes.push({ box, label, color: 0x00ff88 });
    }
    this.detBoxes.forEach((d, i) => {
      const det = dets[i];
      d.box.visible = d.label.visible = !!det;
      if (!det) return;
      const person = det.label === 'person';
      const size = person ? [0.5, 0.3, 0.3] : [0.055, 0.055, 0.055];
      d.box.scale.set(...size);
      d.box.position.set(...det.pos);
      d.color = person ? 0xff3b3b : 0x00ff88;
      d.label.position.set(det.pos[0], det.pos[1] + size[1] / 2 + 0.025, det.pos[2]);
      setSpriteText(d.label, `${det.label} ${det.conf.toFixed(2)}`, person ? '#ff6b6b' : '#7dffb5', 'rgba(0,0,0,0.55)');
    });
  }
}
