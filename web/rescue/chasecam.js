// "Onboard camera" screen: a chase camera behind the rover renders the same
// miniature scene to a texture shown on a floating screen. It is rendered on
// the headset from the state stream, so no video crosses the 5G link.
import * as THREE from '../vendor/three.module.js';
import { label } from './map.js';

const W = 640, H = 360;

export class ChaseCam {
  constructor(renderer, scene, rig, fmap) {
    this.renderer = renderer;
    this.scene = scene;
    this.fmap = fmap;
    this.rt = new THREE.WebGLRenderTarget(W, H);
    this.cam = new THREE.PerspectiveCamera(62, W / H, 0.0005, 5);
    this.screen = new THREE.Group();
    const frame = new THREE.Mesh(new THREE.BoxGeometry(0.68, 0.40, 0.02),
      new THREE.MeshStandardMaterial({ color: 0x111418 }));
    frame.position.z = -0.012;
    const img = new THREE.Mesh(new THREE.PlaneGeometry(0.64, 0.36),
      new THREE.MeshBasicMaterial({ map: this.rt.texture, toneMapped: false }));
    const title = label('ROVER CAM · simulated onboard camera', '#cbd5e0', 'rgba(17,20,24,0.9)', 560);
    title.scale.set(0.42, 0.048, 1);
    title.position.set(0, 0.225, 0);
    this.screen.add(frame, img, title);
    // above the far edge of the table, tilted towards the operator
    this.screen.position.set(0, 0.42, -0.78);
    this.screen.rotation.x = -0.18;
    rig.add(this.screen);
    fmap.hudOnly.push(this.screen);
    this.pos = null;
    this.frame = 0;
  }

  render(dt) {
    const f = this.fmap;
    if (!f.m || !f.rover) return;
    if ((this.frame++ & 1) === 1) return;   // ~36 fps on a 72 Hz Quest
    const r = f.rover, S = f.S;
    const yaw = r.rotation.y;
    const fwd = new THREE.Vector3(Math.sin(yaw), 0, Math.cos(yaw));
    // map-local camera position: 9 m behind, 4.5 m up; look 8 m ahead
    const local = r.position.clone().addScaledVector(fwd, -9).add(new THREE.Vector3(0, 4.5, 0));
    local.y = Math.max(local.y, f.surfaceAt(local.x, local.z) + 2.5);
    const target = r.position.clone().addScaledVector(fwd, 8).add(new THREE.Vector3(0, 1.0, 0));
    const wpos = f.map.localToWorld(local.clone());
    const wtgt = f.map.localToWorld(target.clone());
    if (!this.pos) this.pos = wpos.clone();
    this.pos.lerp(wpos, Math.min(1, dt * 6));
    this.cam.position.copy(this.pos);
    this.cam.lookAt(wtgt);
    this.cam.near = 0.3 * S;
    this.cam.far = 400 * S;
    this.cam.updateProjectionMatrix();

    f.tableView(false);
    const renderer = this.renderer;
    const prevRT = renderer.getRenderTarget();
    const prevXr = renderer.xr.enabled;
    renderer.xr.enabled = false;            // standard three.js pattern for render-to-texture inside XR
    renderer.setRenderTarget(this.rt);
    renderer.clear();
    renderer.render(this.scene, this.cam);
    renderer.xr.enabled = prevXr;
    renderer.setRenderTarget(prevRT);
    f.tableView(true);
  }
}
