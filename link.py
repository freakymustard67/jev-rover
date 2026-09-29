"""Transport to the rover, and the telemetry coming back.

One wire protocol, three implementations:
  * UDPLink    -- real ESP32 over WiFi (see firmware/README.md)
  * MockLink   -- a tiny simulated rover so the loop runs with no hardware
  * DryRunLink -- accepts commands, transmits nothing (safe observation mode)

UDPLink speaks JSON both ways; the ESP32 may also send spec-v1 binary scan
chunks (v6 §2.2, magic 0x53) which are routed to `scan_payload.py` and exposed
via `last_scan` / `scan_assembler` — they never touch the JSON telemetry path.

Safety stance: the link is not trusted with safety. The ESP32 owns a watchdog
(stop on command loss) and its own distance reflex. The laptop mirrors the
reflex so behavior is identical in mock and live runs.
"""
from __future__ import annotations

import json
import math
import socket

from scan_payload import (
    ScanAssembly,
    ScanAssembler,
    ScanChunk,
    ScanPayloadError,
    decode_scan_chunk,
    is_scan_chunk,
)
from scene import Hardware

DEFAULT_PORT = 4210
DEFAULT_LOCAL_PORT = 4211


class Cmd:
    __slots__ = ("v_mps", "w_deg_s", "source", "note", "scan")

    def __init__(self, v_mps: float = 0.0, w_deg_s: float = 0.0,
                 source: str = "default", note: str = "", scan: dict | None = None):
        self.v_mps = float(v_mps)
        self.w_deg_s = float(w_deg_s)
        self.source = source
        self.note = note
        #: Optional scan request (spec v1 §2.3) riding on this command datagram;
        #: must be built by ``encode_scan_command`` so it is wire-valid.
        self.scan = scan

    def __repr__(self) -> str:
        extra = f", scan={self.scan['id']}" if self.scan else ""
        return (f"Cmd(v={self.v_mps:+.2f} m/s, w={self.w_deg_s:+05.1f} deg/s, "
                f"{self.source}{extra})")

    def as_dict(self) -> dict:
        out = {"v_mps": round(self.v_mps, 3), "w_deg_s": round(self.w_deg_s, 1),
               "source": self.source, "note": self.note}
        if self.scan:
            out["scan"] = dict(self.scan)
        return out


def encode_scan_command(scan_id: int, *, action: str = "start", start_deg: float = -90.0,
                        end_deg: float = 90.0, step_deg: float = 6.0,
                        rate_hz: float = 20.0) -> dict:
    """Build the ``scan`` object for a command datagram (spec v1 §2.3).

    Validation mirrors the firmware's ``rvrParseCommand`` rules (scan-link spec
    §4): a violation raises ``ValueError`` - on the wire a malformed scan object
    invalidates the whole datagram, so it must never be built by accident.
    """
    if not isinstance(scan_id, int) or isinstance(scan_id, bool) or not 0 <= scan_id <= 65535:
        raise ValueError(f"scan id must be an integer in [0, 65535], got {scan_id!r}")
    if action not in ("start", "stop"):
        raise ValueError(f"scan action must be start|stop, got {action!r}")
    if action == "stop":
        return {"id": scan_id, "action": "stop"}
    if not (-90.0 <= start_deg <= 90.0 and -90.0 <= end_deg <= 90.0):
        raise ValueError(f"scan angles must be within [-90, 90]: {start_deg}, {end_deg}")
    if not start_deg < end_deg:
        raise ValueError(f"scan start_deg ({start_deg}) must be < end_deg ({end_deg})")
    if not float(step_deg).is_integer() or not 1 <= step_deg <= 180:
        raise ValueError(f"scan step_deg must be an integer in [1, 180], got {step_deg!r}")
    if not float(rate_hz).is_integer() or not 1 <= rate_hz <= 50:
        raise ValueError(f"scan rate_hz must be an integer in [1, 50], got {rate_hz!r}")
    return {"id": scan_id, "action": "start", "start_deg": int(start_deg),
            "end_deg": int(end_deg), "step_deg": int(step_deg), "rate_hz": int(rate_hz)}


def scan_beam_count(start_deg: float, end_deg: float, step_deg: float) -> int:
    return int(round((end_deg - start_deg) / step_deg)) + 1


