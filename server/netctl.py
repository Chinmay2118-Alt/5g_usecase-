"""Latency and link-loss injection (stands in for `tc netem`, which Windows lacks).

The operator <-> robot path gets extra delay, split evenly between the uplink
(commands) and the downlink (state and acks):

  * a **network profile** emulating another kind of network (public 5G, 4G,
    busy Wi-Fi, satellite) on top of the real private-5G link, with latency,
    jitter and packet loss;
  * plus an optional **extra delay** (the dashboard's 0-400 ms buttons).

WebSocket runs over TCP, so the emulation behaves like TCP:
  * delivery stays in order: a late (jittered) message also holds up the ones
    behind it (head-of-line blocking);
  * a lost packet is not dropped but retransmitted: that message, and
    everything queued behind it, stalls for RETX_S.

A "cut link" switch drops all operator traffic in both directions.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

STALE_S = 0.150  # drop a command delivered more than this late (project doc: 150 ms)
RETX_S = 0.200   # TCP retransmission stall for a lost packet


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    rtt_ms: float        # added round-trip time on top of the real link
    jitter_ms: float     # typical extra one-way variation
    loss: float          # probability that a message needs a TCP retransmission
    spiky: bool = False  # occasional large delay spikes (contention on unlicensed spectrum)
    live: bool = False   # True = the real link, no emulation
    note: str = ""


# Typical values for illustration (they vary by deployment); edit here to tune the comparison.
# loss = chance per message of a TCP retransmission stall. Each stall (200 ms) is longer than the
# 180 ms watchdog, so on lossy networks the rover's safety layer will occasionally hold: that is
# the honest consequence of an unreliable link, and part of the comparison.
PROFILES: dict[str, Profile] = {p.id: p for p in [
    Profile("p5g_mec", "Private 5G + MEC (this lab)", 0, 0, 0.0, live=True,
            note="Real measured link: robot, AI and safety on the edge server next to the 5G core"),
    Profile("public_5g", "Public 5G → cloud", 35, 8, 0.0002,
            note="Operator network, server in a cloud region: internet backhaul adds latency"),
    Profile("lte", "4G LTE → cloud", 70, 20, 0.001,
            note="Older radio with longer scheduling delays, plus internet backhaul"),
    Profile("wifi_busy", "Busy Wi-Fi", 25, 40, 0.003, spiky=True,
            note="Unlicensed spectrum shared with other devices: contention causes spikes and loss"),
    Profile("satellite", "Satellite (GEO) → cloud", 600, 30, 0.002,
            note="Geostationary link: ~36,000 km up and down"),
]}
DEFAULT_PROFILE = "p5g_mec"


class NetCtl:
    def __init__(self, seed: int = 3) -> None:
        self.added_rtt_ms: float = 0.0
        self.link_cut: bool = False
        self.profile_id: str = DEFAULT_PROFILE
        self.rng = random.Random(seed)

    @property
    def profile(self) -> Profile:
        return PROFILES[self.profile_id]

    @property
    def one_way_s(self) -> float:
        """Mean added one-way delay (profile + extra), without jitter."""
        return (self.added_rtt_ms + self.profile.rtt_ms) / 2000.0

    def sample_jitter_s(self) -> float:
        """Extra one-way delay for one message: jitter (always >= 0) plus a possible TCP stall."""
        p = self.profile
        j = 0.0
        if p.jitter_ms:
            j = abs(self.rng.gauss(0.0, p.jitter_ms / 2)) / 1000.0
            if p.spiky and self.rng.random() < 0.06:
                j += self.rng.uniform(p.jitter_ms, 3 * p.jitter_ms) / 1000.0
        if p.loss and self.rng.random() < p.loss:
            j += RETX_S
        return j

    def sample_one_way_s(self) -> float:
        return self.one_way_s + self.sample_jitter_s()

    def describe(self) -> dict:
        return {"id": self.profile_id, "label": self.profile.label, "live": self.profile.live}


def profiles_json() -> list[dict]:
    return [asdict(p) for p in PROFILES.values()]


class DelayLine:
    """FIFO that releases each item after the emulated one-way delay, in order."""

    def __init__(
        self,
        net: NetCtl,
        deliver: Callable[[Any], Awaitable[None]],
        stale_s: float | None = None,
        on_stale: Callable[[Any, float], None] | None = None,
    ) -> None:
        self.net = net
        self.deliver = deliver
        self.stale_s = stale_s
        self.on_stale = on_stale
        self.q: asyncio.Queue = asyncio.Queue()
        self.dropped_cut = 0
        self._last_due = 0.0
        self._task: asyncio.Task | None = None

    def start(self) -> "DelayLine":
        self._task = asyncio.create_task(self._run())
        return self

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def put(self, item: Any) -> None:
        if self.net.link_cut:
            self.dropped_cut += 1
            return
        due = time.monotonic() + self.net.sample_one_way_s()
        due = max(due, self._last_due)       # TCP: in order, so a delayed message holds up the next
        self._last_due = due
        self.q.put_nowait((due, item))

    async def _run(self) -> None:
        while True:
            due, item = await self.q.get()
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if self.net.link_cut:          # cut while in flight
                self.dropped_cut += 1
                continue
            late = time.monotonic() - due
            if self.stale_s is not None and late > self.stale_s:
                if self.on_stale:
                    self.on_stale(item, late)
                continue
            try:
                await self.deliver(item)
            except (ConnectionError, RuntimeError):
                pass  # receiver went away; the safety layer handles the rest
