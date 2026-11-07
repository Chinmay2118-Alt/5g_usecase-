// Rescue rover input.
//
// VR (Quest Touch):
//   right controller laser -> point at the map; TRIGGER = send the rover there
//   hold GRIP (either hand) = manual takeover: left stick = throttle, right stick = turn
//   A = arm, B = cancel route, Y = STOP, X = switch view (cockpit / table), left stick click = re-centre
// Desktop:
//   mouse over the map + left click = send the rover there; right-drag = orbit; wheel = zoom
//   hold SPACE = manual takeover: W/S throttle, A/D turn
//   R = arm, T = disarm, X = stop, C = cancel route, V = switch view (cockpit / table)
import * as THREE from '../vendor/three.module.js';

const DEAD = 0.15;
const dz = (v) => (Math.abs(v) < DEAD ? 0 : Math.sign(v) * (Math.abs(v) - DEAD) / (1 - DEAD));

export class RoverControls {
  constructor(renderer, camera, fmap, cb) {
    this.renderer = renderer;
    this.camera = camera;
    this.fmap = fmap;
    this.cb = cb;           // { onGoto(x,z), onArm, onDisarm, onStop, onCancel, onRecenter, onToggleView, pick }
    this.pick = cb.pick || ((o, d) => fmap.raycast(o, d));
    this.input = { clutch: false, vx: 0, vy: 0, vz: 0, gripper: false };
    this.source = 'keyboard';
    this.keys = new Set();
    this.prev = {};
    this.hit = null;
    this.mouse = null;

    // XR controllers with a laser
    this.rays = [0, 1].map((i) => {
      const c = renderer.xr.getController(i);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]),
        new THREE.LineBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.8 }));
      line.scale.z = 2;
      line.visible = false;
      c.add(line);
      c.userData.line = line;
      c.addEventListener('connected', (e) => { c.userData.handedness = e.data.handedness; line.visible = e.data.handedness === 'right'; });
      c.addEventListener('disconnected', () => { line.visible = false; });
      fmap.hudOnly.push(line);
      return c;
    });

    addEventListener('keydown', (e) => {
      if (e.repeat) return;
      const k = e.key.toLowerCase();
      this.keys.add(k);
      if (k === 'r') cb.onArm();
      if (k === 't') cb.onDisarm();
      if (k === 'x') cb.onStop();
      if (k === 'c') cb.onCancel();
      if (k === 'v') cb.onToggleView?.();
      if (k === ' ') e.preventDefault();
    });
    addEventListener('keyup', (e) => this.keys.delete(e.key.toLowerCase()));
    addEventListener('blur', () => this.keys.clear());

    // Desktop mouse: hover/click on the map, right-drag orbit, wheel zoom
    const el = renderer.domElement;
    this.orbit = { yaw: 0, pitch: 0.62, dist: 0.95, drag: null };
    el.addEventListener('contextmenu', (e) => e.preventDefault());
    el.addEventListener('pointermove', (e) => {
      this.mouse = new THREE.Vector2((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
      const d = this.orbit.drag;
      if (d) {
        this.orbit.yaw = d.yaw - (e.clientX - d.x) * 0.005;
        this.orbit.pitch = Math.min(1.45, Math.max(0.15, d.pitch + (e.clientY - d.y) * 0.004));
      }
    });
    el.addEventListener('pointerdown', (e) => {
      if (e.button === 2) this.orbit.drag = { x: e.clientX, y: e.clientY, yaw: this.orbit.yaw, pitch: this.orbit.pitch };
      if (e.button === 0 && this.hit && !renderer.xr.isPresenting) cb.onGoto(this.hit.x, this.hit.z);
    });
    addEventListener('pointerup', () => { this.orbit.drag = null; });
    el.addEventListener('wheel', (e) => {
      this.orbit.dist = Math.min(2.2, Math.max(0.35, this.orbit.dist * (1 + Math.sign(e.deltaY) * 0.08)));
    }, { passive: true });
  }

  edge(id, pressed, fn) {
    if (pressed && !this.prev[id]) fn();
    this.prev[id] = pressed;
  }

  // Desktop camera orbiting around the table centre (rig-local target)
  applyOrbit(rig) {
    const o = this.orbit;
    const target = new THREE.Vector3(0, 0.05, 0).applyMatrix4(rig.matrixWorld);
    const off = new THREE.Vector3(Math.sin(o.yaw) * Math.cos(o.pitch), Math.sin(o.pitch), Math.cos(o.yaw) * Math.cos(o.pitch))
      .multiplyScalar(o.dist);
    this.camera.position.copy(target).add(off);
    this.camera.lookAt(target);
  }

  update() {
    const session = this.renderer.xr.getSession();
    this.hit = null;
    if (session) {
      this.source = 'controllers';
      const out = { clutch: false, vx: 0, vy: 0, vz: 0, gripper: false };
      for (const src of session.inputSources) {
        const gp = src.gamepad;
        if (!gp) continue;
        const b = (i) => !!gp.buttons[i]?.pressed;
        if ((gp.buttons[1]?.value ?? 0) > 0.5) out.clutch = true;
        if (src.handedness === 'left') {
          out.vz = dz(gp.axes[3] ?? 0);                 // forward (-1) = drive forward
          this.edge('lx', b(4), () => this.cb.onToggleView?.());
          this.edge('ls', b(3), this.cb.onRecenter);
          this.edge('ly', b(5), this.cb.onStop);
        } else if (src.handedness === 'right') {
          out.vx = dz(gp.axes[2] ?? 0);                 // right = turn right
          this.edge('ra', b(4), this.cb.onArm);
          this.edge('rb', b(5), this.cb.onCancel);
          this.rightTrigger = (gp.buttons[0]?.value ?? 0) > 0.5;
        }
      }
      // laser from the right controller onto the map
      const right = this.rays.find((c) => c.userData.handedness === 'right');
      if (right) {
        const o = new THREE.Vector3(), q = new THREE.Quaternion();
        right.getWorldPosition(o); right.getWorldQuaternion(q);
        const dir = new THREE.Vector3(0, 0, -1).applyQuaternion(q);
        this.hit = this.pick(o, dir);
        const line = right.userData.line;
        const len = this.hit ? (this.hit.dist ?? o.distanceTo(this.hitWorld(this.hit))) : 2;
        line.scale.z = Math.max(0.05, Math.min(len, 400));
      }
      this.edge('rt', !!this.rightTrigger, () => { if (this.hit) this.cb.onGoto(this.hit.x, this.hit.z); });
      this.input = out;
    } else {
      this.source = 'keyboard';
      const k = (c) => (this.keys.has(c) ? 1 : 0);
      this.input = {
        clutch: this.keys.has(' '),
        vx: k('d') - k('a'),
        vy: 0,
        vz: k('s') - k('w'),
        gripper: false,
      };
      if (this.mouse && !this.orbit.drag) {
        const ray = new THREE.Raycaster();
        ray.setFromCamera(this.mouse, this.camera);
        this.hit = this.pick(ray.ray.origin, ray.ray.direction);
      }
    }
    this.fmap.showReticle(this.hit);
    return this.input;
  }

  hitWorld(hit) {
    return this.fmap.map.localToWorld(new THREE.Vector3(hit.x, this.fmap.surfaceAt(hit.x, hit.z), hit.z));
  }
}
