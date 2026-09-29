"""Scan-chunk codec over the real UDP path (v6 §5 items 1-2).

Item 1: encode -> socket -> decode round trip covering int8 negative angles,
range_mm=4000, status 0xFE, all flag combos, chunk tiling of a 181-sample scan,
and one telemetry() drain handling BOTH a JSON telemetry packet and a binary
chunk (each on its own code path, neither silently dropped).

Item 2: regression for the verified v6 §2.5 leak — the exact check_parser.py
bytes must not raise out of telemetry() (pre-fix: UnicodeDecodeError), must be
counted as rejected scan traffic, must not pollute Hardware, and a subsequent
JSON telemetry packet must still parse.
"""
import json
import socket
import time

import pytest

from link import UDPLink
from scan_payload import (
    CHUNK_HEADER_LEN,
    SCAN_CHUNK_MAX,
    ScanChunk,
    ScanPayloadError,
    ScanSample,
    chunk_plan,
    decode_scan_chunk,
    encode_scan_chunk,
    is_scan_chunk,
)

# --- exact bytes from the verified bad-behaviour repro (check_parser.py, v6 §2.5)
LEAK_REAL = bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40,
                   0xD3, 0x2C, 0x01, 0x00])
LEAK_ASCII = bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40,
                    0x00, 0x2C, 0x01, 0x01])

FLAG_COMBOS = [
    (False, False, False),
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, True),
]


def _udp_pair():
    esp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    esp.bind(("127.0.0.1", 0))
    esp.settimeout(2.0)
    link = UDPLink("127.0.0.1", esp.getsockname()[1], local_port=0)
    return esp, link


def _to_link(esp, link, payload: bytes) -> None:
    esp.sendto(payload, ("127.0.0.1", link.sock.getsockname()[1]))


def _sample(i: int, signal: bool, ambient: bool, t_us: bool) -> ScanSample:
    """Sample for global sweep index i (0..180): 1 deg steps, -90..+90."""
    return ScanSample(
        angle_dd=-128 if i == 3 else -90 + i,
        range_mm=4000 if i == 5 else 100 + i,
        status=0xFE if i == 7 else (0x00 if i % 2 == 0 else 0x01),
        signal_cmcps=(600 + i) if signal else None,
        ambient_cmcps=(40 + i) if ambient else None,
        t_us=(i * 33) if t_us else None,
    )


def test_scan_chunk_roundtrip_over_udp():
    esp, link = _udp_pair()
    try:
        for k, (sig, amb, tus) in enumerate(FLAG_COMBOS):
            samples = tuple(_sample(i, sig, amb, tus) for i in range(64))
            chunk = ScanChunk(scan_id=100 + k, chunk_idx=0, chunk_count=3,
                              first_idx=0, t0_ms=1234, period_us=33000,
                              samples=samples, partial=(k % 2 == 0))
            _to_link(esp, link, encode_scan_chunk(chunk))
            time.sleep(0.05)
            link.telemetry(1.0 + k)
            got = link.last_scan
            assert got is not None
            assert got.samples == samples                       # exact round trip
            assert got.has_signal is sig
            assert got.has_ambient is amb
            assert got.has_t_us is tus
            assert got.sample_size == 4 + 2 * sig + 2 * amb + 2 * tus
            assert got.datagram_size == CHUNK_HEADER_LEN + 64 * got.sample_size
            assert got.partial is (k % 2 == 0)
        # int8 negatives, the -128 no-sample sentinel, range_mm=4000, status 0xFE
        last = link.last_scan
        assert last is not None
        assert last.samples[0].angle_dd == -90
        assert last.samples[3].angle_dd == -128
        assert last.samples[5].range_mm == 4000
        assert last.samples[7].status == 0xFE
        assert last.has_timeouts is True
        assert link.scan_rx == len(FLAG_COMBOS)
        assert link.scan_rx_errors == 0
    finally:
        link.close()
        esp.close()


def test_scan_181_tiles_and_assembles_over_udp():
    esp, link = _udp_pair()
    try:
        plan = chunk_plan(181)
        assert plan == [(0, 64), (64, 64), (128, 53)]
        for k, (first, count) in enumerate(plan):
            samples = tuple(_sample(first + j, True, False, False) for j in range(count))
            chunk = ScanChunk(scan_id=9, chunk_idx=k, chunk_count=len(plan),
                              first_idx=first, t0_ms=first * 33, period_us=33000,
                              samples=samples)
            _to_link(esp, link, encode_scan_chunk(chunk))
        time.sleep(0.05)
        link.telemetry(3.0)
        scans = link.take_scans()
        assert len(scans) == 1
        asm = scans[0]
        assert asm.scan_id == 9 and asm.complete and asm.missing == []
        got = asm.samples()
        assert len(got) == 181
        assert got[0].angle_dd == -90 and got[-1].angle_dd == 90   # covers [0, 181)
        assert link.scan_rx == 3
    finally:
        link.close()
        esp.close()


