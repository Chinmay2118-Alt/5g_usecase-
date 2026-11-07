"""Headless operator for testing without a headset.

Runs N link-loss trials: arm, jog the arm with the clutch held, then go
silent (as if the 5G link dropped) and check that the robot reaches HOLDING
within 200 ms of the last command it received.

  python tools/fake_operator.py                       # 20 trials against https://localhost:8443
  python tools/fake_operator.py --url http://localhost:8080 --trials 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import ssl
import statistics
import sys
import time

import aiohttp

PASS_MS = 200.0


class Op:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0
        self.state = None
        self.rtts: list[float] = []
        self.notices: list[str] = []

    async def recv(self):
        async for m in self.ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = json.loads(m.data)
            if msg["type"] == "state":
                self.state = msg
            elif msg["type"] == "ack":
                self.rtts.append((time.perf_counter() * 1000) - msg["t_op"])
            elif msg["type"] == "notice":
                self.notices.append(msg["text"])

    async def cmd(self, clutch=False, vx=0.0, vy=0.0, vz=0.0, gripper=False):
        await self.ws.send_str(json.dumps({
            "type": "command", "seq": self.seq, "t_op": time.perf_counter() * 1000,
            "clutch": clutch, "vx": vx, "vy": vy, "vz": vz, "gripper": gripper}))
        self.seq += 1

    async def stream(self, seconds, **kw):
        end = time.perf_counter() + seconds
        k = 0
        while time.perf_counter() < end:
            phase = k * 0.02
            args = {key: (v(phase) if callable(v) else v) for key, v in kw.items()}
            await self.cmd(**args)
            k += 1
            await asyncio.sleep(0.02)

    def robot(self):
        return self.state["robot"] if self.state else {}

    async def wait_for(self, robot_state, timeout=3.0):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if self.robot().get("state") == robot_state:
                return True
            await asyncio.sleep(0.005)
        return False


async def run(url: str, trials: int, move_s: float) -> int:
    ws_url = url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/ws/operator"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # self-signed dev certificate
    results = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url, ssl=ctx if ws_url.startswith("wss") else None) as ws:
            op = Op(ws)
            rx = asyncio.create_task(op.recv())
            for i in range(1, trials + 1):
                await op.stream(0.4, clutch=False)                     # live link, clutch released
                await ws.send_str(json.dumps({"type": "control", "action": "arm"}))
                arm_task = asyncio.create_task(op.stream(0.6, clutch=False))
                armed = await op.wait_for("ARMED", 1.0)
                await arm_task
                if not armed:
                    print(f"trial {i:2d}: could not arm ({op.notices[-1:]})")
                    results.append(None)
                    continue
                await op.stream(move_s, clutch=True,
                                vx=lambda t: round(0.8 * math.sin(2 * t), 3),
                                vz=lambda t: round(0.5 * math.cos(2 * t), 3))
                # --- link loss: go silent ---
                t_silent = time.perf_counter()
                held = await op.wait_for("HOLDING", 2.0)
                seen_ms = (time.perf_counter() - t_silent) * 1000
                lat = op.robot().get("hold_latency_ms")
                ok = held and lat is not None and lat <= PASS_MS
                results.append(lat if ok else None)
                print(f"trial {i:2d}: {'PASS' if ok else 'FAIL'}  robot held {lat} ms after last command "
                      f"(seen by operator after {seen_ms:.0f} ms)  reason={op.robot().get('reason')}")
            rx.cancel()

    passed = [r for r in results if r is not None]
    print(f"\n{len(passed)}/{trials} trials held within {PASS_MS:.0f} ms", end="")
    if passed:
        print(f"  (hold latency mean {statistics.mean(passed):.1f} ms, max {max(passed):.1f} ms)", end="")
    if op.rtts:
        r = sorted(op.rtts)
        print(f"\ncommand RTT p50 {r[len(r)//2]:.1f} ms, p95 {r[int(len(r)*0.95)]:.1f} ms over {len(r)} acks", end="")
    print()
    return 0 if len(passed) == trials else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://localhost:8443")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--move-s", type=float, default=1.0)
    a = ap.parse_args()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    sys.exit(asyncio.run(run(a.url, a.trials, a.move_s)))
