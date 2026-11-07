"""5G Remote Hands — simulation server.

Plays the role of the robot (sim + safety) and the MEC relay (WebSockets,
delay injection, logging) on one laptop.

  python -m server.main                          # rescue rover, flood (switch live from the dashboard)
  python -m server.main --scenario wildfire      # start in another disaster: flood|earthquake|wildfire|landslide
  python -m server.main --scenario arm           # tabletop arm
  python -m server.main --http 8080         # plain HTTP, desktop testing only (no WebXR on Quest)

World interface (implemented by RescueWorld and ArmWorld):
  name, client_path, dashboard_path, bounds, events
  hello() -> dict                     extra fields for the 'hello' message
  step(dt, armed, cmd)                advance the sim; armed = safety state is ARMED
  hazard_reason() -> str|None         "ai" (person detected) / "overheat" -> safety stop
  layer() -> (version, b64)|None      big per-cell hazard state (wildfire), streamed on change
  detections(rng) -> list             simulated detector output for overlays
  snapshot() -> dict                  sent as state["sim"]
  reset()
  operator_msg(msg, armed) -> str|None   goto / cancel_goto; returns a notice
  dashboard_msg(c)                    scenario-specific dashboard controls
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import ssl
import sys
import time
from collections import deque
from pathlib import Path

from aiohttp import WSMsgType, web

from . import protocol as P
from .certs import CERT, KEY, local_ipv4s, make_cert
from .logger import RunLogger
from .netctl import PROFILES, STALE_S, DelayLine, NetCtl, profiles_json
from .safety import Safety

WEB = Path(__file__).resolve().parent.parent / "web"
SIM_HZ = 100
STATE_HZ = 30
DASH_EVERY = 3        # dashboard gets every 3rd state (10 Hz)
DET_EVERY = 2         # simulated detections at 15 Hz
AI_INFERENCE_S = 0.030  # detector time on the edge GPU (illustrative)
MAX_RESULTS = 500


RESCUE_ALIASES = {"rescue": "flood"}
LAYER_MIN_S = 0.5     # stream the hazard layer at most twice a second...
LAYER_REFRESH_S = 2.0  # ...and re-send it every 2 s so a client that missed one catches up


def make_worlds(name: str) -> tuple[dict, str]:
    """All worlds the server can switch between, and the one to start in."""
    if name == "arm":
        from .arm_world import ArmWorld
        return {"arm": ArmWorld()}, "arm"
    from .rescue_world import SCENARIOS, RescueWorld
    name = RESCUE_ALIASES.get(name, name)
    return {s: RescueWorld(s) for s in SCENARIOS}, name


class Server:
    def __init__(self, worlds: dict, start: str) -> None:
        self.worlds = worlds
        self.world = worlds[start]
        self.layer_sent = {"op": (None, -1e9), "dash": (None, -1e9)}
        self.safety = Safety()
        self.net = NetCtl()
        self.log = RunLogger()
        self.rng = random.Random(1)
        self.operator: web.WebSocketResponse | None = None
        self.uplink: DelayLine | None = None
        self.downlink: DelayLine | None = None
        self.dashboards: set[web.WebSocketResponse] = set()
        self.last_cmd: P.Command | None = None
        self.cmd_times: deque[float] = deque(maxlen=200)
        self.rejects: dict[str, int] = {}
        self.client_rtt: dict = {}
        self.base_rtt_ms: float | None = None   # the real link's RTT with no emulation (Quest-measured)
        self.results: list[dict] = []           # missions + AI-stop tests, for the "Why 5G" page
        self.blocked_count = 0
        self.tasks: list[asyncio.Task] = []
        self.t0 = time.monotonic()

    # ---------------------------------------------------------------- utils
    def now(self) -> float:
        return time.monotonic()

    def rel(self, t: float) -> float:
        return t - self.t0

    def event(self, name: str, detail: str = "") -> None:
        self.log.row("events", self.rel(self.now()), name, detail, self.net.added_rtt_ms)

    def notice_operator(self, text: str) -> None:
        if self.downlink:
            self.downlink.put(P.dumps({"type": "notice", "text": text}))

    def hello(self) -> str:
        msg = {"type": "hello", "scenario": self.world.name, **self.world.hello(), "profiles": profiles_json()}
        if len(self.worlds) > 1:
            msg["scenarios"] = [{"id": k, "title": getattr(w.hazard, "title", k)} for k, w in self.worlds.items()]
        return P.dumps(msg)

    def with_layer(self, msg: dict, who: str, now: float) -> dict:
        """Attach the big hazard layer (wildfire cells) only when it changed, at most every 0.5 s."""
        lay = self.world.layer()
        if lay is None:
            return msg
        v_sent, t_sent = self.layer_sent[who]
        if now - t_sent < LAYER_MIN_S or (lay[0] == v_sent and now - t_sent < LAYER_REFRESH_S):
            return msg
        self.layer_sent[who] = (lay[0], now)
        out = dict(msg)
        out["layer"] = {"v": lay[0], "data": lay[1], "scenario": self.world.name}
        return out

    def switch_scenario(self, name: str) -> None:
        name = RESCUE_ALIASES.get(name, name)
        if name not in self.worlds:
            return
        now = self.now()
        self.safety.disarm(now, "scenario_change")
        self.world = self.worlds[name]
        self.world.reset()
        self.world.events.clear()
        self.layer_sent = {"op": (None, -1e9), "dash": (None, -1e9)}
        self.log.start(label=f"scenario-{name}", scenario=name, added_rtt_ms=self.net.added_rtt_ms)
        self.event("scenario", name)
        hello = self.hello()
        targets = list(self.dashboards) + ([self.operator] if self.operator is not None else [])
        for ws in targets:
            asyncio.ensure_future(self._send_quiet(ws, hello))
        title = getattr(getattr(self.world, "hazard", None), "title", name)
        self.notice_operator(f"Scenario: {title} - press A to arm")

    @staticmethod
    async def _send_quiet(ws, data: str) -> None:
        try:
            await ws.send_str(data)
        except (ConnectionError, RuntimeError):
            pass

    # ---------------------------------------------------------------- loops
    def sim_tick(self, dt: float) -> None:
        now = self.now()
        reason = self.world.hazard_reason()
        if reason:
            # simulated edge AI (person detected) or vehicle protection (overheat): stop only, never move
            self.safety.stop(now, reason)
        self.safety.tick(now)
        self.world.step(dt, self.safety.state == P.ARMED, self.last_cmd)
        for t, ev, detail in self.safety.events:
            self.log.row("events", self.rel(t), ev, detail, self.net.added_rtt_ms)
        self.safety.events.clear()
        for _, ev, detail in self.world.events:
            self.event(ev, detail)
            if ev == "task_start":
                self.blocked_count = 0
            elif ev == "blocked":
                self.blocked_count += 1
            elif ev == "task_complete":
                self.record_mission()
        self.world.events.clear()
        for r in getattr(self.world, "stop_results", ()):
            self.record({"kind": "stop_test", **r})
        if getattr(self.world, "stop_results", None):
            self.world.stop_results.clear()

    # ------------------------------------------------------------ results
    def record(self, r: dict) -> None:
        prof = PROFILES.get(r.get("profile") or self.net.profile_id, self.net.profile)
        r = {"scenario": self.world.name, "profile": prof.id, "network": prof.label,
             "extra_ms": self.net.added_rtt_ms, "rtt_p50": self.client_rtt.get("p50"),
             "wall": round(time.time(), 1), **r}
        r["profile"], r["network"] = prof.id, prof.label
        self.results.append(r)
        del self.results[:-MAX_RESULTS]
        self.log.row("results", r["kind"], r["scenario"], r["profile"], r["extra_ms"], r["rtt_p50"],
                     r.get("speed_kmh"), r.get("reaction_ms"), r.get("travelled_m"), r.get("margin_m"),
                     r.get("result"), r.get("rescued"), r.get("time_s"), r.get("blocked"))

    def record_mission(self) -> None:
        s = self.world.snapshot()
        self.record({"kind": "mission", "rescued": s.get("score"), "target": s.get("target"),
                     "airlifted": s.get("airlifted", 0), "time_s": s.get("task_s"), "blocked": self.blocked_count})

    def ai_pipeline_latency_s(self) -> float:
        """Camera frame up to the AI server + inference + stop command back down, on the active network.
        The real private-5G part is the Quest-measured RTT; the emulated network adds its own delay."""
        base = (self.base_rtt_ms if self.base_rtt_ms is not None else 10.0) / 1000.0
        return (base + 2 * self.net.one_way_s + self.net.sample_jitter_s() + self.net.sample_jitter_s()
                + AI_INFERENCE_S)

    async def sim_loop(self) -> None:
        dt = 1.0 / SIM_HZ
        nxt = self.now()
        while True:
            self.sim_tick(dt)
            nxt += dt
            await asyncio.sleep(max(0.0, nxt - self.now()))
            if self.now() - nxt > 0.5:  # fell far behind (debugger, sleep) -> resync
                nxt = self.now()

    def state_msg(self, with_det: bool) -> dict:
        now = self.now()
        recent = [t for t in self.cmd_times if now - t < 1.0]
        msg = {
            "type": "state",
            "t_srv": round(self.rel(now), 4),
            "robot": {
                "state": self.safety.state,
                "reason": self.safety.reason,
                "hold_latency_ms": None if self.safety.hold_latency_ms is None
                else round(self.safety.hold_latency_ms, 1),
            },
            "sim": self.world.snapshot(),
            "net": {
                "profile": self.net.describe(),
                "base_rtt_ms": None if self.base_rtt_ms is None else round(self.base_rtt_ms, 1),
                "added_rtt_ms": self.net.added_rtt_ms,
                "link_cut": self.net.link_cut,
                "operator": self.operator is not None,
                "cmd_hz": len(recent),
            },
            "run": {"id": self.log.run_id, "participant": self.log.meta.get("participant", "")},
        }
        if with_det:
            msg["det"] = self.world.detections(self.rng)
        return msg

    async def broadcast_loop(self) -> None:
        i = 0
        while True:
            i += 1
            msg = self.state_msg(with_det=(i % DET_EVERY == 0))
            now = self.now()
            if self.downlink:
                self.downlink.put(P.dumps(self.with_layer(msg, "op", now)))
            if i % DASH_EVERY == 0 and self.dashboards:
                msg["client_rtt"] = self.client_rtt
                msg["rejects"] = self.rejects
                data = P.dumps(self.with_layer(msg, "dash", now))
                for ws in list(self.dashboards):
                    try:
                        await ws.send_str(data)
                    except (ConnectionError, RuntimeError):
                        self.dashboards.discard(ws)
            if i % STATE_HZ == 0:
                self.log.flush()
            await asyncio.sleep(1.0 / STATE_HZ)

    # ------------------------------------------------------- operator path
    def on_stale(self, item, late: float) -> None:
        kind, msg = item
        if kind == "command":
            self.rejects["stale"] = self.rejects.get("stale", 0) + 1
            self.log.row("commands", self.rel(self.now()), msg.seq, 0, "stale", self.safety.state,
                         int(msg.clutch), msg.vx, msg.vy, msg.vz, int(msg.gripper), self.net.added_rtt_ms)

    async def deliver_uplink(self, item) -> None:
        """Runs when an operator message 'arrives at the robot' after the injected delay."""
        kind, msg = item
        now = self.now()
        if kind == "command":
            d = self.safety.on_command(msg, now)
            if d.accepted:
                self.cmd_times.append(now)
                self.last_cmd = msg
            else:
                self.rejects[d.reason] = self.rejects.get(d.reason, 0) + 1
            self.log.row("commands", self.rel(now), msg.seq, int(d.accepted), d.reason, self.safety.state,
                         int(msg.clutch), msg.vx, msg.vy, msg.vz, int(msg.gripper), self.net.added_rtt_ms)
            if self.downlink:
                self.downlink.put(P.dumps({"type": "ack", "seq": msg.seq, "t_op": msg.t_op,
                                           "state": self.safety.state}))
        elif kind == "control":
            a = msg["action"]
            if a == "arm":
                ok, why = self.safety.arm(now)
                self.notice_operator("ARMED" if ok else f"Cannot arm: {why.replace('_', ' ')}")
            elif a == "disarm":
                self.safety.disarm(now, "operator")
            elif a == "stop":
                self.safety.stop(now, "operator")
        elif kind == "world":
            notice = self.world.operator_msg(msg, self.safety.state == P.ARMED)
            if notice:
                self.notice_operator(notice)
        elif kind == "rtt":
            self.client_rtt = msg
            if msg.get("p50") is not None:            # the real link = measured minus what we emulate
                emulated = self.net.added_rtt_ms + self.net.profile.rtt_ms
                self.base_rtt_ms = max(0.5, float(msg["p50"]) - emulated)
            self.log.row("rtt", self.rel(now), msg.get("p50"), msg.get("p95"), msg.get("last"),
                         msg.get("n"), self.net.added_rtt_ms)

    async def ws_operator(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None, max_msg_size=64 * 1024)
        await ws.prepare(request)
        if self.operator is not None:      # newest operator wins (e.g. after a Wi-Fi drop)
            # code 4001 tells the old page not to auto-reconnect (no tug-of-war between two tabs)
            await self.operator.close(code=4001, message=b"replaced by a new operator")
        self._teardown_operator()
        self.operator = ws
        self.safety.new_session(self.now())
        self.uplink = DelayLine(self.net, self.deliver_uplink, STALE_S, self.on_stale).start()
        self.downlink = DelayLine(self.net, ws.send_str).start()
        self.layer_sent["op"] = (None, -1e9)
        await ws.send_str(self.hello())   # map/geometry immediately, not delayed
        self.event("operator_connected", request.remote or "")
        try:
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    continue
                try:
                    msg = P.parse(m.data)
                    t = msg["type"]
                    if t == "command":
                        self.uplink.put(("command", P.parse_command(msg)))
                    elif t == "control":
                        self.uplink.put(("control", P.parse_control(msg, P.OPERATOR_CONTROLS)))
                    elif t in P.WORLD_MESSAGES:
                        self.uplink.put(("world", P.parse_world_msg(msg, self.world.bounds)))
                    elif t == "rtt":
                        self.uplink.put(("rtt", {k: msg.get(k) for k in ("p50", "p95", "last", "n")}))
                except P.ProtocolError as e:
                    self.rejects["malformed"] = self.rejects.get("malformed", 0) + 1
                    await ws.send_str(P.dumps({"type": "notice", "text": f"rejected: {e}"}))
        finally:
            if self.operator is ws:
                self.safety.stop(self.now(), "link")
                self._teardown_operator()
                self.event("operator_disconnected", request.remote or "")
        return ws

    def _teardown_operator(self) -> None:
        for line in (self.uplink, self.downlink):
            if line:
                line.stop()
        self.operator = self.uplink = self.downlink = None
        self.last_cmd = None

    # ------------------------------------------------------ dashboard path
    async def ws_dashboard(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        self.dashboards.add(ws)
        await ws.send_str(self.hello())
        try:
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    continue
                try:
                    msg = P.parse(m.data)
                    if msg["type"] == "control":
                        self.dashboard_control(P.parse_control(msg, P.DASHBOARD_CONTROLS))
                except P.ProtocolError as e:
                    await ws.send_str(P.dumps({"type": "notice", "text": f"rejected: {e}"}))
        finally:
            self.dashboards.discard(ws)
        return ws

    def dashboard_control(self, c: dict) -> None:
        t0 = self.now()
        try:
            self._dashboard_control(c)
        finally:
            # Some hazard actions re-plan a whole map (e.g. a wind shift, ~0.3 s). The event loop
            # was busy, not the link: don't let the watchdog mistake that pause for a lost link.
            spent = self.now() - t0
            if spent > 0.05:
                self.safety.extend_watchdog(spent)

    def _dashboard_control(self, c: dict) -> None:
        now, a = self.now(), c["action"]
        if a == "stop":
            self.safety.stop(now, c.get("source", "dashboard"))
        elif a == "disarm":
            self.safety.disarm(now, "dashboard")
        elif a == "set_delay_ms":
            old = self.net.added_rtt_ms
            self.net.added_rtt_ms = c["value"]
            # raising the delay leaves a gap in command arrivals; don't call that a link loss
            self.safety.extend_watchdog(max(0.0, (c["value"] - old) / 2000.0))
            self.event("set_delay", f"{old:.0f}->{c['value']:.0f}")
        elif a == "cut_link":
            self.net.link_cut = c["value"]
            self.event("link_cut" if c["value"] else "link_restored")
        elif a == "reset_task":
            self.world.reset()
        elif a == "set_scenario":
            self.switch_scenario(c["value"])
        elif a == "set_profile":
            if c["value"] in PROFILES:
                old = self.net.one_way_s
                self.net.profile_id = c["value"]
                # a slower network leaves a gap in command arrivals; don't call that a link loss
                self.safety.extend_watchdog(max(0.0, self.net.one_way_s - old) + 0.05)
                self.event("set_profile", self.net.profile.label)
        elif a == "person_on_road":
            self.world.dashboard_msg({**c, "ai_latency_s": self.ai_pipeline_latency_s(),
                                      "profile": self.net.profile_id})
        elif a == "start_run":
            self.world.reset()
            rid = self.log.start(c["participant"], c["label"], scenario=self.world.name,
                                 added_rtt_ms=self.net.added_rtt_ms)
            self.event("run_start", rid)
        else:
            self.world.dashboard_msg(c)

    # ------------------------------------------------------------------ app
    async def on_startup(self, app: web.Application) -> None:
        self.log.start(label=f"session-{self.world.name}", scenario=self.world.name)
        self.tasks = [asyncio.create_task(self.sim_loop()), asyncio.create_task(self.broadcast_loop())]

    async def on_cleanup(self, app: web.Application) -> None:
        for t in self.tasks:
            t.cancel()
        self._teardown_operator()
        self.log.close()


@web.middleware
async def no_cache(request: web.Request, handler):
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


PAGES = ["xr", "dashboard", "rescue", "rescue-dashboard", "why5g"]


def make_app(scenario: str = "flood") -> web.Application:
    srv = Server(*make_worlds(scenario))
    app = web.Application(middlewares=[no_cache])
    app["server"] = srv

    def page(path: Path):
        async def handler(_request):
            return web.FileResponse(path)
        return handler

    async def to_client(_request):
        raise web.HTTPFound(srv.world.client_path)

    async def status(_request):
        return web.json_response(srv.state_msg(with_det=False))

    async def results(_request):
        return web.json_response({"profiles": profiles_json(), "profile": srv.net.describe(),
                                  "base_rtt_ms": srv.base_rtt_ms, "client_rtt": srv.client_rtt,
                                  "results": srv.results})

    async def health(_request):
        return web.json_response({
            "status": "ok",
            "scenario": srv.world.name,
            "uptime_s": round(srv.rel(srv.now()), 1),
            "operator_connected": srv.operator is not None,
            "dashboards_connected": len(srv.dashboards),
            "safety_state": srv.safety.state,
        })

    async def no_icon(_request):
        return web.Response(status=204)

    app.router.add_get("/", to_client)
    app.router.add_get("/favicon.ico", no_icon)
    for name in PAGES:
        if (WEB / name / "index.html").exists():
            app.router.add_get(f"/{name}/", page(WEB / name / "index.html"))
    app.router.add_get("/ws/operator", srv.ws_operator)
    app.router.add_get("/ws/dashboard", srv.ws_dashboard)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/results", results)
    app.router.add_get("/api/health", health)
    app.router.add_get("/healthz", health)
    for name in PAGES + ["vendor", "common"]:
        if (WEB / name).is_dir():
            app.router.add_static(f"/{name}", WEB / name)
    app.on_startup.append(srv.on_startup)
    app.on_cleanup.append(srv.on_cleanup)
    return app


def _load_dotenv(path: Path | None = None) -> None:
    env_file = path if path is not None else Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def _high_res_timer() -> None:
    """Windows sleeps in ~15.6 ms steps by default; ask for 1 ms so the 100 Hz
    loop and the delay injection are accurate."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except (OSError, AttributeError):
            pass


