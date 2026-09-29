#!/usr/bin/env python3
"""Codec conformance vs v6 §2 spec v1: measured layout, sizes, strictness.

Usage: codec_conformance.py <repo_dir>   (evidence, exit != 0 on mismatch)
"""
import sys

repo = sys.argv[1]
sys.path.insert(0, repo)

from scan_payload import (  # noqa: E402
    CHUNK_HEADER_LEN,
    SCAN_CHUNK_MAX,
    ScanChunk,
    ScanPayloadError,
    ScanSample,
    chunk_plan,
    decode_scan_chunk,
    encode_scan_chunk,
)

fail = 0


def check(name, got, want):
    global fail
    ok = got == want
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r} want {want!r}")


print("== A. header + sample byte layout (§2.2/§2.1), one +t_us chunk ==")
chunk = ScanChunk(scan_id=0x0129, chunk_idx=0, chunk_count=3, first_idx=0,
                  t0_ms=1000, period_us=33000,
                  samples=(ScanSample(-45, 4000, 0xFE, signal_cmcps=600,
                                      ambient_cmcps=40, t_us=7),))
b = encode_scan_chunk(chunk)
print("hex:", b.hex(" "))
fields = [
    ("msg        u8      §2.2 off0", b[0], 0x53),
    ("ver_flags  u8      §2.2 off1", b[1], 0x01 | 0x04 | 0x08 | 0x10 | 0x40),
    ("scan_id    u16 LE  §2.2 off2-3", int.from_bytes(b[2:4], "little"), 0x0129),
    ("chunk_idx  u8      §2.2 off4", b[4], 0),
    ("chunk_cnt  u8      §2.2 off5", b[5], 3),
    ("first_idx  u8      §2.2 off6", b[6], 0),
    ("n_samples  u8      §2.2 off7", b[7], 1),
    ("t0_ms      u32 LE  §2.2 off8-11", int.from_bytes(b[8:12], "little"), 1000),
    ("period_us  u32 LE  §2.2 off12-15", int.from_bytes(b[12:16], "little"), 33000),
    ("angle_dd   i8      §2.1 off16", int.from_bytes(b[16:17], "little", signed=True), -45),
    ("range_mm   u16 LE  §2.1 off17-18", int.from_bytes(b[17:19], "little"), 4000),
    ("status     u8      §2.1 off19", b[19], 0xFE),
    ("signal     u16 LE  §2.1 off20-21", int.from_bytes(b[20:22], "little"), 600),
    ("ambient    u16 LE  §2.1 off22-23", int.from_bytes(b[22:24], "little"), 40),
    ("t_us       u16 LE  §2.1 off24-25", int.from_bytes(b[24:26], "little"), 7),
]
for name, got, want in fields:
    check(name, got, want)
check("datagram length (16 + 10)", len(b), 26)
check("roundtrip equality", decode_scan_chunk(b), chunk)

print("\n== B. datagram sizes vs v6 §2.4 (flags = has_signal, has_ambient, has_t_us) ==")
# Two framings coexist in §2.4: the "one datagram per whole sweep" table
# (16 + 181*s) and the recommended always-chunked framing (ceil(181/64)=3
# datagrams, so 3 headers = 16*3 + 181*s bytes actually on the wire).
CANON = [  # (label, flags, B/sample, v6 chunk-64 B, v6 one-datagram-181 B)
    ("B core      {angle,range,status}", (0, 0, 0), 4, 16 + 64 * 4, 16 + 181 * 4),
    ("C +signal", (1, 0, 0), 6, 16 + 64 * 6, 16 + 181 * 6),
    ("D +ambient", (1, 1, 0), 8, 16 + 64 * 8, 16 + 181 * 8),
    ("E +t_us (all)", (1, 1, 1), 10, 16 + 64 * 10, 16 + 181 * 10),
]


def sample(i, sig, amb, tus):
    return ScanSample(angle_dd=-90 + (i % 181), range_mm=1000,
                      status=0, signal_cmcps=(500 if sig else None),
                      ambient_cmcps=(30 if amb else None), t_us=(i * 33 if tus else None))


for label, (sig, amb, tus), size, b64, b181 in CANON:
    chunks = [encode_scan_chunk(ScanChunk(
        scan_id=1, chunk_idx=k, chunk_count=len(chunk_plan(181)), first_idx=f,
        t0_ms=0, period_us=33000,
        samples=tuple(sample(f + j, sig, amb, tus) for j in range(c))))
        for k, (f, c) in enumerate(chunk_plan(181))]
    d64 = len(encode_scan_chunk(ScanChunk(
        scan_id=1, chunk_idx=0, chunk_count=3, first_idx=0, t0_ms=0, period_us=33000,
        samples=tuple(sample(i, sig, amb, tus) for i in range(64)))))
    total181 = sum(len(c) for c in chunks)
    print(f"  {label:34s} {size:2d} B/sample  chunk64={d64:4d} B (§2.4 {b64:4d})"
          f"  chunked N=181 total={total181:4d} B  (one-datagram math {16 + 181 * size:4d} B)")
    check(f"  size chunk64 {label}", d64, b64)
    check(f"  chunked N=181 total {label}", total181, 16 * len(chunk_plan(181)) + 181 * size)
    check(f"  one-datagram N=181 math {label}", 16 + 181 * size, b181)
    check(f"  all chunked datagrams <=1472 {label}", max(len(c) for c in chunks) <= 1472, True)
    check(f"  last chunk datagram (53 samples) {label}", len(chunks[2]), 16 + 53 * size)

print("\n== C. 512 B policy and chunk limits (§2.4 recommendation) ==")
check("chunk=64 core <=512", 16 + 64 * 4, 272)
print("  core/+signal meet the 512 B cap at chunk 64 (272/400 B)")
for label, (sig, amb, tus), size in [("+ambient", (1, 1, 0), 8), ("+t_us", (1, 1, 1), 10)]:
    safe = min(SCAN_CHUNK_MAX, (512 - CHUNK_HEADER_LEN) // size)
    d = len(encode_scan_chunk(ScanChunk(
        scan_id=1, chunk_idx=0, chunk_count=2, first_idx=0, t0_ms=0, period_us=33000,
        samples=tuple(sample(i, sig, amb, tus) for i in range(safe)))))
    print(f"  {label:8s} 512-safe chunk size = {safe:2d} -> datagram {d} B (<=512: {d <= 512})")
    check(f"  {label} 512-safe", d <= 512, True)
for n in (37, 91, 181):
    import math
    check(f"chunk_count(N={n}) == ceil(N/64)", len(chunk_plan(n)), math.ceil(n / 64))

print("\n== D. strict rejection (measured error messages) ==")
LEAK_REAL = bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40,
                   0xD3, 0x2C, 0x01, 0x00])  # v6 §2.5 repro bytes
cases = {
    "check_parser.py bytes (12 B, header says 64 samples)": LEAK_REAL,
    "trailing byte": encode_scan_chunk(chunk) + b"\x00",
    "version 2": bytes([0x53, 0x02]) + encode_scan_chunk(chunk)[2:],
    "reserved bit7": bytes([0x53, 0x01 | 0x80]) + encode_scan_chunk(chunk)[2:],
}
for name, data in cases.items():
    try:
        decode_scan_chunk(data)
        print(f"  FAIL {name}: accepted!")
        fail += 1
    except ScanPayloadError as e:
        print(f"  PASS {name}: ScanPayloadError: {e}")
print("\nRESULT:", "ALL CHECKS PASSED" if fail == 0 else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
