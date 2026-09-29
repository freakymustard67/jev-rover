"""Loopback fake ESP32: answers commands and emits spec-v1 scan chunks.

Used by ``tools/smoke_scan.py --loopback`` and the loopback integration test.
It replays the in-repo room + sensor model (``tools/sim/tof_sim.py``), so the
whole host path - ``encode_scan_command`` -> UDP -> chunk decode -> assembly ->
matcher - can be exercised without hardware.

Timing is compressed: chunks for a requested scan are emitted as soon as the
request arrives, while the chunk header still carries realistic ``t0_ms`` /
``period_us`` values (the matcher's desmear uses those, not wall time).
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools" / "sim"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np

import tof_sim as sim
from scan_payload import STATUS_TIMEOUT, ScanChunk, ScanSample, chunk_plan, encode_scan_chunk

DEFAULT_SWEEP_S = 1.0


class FakeRover:
    """A UDP peer that behaves like the firmware's scan mode, in-sim."""

    def __init__(self, pose, *, host: str = "127.0.0.1", port: int = 0,
                 noise_seed: int = 7, sweep_s: float = DEFAULT_SWEEP_S,
                 telemetry_hz: float = 20.0):
        self.pose = np.asarray(pose, dtype=float)
        self.sweep_s = float(sweep_s)
        self.telemetry_period = 1.0 / max(1.0, telemetry_hz)
        self.rng = np.random.default_rng(noise_seed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, int(port)))
        self.host, self.port = self.sock.getsockname()
        self.requests: list[dict] = []
        self.last_scan_id = 0
        self.scan_state = "idle"
        self.motion = (0.0, 0.0)          # (v, w) to simulate during the sweep
        self.drop_chunks: set[int] = set()  # chunk indices to omit (loss simulation)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> "FakeRover":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.sock.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- wiring -------------------------------------------------------------
    def _run(self) -> None:
        peer = None
        last_telemetry = 0.0
        while not self._stop.is_set():
            self.sock.settimeout(0.05)
            try:
                data, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                data = None
            except OSError:
                break
            if data is not None:
                self._handle(data, peer)
            if peer is not None and time.time() - last_telemetry >= self.telemetry_period:
                self._send_telemetry(peer)
                last_telemetry = time.time()

    def _handle(self, data: bytes, peer) -> None:
        try:
            pkt = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(pkt, dict) or "seq" not in pkt:
            return
        scan = pkt.get("scan")
        if not isinstance(scan, dict):
            return
        self.requests.append(scan)
        if scan.get("action") == "stop":
            self.scan_state = "idle"
            return
        if scan.get("action") == "start" and int(scan.get("id", -1)) != self.last_scan_id:
            self.last_scan_id = int(scan["id"])
            self.scan_state = "scanning"
            self._emit_scan(scan, peer)
            self.scan_state = "idle"

    # -- scan emission ------------------------------------------------------
    def _emit_scan(self, scan: dict, peer) -> None:
        start = float(scan.get("start_deg", -90.0))
        end = float(scan.get("end_deg", 90.0))
        step = float(scan.get("step_deg", 6.0))
        bearings = np.arange(start, end + 1e-9, step)
        v, w = self.motion
        ranges = sim.simulate_sweep(self.pose, bearings, self.rng,
                                    v=v, w_deg_s=w, sweep_s=self.sweep_s)
        n = len(bearings)
        period_us = int(self.sweep_s * 1e6 / max(1, n - 1)) if n > 1 else 0
        samples = []
        for bearing, rng_m in zip(bearings, ranges):
            if np.isfinite(rng_m) and 0.04 <= rng_m <= sim.MAX_RANGE:
                mm = int(round(rng_m * 1000.0))
                samples.append(ScanSample(int(round(bearing)), mm, 0))
            else:
                samples.append(ScanSample(int(round(bearing)), 0, STATUS_TIMEOUT))
        plan = chunk_plan(n)
        for idx, (first, count) in enumerate(plan):
            if idx in self.drop_chunks:
                continue
            chunk = ScanChunk(scan_id=self.last_scan_id, chunk_idx=idx,
                              chunk_count=len(plan), first_idx=first, t0_ms=0,
                              period_us=period_us, samples=tuple(samples[first:first + count]))
            self.sock.sendto(encode_scan_chunk(chunk), peer)

    def _send_telemetry(self, peer) -> None:
        pkt = {"t": round(time.time(), 3), "seq": 1, "dist_front_m": 3.0,
               "dist_rear_m": 3.0, "batt_v": 7.4, "reflex": False, "watchdog": False,
               "uptime_s": 1.0, "scan_seq": self.last_scan_id,
               "scan_state": self.scan_state}
        try:
            self.sock.sendto(json.dumps(pkt).encode(), peer)
        except OSError:
            pass


def main(argv=None) -> int:
    """Standalone fake rover for manual bench runs: prints its port, serves forever."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pose", default="3.0,1.8,0", help="truth x,y,yaw_deg")
    p.add_argument("--motion", default="0,0", help="simulated v,w during a sweep")
    p.add_argument("--port", type=int, default=4210)
    args = p.parse_args(argv)
    x, y, yaw = (float(v) for v in args.pose.split(","))
    v, w = (float(z) for z in args.motion.split(","))
    rover = FakeRover((x, y, yaw), port=args.port)
    rover.motion = (v, w)
    rover.start()
    print(f"[fake-rover] listening on {rover.host}:{rover.port} pose=({x},{y},{yaw}) "
          f"motion=({v},{w}) - Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        rover.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