def test_scan_chunk_and_json_in_one_drain():
    esp, link = _udp_pair()
    try:
        samples = tuple(_sample(i, False, False, False) for i in range(3))
        chunk = ScanChunk(scan_id=5, chunk_idx=0, chunk_count=1, first_idx=0,
                          t0_ms=0, period_us=0, samples=samples)
        _to_link(esp, link, encode_scan_chunk(chunk))
        _to_link(esp, link, json.dumps({
            "seq": 11, "t": 2.0, "dist_front_m": 0.7, "dist_rear_m": None,
            "batt_v": 7.2, "reflex": False, "watchdog": False, "uptime_s": 4.0,
        }).encode())
        time.sleep(0.05)
        hw = link.telemetry(2.0)                 # single drain, both packets
        assert hw.dist_front_m == 0.7 and hw.batt_v == 7.2
        assert hw.watchdog_ok is True
        assert link.scan_rx == 1 and link.telemetry_rx == 1
        assert link.last_scan is not None and link.last_scan.scan_id == 5
        assert link.stats()["scan_rx"] == 1
        scans = link.take_scans()                # chunk_count=1 => complete
        assert len(scans) == 1 and scans[0].complete and scans[0].scan_id == 5
    finally:
        link.close()
        esp.close()


def test_binary_packet_never_escapes_parser():
    """v6 §2.5 regression: the exact check_parser.py bytes."""
    esp, link = _udp_pair()
    try:
        _to_link(esp, link, LEAK_REAL)    # pre-fix: UnicodeDecodeError escaped
        _to_link(esp, link, LEAK_ASCII)   # pre-fix: silently dropped as bad JSON
        time.sleep(0.05)
        hw = link.telemetry(1.0)          # must NOT raise
        assert hw.dist_front_m is None    # no Hardware pollution from scan traffic
        assert link.telemetry_rx == 0
        assert link.scan_rx == 0
        assert link.scan_rx_errors == 2   # both are truncated v1 chunks: counted
        # a subsequent JSON telemetry packet still parses
        _to_link(esp, link, json.dumps({
            "seq": 12, "t": 1.0, "dist_front_m": 0.5, "batt_v": 7.4,
            "watchdog": False,
        }).encode())
        time.sleep(0.05)
        hw = link.telemetry(1.1)
        assert hw.dist_front_m == 0.5 and hw.watchdog_ok is True
        assert link.stats()["telemetry_rx"] == 1
    finally:
        link.close()
        esp.close()


def test_decode_rejects_malformed_chunks():
    good = encode_scan_chunk(ScanChunk(
        scan_id=1, chunk_idx=0, chunk_count=1, first_idx=0, t0_ms=0,
        period_us=0, samples=(ScanSample(-45, 4000, 0xFE),)))
    assert decode_scan_chunk(good).samples[0].angle_dd == -45
    assert is_scan_chunk(good) and not is_scan_chunk(b'{"seq": 1}')
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(good[:-1])                      # truncated vs header
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(good + b"\x00")                 # trailing bytes
    bad_ver = bytearray(good)
    bad_ver[1] = (bad_ver[1] & ~0b11) | 0b10
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(bytes(bad_ver))                 # version 2
    resv = bytearray(good)
    resv[1] |= 0x80
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(bytes(resv))                    # reserved bit7
    bad_angle = bytearray(good)
    bad_angle[16] = 0x64                                  # +100 deg
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(bytes(bad_angle))
    bad_range = bytearray(good)
    bad_range[17:19] = (5000).to_bytes(2, "little")
    with pytest.raises(ScanPayloadError):
        decode_scan_chunk(bytes(bad_range))
    with pytest.raises(ScanPayloadError):
        encode_scan_chunk(ScanChunk(                      # n_samples > 64
            scan_id=1, chunk_idx=0, chunk_count=2, first_idx=0, t0_ms=0,
            period_us=0, samples=tuple(ScanSample(0, 1, 0) for _ in range(SCAN_CHUNK_MAX + 1))))
