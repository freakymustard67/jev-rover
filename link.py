"""Transport to the rover, and the telemetry coming back.

One wire protocol, three implementations:
  * UDPLink    -- real ESP32 over WiFi (see firmware/README.md)
  * MockLink   -- a tiny simulated rover so the loop runs with no hardware
  * DryRunLink -- accepts commands, transmits nothing (safe observation mode)

Safety stance: the link is not trusted with safety. The ESP32 owns a watchdog
(stop on command loss) and its own distance reflex. The laptop mirrors the
reflex so behavior is identical in mock and live runs.
"""
from __future__ import annotations

import json
import math
import socket

from scene import Hardware

DEFAULT_PORT = 4210
DEFAULT_LOCAL_PORT = 4211


class Cmd:
    __slots__ = ("v_mps", "w_deg_s", "source", "note")

    def __init__(self, v_mps: float = 0.0, w_deg_s: float = 0.0,
                 source: str = "default", note: str = ""):
        self.v_mps = float(v_mps)
        self.w_deg_s = float(w_deg_s)
        self.source = source
        self.note = note

    def __repr__(self) -> str:
        return f"Cmd(v={self.v_mps:+.2f} m/s, w={self.w_deg_s:+05.1f} deg/s, {self.source})"

    def as_dict(self) -> dict:
        return {"v_mps": round(self.v_mps, 3), "w_deg_s": round(self.w_deg_s, 1),
                "source": self.source, "note": self.note}


class RoverLink:
    def send(self, cmd: Cmd, t: float) -> None:
        raise NotImplementedError

    def telemetry(self, t: float) -> Hardware:
        return Hardware()

    def close(self) -> None:
        pass

    def stats(self) -> dict:
        return {}


class DryRunLink(RoverLink):
    """Never transmits. Default until the link is explicitly armed."""

    def __init__(self):
        self.sent = 0
        self.last: Cmd | None = None

    def send(self, cmd: Cmd, t: float) -> None:
        self.sent += 1
        self.last = cmd

    def stats(self) -> dict:
        return {"link": "dry_run", "commands_logged": self.sent}


class UDPLink(RoverLink):
    """Laptop -> ESP32 commands, ESP32 -> laptop telemetry, JSON over UDP."""

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 local_port: int = DEFAULT_LOCAL_PORT, ttl_ms: int = 400):
        self.addr = (host, int(port))
        self.ttl_ms = int(ttl_ms)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", int(local_port)))
        self.sock.setblocking(False)
        self.seq = 0
        self.telemetry_rx = 0
        self.send_errors = 0
        self.last_rx_t: float | None = None
        self.last_hw = Hardware()

    def send(self, cmd: Cmd, t: float) -> None:
        self.seq += 1
        pkt = {
            "t": round(t, 3), "seq": self.seq,
            "v": round(cmd.v_mps, 3), "w": round(math.radians(cmd.w_deg_s), 3),
            "ttl_ms": self.ttl_ms,
        }
        try:
            self.sock.sendto(json.dumps(pkt).encode(), self.addr)
        except OSError:
            self.send_errors += 1

    def telemetry(self, t: float) -> Hardware:
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                break
            try:
                pkt = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(pkt, dict) or "seq" not in pkt:
                continue
            self.telemetry_rx += 1
            self.last_rx_t = t
            # Firmware contract: "watchdog": true means the watchdog has TRIPPED.
            self.last_hw = Hardware(
                dist_front_m=pkt.get("dist_front_m"),
                dist_rear_m=pkt.get("dist_rear_m"),
                batt_v=pkt.get("batt_v"),
                watchdog_ok=not bool(pkt.get("watchdog", True)),
                reflex=bool(pkt.get("reflex", False)),
                telemetry_age_s=0.0,
            )
        if self.last_rx_t is not None:
            self.last_hw.telemetry_age_s = round(t - self.last_rx_t, 2)
        return self.last_hw

    def close(self) -> None:
        self.sock.close()

    def stats(self) -> dict:
        return {"link": "udp", "commands": self.seq, "telemetry_rx": self.telemetry_rx,
                "send_errors": self.send_errors}


class MockLink(RoverLink):
    """Simulated rover: obeys commands, reports plausible telemetry."""

    def __init__(self, ttl_ms: int = 400):
        self.ttl_ms = ttl_ms
        self.seq = 0
        self.last_cmd_t: float | None = None
        self.last: Cmd | None = None

    def _active(self, t: float) -> bool:
        return self.last_cmd_t is not None and (t - self.last_cmd_t) * 1000.0 <= self.ttl_ms

    def send(self, cmd: Cmd, t: float) -> None:
        self.seq += 1
        self.last_cmd_t = t
        self.last = cmd

    def telemetry(self, t: float) -> Hardware:
        return Hardware(dist_front_m=3.0, dist_rear_m=3.0, batt_v=7.4,
                        watchdog_ok=self._active(t), reflex=False, telemetry_age_s=0.0)

    def stats(self) -> dict:
        return {"link": "mock", "commands": self.seq,
                "ttl_ok": self.last_cmd_t is not None}