class RoverLink:
    #: Latest scan telemetry echoed by the rover (None until a scan is run).
    last_scan_seq: int | None = None
    scan_state: str | None = None

    def send(self, cmd: Cmd, t: float) -> None:
        raise NotImplementedError

    def telemetry(self, t: float) -> Hardware:
        return Hardware()

    def scan_progress(self) -> tuple[int | None, str | None]:
        """(scan_id the rover reports, scan_state) - the echo that stops re-sends."""
        return self.last_scan_seq, self.scan_state

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
    """Laptop -> ESP32 commands, ESP32 -> laptop telemetry, JSON over UDP.

    Also accepts spec-v1 binary scan chunks (magic 0x53): decoded chunks land
    in `last_scan`, feed `scan_assembler`, and finished scans queue in
    `scan_results` (drained by `take_scans()`). Scan traffic never updates the
    Hardware telemetry state.
    """

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
        # Scan payload (v6 spec v1, scan_payload.py)
        self.scan_rx = 0
        self.scan_rx_errors = 0
        self.last_scan: ScanChunk | None = None
        self.scan_assembler = ScanAssembler()
        self.scan_results: list[ScanAssembly] = []

    def send(self, cmd: Cmd, t: float) -> None:
        self.seq += 1
        pkt = {
            "t": round(t, 3), "seq": self.seq,
            "v": round(cmd.v_mps, 3), "w": round(math.radians(cmd.w_deg_s), 3),
            "ttl_ms": self.ttl_ms,
        }
        if cmd.scan:
            pkt["scan"] = cmd.scan            # spec v1 §2.3: rides the normal command,
        try:                                  # refreshes the watchdog like any command
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
            # Spec-v1 binary scan chunk? Sniff before json.loads (v6 §2.5):
            # 0x53 is unambiguous because JSON datagrams start with '{' (0x7B).
            if is_scan_chunk(data):
                try:
                    chunk = decode_scan_chunk(data)
                    finished = self.scan_assembler.add(chunk, t)
                except ScanPayloadError:
                    self.scan_rx_errors += 1
                    continue
                self.scan_rx += 1
                self.last_scan = chunk
                if finished is not None:
                    self.scan_results.append(finished)
                continue
            try:
                pkt = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(pkt, dict) or "seq" not in pkt:
                continue
            self.telemetry_rx += 1
            self.last_rx_t = t
            if pkt.get("scan_seq") is not None:
                self.last_scan_seq = int(pkt["scan_seq"])
            if pkt.get("scan_state") is not None:
                self.scan_state = str(pkt["scan_state"])
            # Firmware contract: "watchdog": true means the watchdog has TRIPPED.
            self.last_hw = Hardware(
                dist_front_m=pkt.get("dist_front_m"),
                dist_rear_m=pkt.get("dist_rear_m"),
                batt_v=pkt.get("batt_v"),
                watchdog_ok=not bool(pkt.get("watchdog", True)),
                reflex=bool(pkt.get("reflex", False)),
                telemetry_age_s=0.0,
            )
        # §2.3 host policy: a scan with no new chunk for 0.5 s is closed as a
        # partial (never silently treated as complete).
        stale = self.scan_assembler.poll(t)
        if stale is not None:
            self.scan_results.append(stale)
        if self.last_rx_t is not None:
            self.last_hw.telemetry_age_s = round(t - self.last_rx_t, 2)
        return self.last_hw

    def take_scans(self) -> list[ScanAssembly]:
        """Finished scans (complete or timed-out partial); cleared on take."""
        out = self.scan_results
        self.scan_results = []
        return out

    def close(self) -> None:
        self.sock.close()

    def stats(self) -> dict:
        return {"link": "udp", "commands": self.seq, "telemetry_rx": self.telemetry_rx,
                "send_errors": self.send_errors,
                "scan_rx": self.scan_rx, "scan_rx_errors": self.scan_rx_errors,
                "last_scan_seq": self.last_scan_seq, "scan_state": self.scan_state}


class MockLink(RoverLink):
    """Simulated rover: obeys commands, reports plausible telemetry.

    It echoes ``scan_seq``/``scan_state`` for scan requests (so trigger/repeat
    logic can be exercised) but does NOT emit scan chunks - that is what
    ``tools/fake_rover.py`` is for.
    """

    def __init__(self, ttl_ms: int = 400):
        self.ttl_ms = ttl_ms
        self.seq = 0
        self.last_cmd_t: float | None = None
        self.last: Cmd | None = None
        self.scan_duration_s = 3.0
        self._scan_started_t: float | None = None

    def _active(self, t: float) -> bool:
        return self.last_cmd_t is not None and (t - self.last_cmd_t) * 1000.0 <= self.ttl_ms

    def send(self, cmd: Cmd, t: float) -> None:
        self.seq += 1
        self.last_cmd_t = t
        self.last = cmd
        if isinstance(cmd.scan, dict):
            self.last_scan_seq = int(cmd.scan.get("id", 0))
            if cmd.scan.get("action") == "stop":
                self.scan_state, self._scan_started_t = "idle", None
            else:
                self.scan_state, self._scan_started_t = "scanning", t

    def telemetry(self, t: float) -> Hardware:
        if self._scan_started_t is not None and t - self._scan_started_t >= self.scan_duration_s:
            self.scan_state, self._scan_started_t = "idle", None
        return Hardware(dist_front_m=3.0, dist_rear_m=3.0, batt_v=7.4,
                        watchdog_ok=self._active(t), reflex=False, telemetry_age_s=0.0)

    def stats(self) -> dict:
        return {"link": "mock", "commands": self.seq,
                "ttl_ok": self.last_cmd_t is not None,
                "last_scan_seq": self.last_scan_seq, "scan_state": self.scan_state}
