// Cockpit view: the operator sits inside the rover at full scale and sees the
// world through its front camera, with head tracking to look around.
//
// How it works: instead of moving the operator, the whole map is moved so the
// rover's camera point lands on the operator's seat (world = Seat * RoverCam^-1).
// Comfort measures, because VR motion sickness comes from seeing motion the
// body doesn't feel:
//   * a fixed cockpit frame (dashboard, pillars, roof, bonnet) that never moves
//     relative to you: a stable "rest frame";
//   * a vignette that narrows the view while the rover turns or brakes hard;
//   * no pitch/roll: the horizon stays level even on slopes;
//   * smooth motion between the 30 Hz state updates (dead reckoning from the
//     rover's speed and turn rate, then gentle correction), so it never judders.
import * as THREE from '../vendor/three.module.js';
import { TopDown } from '../common/topdown.js';

const CAM_H = 2.15;        // camera height above the ground (m), on the cab roof
const CAM_FWD = 0.9;       // camera ahead of the rover centre (m)
const TABLET_PX = 512;

const FOG = { flood: 0x2b3442, earthquake: 0x3d3832, wildfire: 0x3a2e28, landslide: 0x2f3329 };

function mat(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.8, metalness: 0.1, ...extra });
}

export class Cockpit {
  constructor(scene, camera, fmap, rig) {
    this.scene = scene;
    this.camera = camera;
    this.fmap = fmap;
    this.rig = rig;
    this.active = false;
    this.pose = null;           // displayed rover pose {x, z, yaw}
    this.recv = null;           // latest received {x, z, yaw, v, w, t}
    this.comfort = 0;
    this.seat = new THREE.Group();          // the operator's seat: cockpit frame + tablet live here
    this.seat.visible = false;
    scene.add(this.seat);
    this.buildFrame();
    this.buildTablet();
    this.buildVignette();
    this.td = null;
    this.tabletT = 0;
    this.hit = null;
    this.savedFog = null;
  }

  // --- construction -------------------------------------------------------
  buildFrame() {
    const dark = mat(0x1f2329), trim = mat(0x2d3139), orange = mat(0xf5a623), glassEdge = mat(0x111317);
    const add = (geo, m, x, y, z, rx = 0, ry = 0, rz = 0) => {
      const o = new THREE.Mesh(geo, m);
      o.position.set(x, y, z); o.rotation.set(rx, ry, rz);
      this.seat.add(o);
      return o;
    };
    add(new THREE.BoxGeometry(1.6, 0.16, 0.5), dark, 0, -0.42, -0.62);           // dashboard
    add(new THREE.BoxGeometry(1.6, 0.04, 0.06), trim, 0, -0.335, -0.86);         // dash top lip
    add(new THREE.BoxGeometry(1.5, 0.12, 1.6), orange, 0, -0.62, -1.75, -0.08);  // bonnet in front of you
    add(new THREE.BoxGeometry(1.52, 0.03, 0.08), mat(0xe5484d), 0, -0.55, -1.0); // red stripe on the bonnet edge
    for (const s of [-1, 1]) {
      add(new THREE.BoxGeometry(0.07, 1.0, 0.07), glassEdge, s * 0.78, 0.05, -0.85, 0.28, 0, s * -0.12);  // A-pillars
      add(new THREE.BoxGeometry(0.1, 0.5, 1.2), dark, s * 0.86, -0.3, -0.25);    // door sills
    }
    add(new THREE.BoxGeometry(1.7, 0.07, 0.12), glassEdge, 0, 0.55, -0.72);      // roof edge
    add(new THREE.BoxGeometry(1.7, 0.02, 1.0), dark, 0, 0.6, -0.25);             // roof
    // steering yoke (does not move: it is a rest frame, not a control)
    const yoke = add(new THREE.TorusGeometry(0.16, 0.018, 8, 24, Math.PI * 1.2), trim, 0, -0.27, -0.48, -0.9, 0, Math.PI * 0.9);
    yoke.scale.set(1.2, 1, 1);
  }

