"""Transport: watchdog semantics, and a UDP round trip against a fake ESP32."""
import json
import math
import socket
import time

from link import Cmd, MockLink, UDPLink


def test_mock_link_watchdog_expires():
    link = MockLink(ttl_ms=200)
    link.send(Cmd(0.3, 0.0), t=0.0)
    assert link.telemetry(0.1).watchdog_ok is True
    assert link.telemetry(0.5).watchdog_ok is False


def test_udp_protocol_round_trip():
    esp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    esp.bind(("127.0.0.1", 0))
    esp.settimeout(2.0)
    esp_port = esp.getsockname()[1]

    link = UDPLink("127.0.0.1", esp_port, local_port=0)
    try:
        link.send(Cmd(0.4, 10.0), t=1.0)
        data, addr = esp.recvfrom(4096)
        pkt = json.loads(data)
        assert pkt["v"] == 0.4
        assert pkt["w"] == round(math.radians(10.0), 3)
        assert pkt["ttl_ms"] == 400
        assert pkt["seq"] == 1

        esp.sendto(json.dumps({
            "seq": 7, "t": 1.0, "dist_front_m": 0.5, "dist_rear_m": None,
            "enc_l": None, "enc_r": None, "v_meas": 0.3, "w_meas": 0.0,
            "batt_v": 7.4, "reflex": False, "watchdog": False, "uptime_s": 3.0,
        }).encode(), addr)
        time.sleep(0.05)
        hw = link.telemetry(1.1)
        assert hw.dist_front_m == 0.5
        assert hw.batt_v == 7.4
        assert hw.watchdog_ok is True
        assert hw.telemetry_age_s is not None

        esp.sendto(json.dumps({"seq": 8, "watchdog": True}).encode(), addr)
        time.sleep(0.05)
        hw = link.telemetry(1.2)
        assert hw.watchdog_ok is False, "tripped watchdog must map to watchdog_ok=False"
        assert link.stats()["telemetry_rx"] == 2
    finally:
        link.close()
        esp.close()
