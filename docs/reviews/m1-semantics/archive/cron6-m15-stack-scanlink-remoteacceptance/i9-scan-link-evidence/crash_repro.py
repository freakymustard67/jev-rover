#!/usr/bin/env python3
"""Binary scan chunk -> UDPLink.telemetry() crash repro (v6 §2.5).

Sends the exact check_parser.py v1 binary chunk bytes to a UDPLink-bound UDP
socket and calls telemetry() once. Pre-fix: UnicodeDecodeError escapes
telemetry() (run.py loop would die). Post-fix: no exception; chunk routed to
the scan decoder (counted, not faked into Hardware); a later JSON telemetry
packet still parses.

Usage: crash_repro.py <repo_dir>
Exit 0 = no exception escaped (post-fix expectation), 1 = exception escaped.
"""
import socket
import sys
import time
import traceback

repo = sys.argv[1]
sys.path.insert(0, repo)

from link import UDPLink  # noqa: E402

# exact bytes from refs check_parser.py ("real chunk": angle=-45 -> 0xD3)
BINARY_CHUNK = bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40,
                      0xD3, 0x2C, 0x01, 0x00])

esp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
esp.bind(("127.0.0.1", 0))
esp.settimeout(2.0)
link = UDPLink("127.0.0.1", esp.getsockname()[1], local_port=0)
link_port = link.sock.getsockname()[1]

try:
    # 1) the crash packet
    esp.sendto(BINARY_CHUNK, ("127.0.0.1", link_port))
    time.sleep(0.05)
    try:
        hw = link.telemetry(1.0)
        print("telemetry() returned without raising: %r" % (hw,))
        escaped = False
    except Exception:  # noqa: BLE001 - we want the exact type for evidence
        print("EXCEPTION ESCAPED telemetry():")
        traceback.print_exc()
        escaped = True

    # 2) a normal JSON telemetry packet afterwards must still parse
    import json
    esp.sendto(json.dumps({
        "seq": 9, "t": 1.0, "dist_front_m": 0.5, "dist_rear_m": None,
        "batt_v": 7.4, "reflex": False, "watchdog": False, "uptime_s": 3.0,
    }).encode(), ("127.0.0.1", link_port))
    time.sleep(0.05)
    try:
        hw = link.telemetry(1.1)
        print("follow-up JSON parsed: dist_front_m=%r batt_v=%r watchdog_ok=%r"
              % (hw.dist_front_m, hw.batt_v, hw.watchdog_ok))
        print("stats: %r" % (link.stats(),))
    except Exception:
        print("EXCEPTION ESCAPED on follow-up JSON:")
        traceback.print_exc()
        escaped = True
finally:
    link.close()
    esp.close()

print("VERDICT: %s" % ("crash escaped (RED)" if escaped else "no exception escaped (GREEN)"))
sys.exit(1 if escaped else 0)