def main() -> None:
    _load_dotenv()
    env_scenario = os.environ.get("SCENARIO", "flood")
    env_port_str = os.environ.get("SERVER_PORT") or os.environ.get("PORT")
    env_port = int(env_port_str) if env_port_str else 8443
    env_host = os.environ.get("SERVER_HOST", "0.0.0.0")

    ap = argparse.ArgumentParser(description="5G Remote Hands simulation server")
    ap.add_argument("--scenario", default=env_scenario,
                    choices=["flood", "earthquake", "wildfire", "landslide", "rescue", "arm"])
    ap.add_argument("--host", default=env_host, help=f"host to bind to (default: {env_host})")
    ap.add_argument("--port", type=int, default=env_port, help=f"HTTPS port (default: {env_port})")
    ap.add_argument("--http", type=int, default=None, metavar="PORT",
                    help="serve plain HTTP on PORT instead (desktop only)")
    ap.add_argument("--auto-cert", action="store_true", default=True,
                    help="automatically generate self-signed cert if missing (default: True)")
    ap.add_argument("--no-auto-cert", dest="auto_cert", action="store_false")
    args = ap.parse_args()
    _high_res_timer()

    ctx, port, scheme = None, args.port, "https"
    if args.http:
        port, scheme = args.http, "http"
    else:
        if not (CERT.exists() and KEY.exists()):
            auto_gen = args.auto_cert and (os.environ.get("AUTO_GEN_CERT", "1").lower() in ("1", "true", "yes"))
            if auto_gen:
                print("TLS certificates not found. Generating self-signed certificate...")
                make_cert()
            else:
                sys.exit("No certificate. Run:  python -m server.certs")
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(CERT, KEY)

    app = make_app(args.scenario)
    world = app["server"].world
    others = [k for k in app["server"].worlds if k != world.name]
    print(f"\n5G Remote Hands server — scenario: {world.name}"
          + (f" (switch live from the dashboard: {', '.join(others)})" if others else ""))
    for ip in local_ipv4s() or ["localhost"]:
        print(f"  Operator (Quest): {scheme}://{ip}:{port}{world.client_path}")
        print(f"  Dashboard:        {scheme}://{ip}:{port}{world.dashboard_path}")
    print()
    web.run_app(app, host=args.host, port=port, ssl_context=ctx, print=None)


if __name__ == "__main__":
    main()