  buildTablet() {
    this.tabletCanvas = document.createElement('canvas');
    this.tabletCanvas.width = this.tabletCanvas.height = TABLET_PX;
    this.tabletTex = new THREE.CanvasTexture(this.tabletCanvas);
    this.tabletTex.colorSpace = THREE.SRGBColorSpace;
    const size = 0.34;
    this.tablet = new THREE.Group();
    const bezel = new THREE.Mesh(new THREE.BoxGeometry(size + 0.03, size + 0.05, 0.015), mat(0x0b0d10));
    bezel.position.z = -0.009;
    this.tabletScreen = new THREE.Mesh(new THREE.PlaneGeometry(size, size),
      new THREE.MeshBasicMaterial({ map: this.tabletTex, toneMapped: false }));
    this.tablet.add(bezel, this.tabletScreen);
    this.tablet.position.set(-0.36, -0.2, -0.52);      // left of centre, on the dashboard
    this.tablet.rotation.set(-0.55, 0.35, 0);           // tilted up and towards you
    this.tabletSize = size;
    this.seat.add(this.tablet);
  }

  buildVignette() {
    const c = document.createElement('canvas');
    c.width = c.height = 256;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(128, 128, 40, 128, 128, 128);
    grad.addColorStop(0, 'rgba(0,0,0,0)');
    grad.addColorStop(0.55, 'rgba(0,0,0,0.0)');
    grad.addColorStop(1, 'rgba(0,0,0,1)');
    g.fillStyle = grad; g.fillRect(0, 0, 256, 256);
    const tex = new THREE.CanvasTexture(c);
    this.vignette = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.6),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0, depthTest: false, depthWrite: false }));
    this.vignette.position.z = -0.35;
    this.vignette.renderOrder = 999;
    this.vignette.visible = false;
    this.camera.add(this.vignette);
  }

  // --- mode switching -----------------------------------------------------
  enter(head, yaw) {
    this.active = true;
    this.seat.position.copy(head);
    this.seat.rotation.set(0, yaw, 0);
    this.seat.updateMatrixWorld(true);
    this.seat.visible = true;
    this.vignette.visible = true;
    this.rig.visible = false;                         // hide the table and its camera screen
    if (!this.savedFog) this.savedFog = { fog: this.scene.fog, bg: this.scene.background.clone(), far: this.camera.far, near: this.camera.near };
    this.applyAtmosphere();
    this.camera.near = 0.05;                          // full-scale world: avoid depth fighting far away
    this.camera.far = 700;
    this.camera.updateProjectionMatrix();
    this.adopt();
  }

  exit() {
    if (!this.active) return;
    this.active = false;
    this.seat.visible = false;
    this.vignette.visible = false;
    this.rig.visible = true;
    const f = this.fmap;
    if (f.map) {
      f.map.matrixAutoUpdate = true;
      this.rig.add(f.map);
      f.map.scale.setScalar(f.S);
      f.map.position.copy(f.mapBase);
      f.map.rotation.set(0, 0, 0);
      f.setCockpit?.(false);
    }
    if (this.savedFog) {
      this.scene.fog = this.savedFog.fog;
      this.scene.background.copy(this.savedFog.bg);
      this.camera.far = this.savedFog.far;
      this.camera.near = this.savedFog.near;
      this.camera.updateProjectionMatrix();
      this.savedFog = null;
    }
  }

  applyAtmosphere() {
    const c = FOG[this.fmap.type] ?? 0x2b3442;
    this.scene.fog = new THREE.Fog(c, 40, 230);
    this.scene.background.setHex(c);
  }

  // Put the (possibly rebuilt) map under the scene at full scale
  adopt() {
    const f = this.fmap;
    if (!f.map) return;
    if (f.map.parent !== this.scene) {
      this.scene.add(f.map);
      f.map.matrixAutoUpdate = false;
      f.setCockpit?.(true);
      this.td = f.m ? new TopDown(f.m) : null;
      this.applyAtmosphere();
    }
  }

  onHello(m) {
    this.td = new TopDown(m);
    this.pose = null;
    this.recv = null;
  }

  onState(state) {
    const r = state.sim.rover;
    this.recv = { x: r.x, z: r.z, yaw: r.yaw, v: r.v, w: r.w ?? 0, t: performance.now() };
    if (state.layer) this.td?.setLayer(state.layer);
  }

  // --- per frame ----------------------------------------------------------
  update(dt, state) {
    if (!this.active || !this.recv) return;
    this.adopt();
    const f = this.fmap;
    if (!f.map) return;
    // Dead-reckon the latest state forward (no added latency), then ease towards it
    const rc = this.recv, age = Math.min(0.15, (performance.now() - rc.t) / 1000);
    const tyaw = rc.yaw + rc.w * age;
    const tx = rc.x + rc.v * Math.sin(tyaw) * age, tz = rc.z + rc.v * Math.cos(tyaw) * age;
    if (!this.pose || Math.hypot(tx - this.pose.x, tz - this.pose.z) > 8) this.pose = { x: tx, z: tz, yaw: tyaw };
    const p = this.pose, k = Math.min(1, dt * 14);
    p.x += (tx - p.x) * k; p.z += (tz - p.z) * k;
    let dy = tyaw - p.yaw; dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    p.yaw += dy * k;

    // World = Seat * RoverCam^-1  (camera looks along the rover's forward = -Z of the camera frame)
    const fx = Math.sin(p.yaw), fz = Math.cos(p.yaw);
    const cx = p.x + fx * CAM_FWD, cz = p.z + fz * CAM_FWD;
    const ground = Math.max(f.groundAt(p.x, p.z), f.level - 0.6);
    const camM = new THREE.Matrix4().compose(
      new THREE.Vector3(cx, ground + CAM_H, cz),
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), p.yaw + Math.PI),
      new THREE.Vector3(1, 1, 1));
    f.map.matrix.copy(this.seat.matrixWorld).multiply(camM.invert());
    f.map.matrixWorldNeedsUpdate = true;

    // Comfort vignette: stronger while turning or braking/accelerating hard
    const turn = Math.min(1, Math.abs(rc.w) / 1.0);
    const accel = this.lastV == null ? 0 : Math.min(1, Math.abs(rc.v - this.lastV) / Math.max(dt, 1e-3) / 8);
    this.lastV = rc.v;
    const target = Math.min(0.9, turn * 0.85 + accel * 0.35);
    this.comfort += (target - this.comfort) * Math.min(1, dt * 6);
    this.vignette.material.opacity = this.comfort;

    // Tablet mini-map at ~6 Hz
    this.tabletT += dt;
    if (this.td && state?.sim && this.tabletT > 0.16) {
      this.tabletT = 0;
      const g = this.tabletCanvas.getContext('2d');
      this.td.draw(g, TABLET_PX, state.sim, { big: 1.6, reticle: this.hit, wind: true });
      g.fillStyle = 'rgba(0,0,0,0.55)'; g.fillRect(0, TABLET_PX - 34, TABLET_PX, 34);
      g.fillStyle = '#e2e8f0'; g.font = 'bold 20px system-ui';
      g.fillText('Point + trigger to send the rover   ·   N ▲', 12, TABLET_PX - 11);
      this.tabletTex.needsUpdate = true;
    }
  }

  // Ray (world space) against the tablet -> {x, z} map coordinates, or null
  pickTablet(origin, dir) {
    if (!this.active || !this.fmap.m) return null;
    const ray = new THREE.Raycaster(origin, dir.clone().normalize(), 0, 3);
    const hit = ray.intersectObject(this.tabletScreen, false)[0];
    if (!hit || !hit.uv) return null;
    const m = this.fmap.m, half = m.size / 2;
    return { x: -half + hit.uv.x * m.size, z: half - hit.uv.y * m.size, tablet: true, dist: hit.distance };
  }

  // Desktop: camera sits on the seat, right-drag looks around
  applyDesktopCamera(orbit) {
    this.camera.position.copy(this.seat.position);
    this.camera.rotation.set(0, 0, 0, 'YXZ');
    this.camera.rotation.order = 'YXZ';
    this.camera.rotation.y = this.seat.rotation.y + orbit.yaw;
    this.camera.rotation.x = -(orbit.pitch - 0.62) * 0.8 - 0.12;
  }
}
