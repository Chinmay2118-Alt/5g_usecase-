// Operator input: Quest Touch controllers in VR, keyboard on desktop.
//
// VR (xr-standard gamepad mapping):
//   left stick  -> move X (left/right) and Z (push = away from you)
//   right stick -> move Y (push = up)
//   grip (either hand)   -> CLUTCH: the robot only moves while held
//   right trigger (held) -> close gripper
//   A -> arm, B -> disarm, X -> re-centre the scene, Y -> stop
// Desktop:
//   W/S/A/D move, E/Q up/down, hold SPACE = clutch, hold F = close gripper,
//   R arm, T disarm, X stop

const DEAD = 0.15;

function dz(v) {
  const a = Math.abs(v);
  if (a < DEAD) return 0;
  return Math.sign(v) * (a - DEAD) / (1 - DEAD);
}

export class Controls {
  constructor(renderer, { onArm, onDisarm, onStop, onRecenter }) {
    this.renderer = renderer;
    this.cb = { onArm, onDisarm, onStop, onRecenter };
    this.keys = new Set();
    this.prev = {};               // previous button states for edge detection
    this.input = { clutch: false, vx: 0, vy: 0, vz: 0, gripper: false };
    this.source = 'keyboard';
    addEventListener('keydown', (e) => {
      if (e.repeat) return;
      const k = e.key.toLowerCase();
      this.keys.add(k);
      if (k === 'r') onArm();
      if (k === 't') onDisarm();
      if (k === 'x') onStop();
      if (k === ' ') e.preventDefault();
    });
    addEventListener('keyup', (e) => this.keys.delete(e.key.toLowerCase()));
    addEventListener('blur', () => this.keys.clear());
  }

  edge(id, pressed, fn) {
    if (pressed && !this.prev[id]) fn();
    this.prev[id] = pressed;
  }

  update() {
    const session = this.renderer.xr.getSession();
    if (session) {
      this.source = 'controllers';
      const out = { clutch: false, vx: 0, vy: 0, vz: 0, gripper: false };
      for (const src of session.inputSources) {
        const gp = src.gamepad;
        if (!gp) continue;
        const b = (i) => !!gp.buttons[i]?.pressed;
        const sx = dz(gp.axes[2] ?? 0), sy = dz(gp.axes[3] ?? 0);
        if ((gp.buttons[1]?.value ?? 0) > 0.5) out.clutch = true;
        if (src.handedness === 'left') {
          out.vx = sx;
          out.vz = sy;                       // stick forward (-1) moves away from the operator (-Z)
          this.edge('lx', b(4), this.cb.onRecenter);
          this.edge('ly', b(5), this.cb.onStop);
        } else if (src.handedness === 'right') {
          out.vy = -sy;                      // stick forward moves up
          if ((gp.buttons[0]?.value ?? 0) > 0.5) out.gripper = true;
          this.edge('ra', b(4), this.cb.onArm);
          this.edge('rb', b(5), this.cb.onDisarm);
        }
      }
      this.input = out;
    } else {
      this.source = 'keyboard';
      const k = (c) => this.keys.has(c) ? 1 : 0;
      this.input = {
        clutch: this.keys.has(' '),
        vx: k('d') - k('a'),
        vy: k('e') - k('q'),
        vz: k('s') - k('w'),
        gripper: this.keys.has('f'),
      };
    }
    return this.input;
  }
}
