import * as THREE from '../vendor/three.module.js';
import { VRButton } from '../vendor/VRButton.js';
import { Net } from '../xr/net.js';
import { FloodMap } from './map.js';
import { ChaseCam } from './chasecam.js';
import { RoverControls } from './controls.js';
import { RescueHud } from './hud.js';
import { Cockpit } from './cockpit.js';

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
document.body.appendChild(renderer.domElement);
document.body.appendChild(VRButton.createButton(renderer));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x161c26);
scene.fog = new THREE.Fog(0x161c26, 4, 12);
scene.add(new THREE.HemisphereLight(0xc8d6f0, 0x2a2620, 1.5));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(-1, 2.5, 1.2);
scene.add(sun);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2),
  new THREE.MeshStandardMaterial({ color: 0x1d222b }));
scene.add(floor);

const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.01, 30);
scene.add(camera);

// The command table: placed in front of the viewer
const rig = new THREE.Group();
scene.add(rig);
function placeRig(head, yaw = 0) {
  const ahead = new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw)).multiplyScalar(0.75);
  rig.position.set(head.x + ahead.x, Math.max(0.6, head.y - 0.5), head.z + ahead.z);
  rig.rotation.y = yaw;
  rig.updateMatrixWorld(true);
}
placeRig(new THREE.Vector3(0, 1.25, 0));

const fmap = new FloodMap(rig);
const chase = new ChaseCam(renderer, scene, rig, fmap);
const hud = new RescueHud(camera);
fmap.hudOnly.push(hud.mesh);
const cockpit = new Cockpit(scene, camera, fmap, rig);

// View: 'cockpit' = inside the rover looking through its front camera (default in VR),
// 'table' = the miniature map on the command table (default on desktop). X / V switches.
let view = 'table';
let pendingView = null;      // applied a few frames after entering VR, once the head pose is known
function headPose() {
  const head = new THREE.Vector3(), dir = new THREE.Vector3();
  camera.getWorldPosition(head); camera.getWorldDirection(dir);
  return { head, yaw: Math.atan2(-dir.x, -dir.z) };
}
function setView(v) {
  view = v;
  const { head, yaw } = renderer.xr.isPresenting ? headPose() : { head: new THREE.Vector3(0, 1.25, 0), yaw: 0 };
  if (v === 'cockpit') {
    cockpit.enter(head, yaw);
    floor.visible = false;
    hud.showNotice('Cockpit view: X = table view');
  } else {
    cockpit.exit();
    floor.visible = true;
    placeRig(head, yaw);
    if (!renderer.xr.isPresenting) placeRig(new THREE.Vector3(0, 1.25, 0));
    hud.showNotice('Table view: X = cockpit view');
  }
}

let state = null;
const net = new Net({
  onHello: (_geom, m) => {
    if (!m.map) return;
    fmap.build(m.map);                 // rebuilds the table map when the scenario changes
    cockpit.onHello(m.map);
    chase.pos = null;
    state = null;
    if (m.title) hud.showNotice(`Scenario: ${m.title}`);
    const sc = document.getElementById('scName');
    if (sc) sc.textContent = m.title ? `· ${m.title}` : '';
  },
  onState: (m) => {
    if (m.layer) fmap.applyLayer(m.layer);
    if (fmap.type && m.sim?.scenario && m.sim.scenario !== fmap.type) return;   // pre-switch state
    state = m;
    fmap.update(m, performance.now());
    cockpit.onState(m);
  },
  onNotice: (text) => hud.showNotice(text),
});

let placeAfterFrames = 0;
const controls = new RoverControls(renderer, camera, fmap, {
  onGoto: (x, z) => net.send({ type: 'goto', x: Math.round(x * 10) / 10, z: Math.round(z * 10) / 10 }),
  onArm: () => net.control('arm'),
  onDisarm: () => net.control('disarm'),
  onStop: () => net.control('stop'),
  onCancel: () => net.send({ type: 'cancel_goto' }),
  onRecenter: () => { placeAfterFrames = 1; },
  onToggleView: () => setView(view === 'cockpit' ? 'table' : 'cockpit'),
  pick: (o, d) => cockpit.pickTablet(o, d) ?? fmap.raycast(o, d),
});
net.getInput = () => (document.hidden ? null : controls.input);

renderer.xr.addEventListener('sessionstart', () => { pendingView = { v: 'cockpit', frames: 5 }; });
renderer.xr.addEventListener('sessionend', () => { setView('table'); });
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const status = document.getElementById('status');
let last = performance.now(), lastHud = 0;
renderer.setAnimationLoop((t) => {
  const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (pendingView && --pendingView.frames <= 0) { setView(pendingView.v); pendingView = null; }
  if (!renderer.xr.isPresenting) {
    if (cockpit.active) cockpit.applyDesktopCamera(controls.orbit);
    else controls.applyOrbit(rig);
  }
  if (placeAfterFrames > 0 && --placeAfterFrames === 0) {
    const { head, yaw } = headPose();
    if (cockpit.active) cockpit.enter(head, yaw);       // re-seat yourself in the cockpit
    else placeRig(head, yaw);
  }
  controls.update();
  fmap.animate(dt, now);
  if (cockpit.active) cockpit.update(dt, state);
  else chase.render(dt);
  if (t - lastHud > 100) {
    lastHud = t;
    hud.draw({ net, state, input: controls.input, source: controls.source, hit: controls.hit });
    if (status) {
      const p50 = net.percentile(0.5);
      const s = state?.sim;
      status.textContent = `${net.replaced ? 'another operator has control (press R to take over)' : net.connected ? 'connected' : 'connecting…'} · RTT ${p50 == null ? '—' : p50.toFixed(0) + ' ms'} · ` +
        `rover ${state?.robot?.state === 'ARMED' ? s?.mode : state?.robot?.state ?? '—'}` +
        (s ? ` · rescued ${s.score}/${s.target}` : '');
    }
  }
  renderer.render(scene, camera);
});
