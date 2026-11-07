import * as THREE from '../vendor/three.module.js';
import { VRButton } from '../vendor/VRButton.js';
import { Net } from './net.js';
import { Controls } from './controls.js';
import { World } from './scene.js';
import { Hud } from './hud.js';

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
document.body.appendChild(renderer.domElement);
document.body.appendChild(VRButton.createButton(renderer));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171c);
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.02, 50);
camera.position.set(0, 1.2, 0);
camera.lookAt(0, 0.78, -0.55);
scene.add(camera);

const world = new World(scene);
world.placeFor(new THREE.Vector3(0, 1.2, 0));
const hud = new Hud(camera);

let state = null;
const net = new Net({
  onHello: (geom) => world.build(geom),
  onState: (m, now) => { state = m; world.update(m, now); },
  onNotice: (text) => hud.showNotice(text),
});

let placeAfterFrames = 0;
const controls = new Controls(renderer, {
  onArm: () => net.control('arm'),
  onDisarm: () => net.control('disarm'),
  onStop: () => net.control('stop'),
  onRecenter: () => { placeAfterFrames = 1; },
});

// Silence (no commands) when the page is hidden -> the robot's watchdog holds it.
net.getInput = () => (document.hidden ? null : controls.input);

renderer.xr.addEventListener('sessionstart', () => { placeAfterFrames = 5; });
renderer.xr.addEventListener('sessionend', () => {
  camera.position.set(0, 1.2, 0);
  camera.lookAt(0, 0.78, -0.55);
  world.placeFor(new THREE.Vector3(0, 1.2, 0));
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const status = document.getElementById('status');
let lastHud = 0;
renderer.setAnimationLoop((t) => {
  controls.update();
  if (placeAfterFrames > 0 && --placeAfterFrames === 0) {
    const head = new THREE.Vector3();
    camera.getWorldPosition(head);
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    world.placeFor(head, Math.atan2(-dir.x, -dir.z));
  }
  if (t - lastHud > 100) {
    lastHud = t;
    hud.draw({ net, state, input: controls.input, source: controls.source });
    if (status) {
      const p50 = net.percentile(0.5);
      status.textContent = `${net.replaced ? 'another operator has control (press R to take over)' : net.connected ? 'connected' : 'connecting…'} · RTT ${p50 == null ? '—' : p50.toFixed(0) + ' ms'} · robot ${state?.robot?.state ?? '—'}`;
    }
  }
  renderer.render(scene, camera);
});
