"""CSV logs per run: logs/<run_id>/{run.json, commands.csv, rtt.csv, events.csv}."""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

LOG_ROOT = Path(__file__).resolve().parent.parent / "logs"


class RunLogger:
    def __init__(self, root: Path = LOG_ROOT) -> None:
        self.root = root
        self.run_id = ""
        self.meta: dict = {}
        self._files: list = []
        self._w: dict[str, csv.writer] = {}

    def start(self, participant: str = "", label: str = "", **meta) -> str:
        self.close()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{stamp}_{participant}" if participant else stamp
        d = self.root / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        self.meta = {"run_id": self.run_id, "participant": participant, "label": label,
                     "started": datetime.now().isoformat(timespec="seconds"), **meta}
        (d / "run.json").write_text(json.dumps(self.meta, indent=2))
        headers = {
            "commands": ["t", "seq", "accepted", "reason", "state", "clutch", "vx", "vy", "vz", "gripper", "added_rtt_ms"],
            "rtt": ["t", "rtt_p50_ms", "rtt_p95_ms", "rtt_last_ms", "samples", "added_rtt_ms"],
            "events": ["t", "event", "detail", "added_rtt_ms"],
            "results": ["kind", "scenario", "profile", "extra_ms", "rtt_p50_ms", "speed_kmh", "reaction_ms",
                        "travelled_m", "margin_m", "result", "rescued", "time_s", "blocked"],
        }
        for name, cols in headers.items():
            f = open(d / f"{name}.csv", "w", newline="", encoding="utf-8")
            w = csv.writer(f)
            w.writerow(cols)
            self._files.append(f)
            self._w[name] = w
        return self.run_id

    def row(self, name: str, *values) -> None:
        w = self._w.get(name)
        if w is not None:
            w.writerow([f"{v:.4f}" if isinstance(v, float) else v for v in values])

    def flush(self) -> None:
        for f in self._files:
            f.flush()

    def close(self) -> None:
        for f in self._files:
            f.close()
        self._files, self._w = [], {}


def wall() -> float:
    return time.time()
