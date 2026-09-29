"""Byte-level conformance between the firmware encoder and the host codec.

The firmware cannot be compiled on this machine, so this pins the contract by
hand-building chunks EXACTLY as ``rvrScanFlushChunk()`` writes them
(``firmware/rover_esp32/rover_esp32.ino``; default options signal+ambient on,
t_us off -> 8 B/sample; chunk max = min(64, (512-16)/8) = 62) and decoding with
``scan_payload``. Any drift in field order, endianness, flags or tiling fails
here - it is the closest thing to a firmware/host integration test we can run
before the servo and L1X are on the bench.
"""
from scan_payload import (SCAN_MAGIC, ScanAssembly, chunk_plan,  # noqa: I001
                          decode_scan_chunk)
from sweep import beams_from_assembly

FW_OPT_SIGNAL = True
FW_OPT_AMBIENT = True
FW_OPT_T_US = False
FW_SAMPLE_BYTES = 4 + 2 * FW_OPT_SIGNAL + 2 * FW_OPT_AMBIENT + 2 * FW_OPT_T_US   # 8
FW_DATAGRAM_CAP = 512
FW_CHUNK_MAX = min(64, (FW_DATAGRAM_CAP - 16) // FW_SAMPLE_BYTES)              # 62
FW_VERSION = 1
FW_FLAG_HAS_SIGNAL = 0x04
FW_FLAG_HAS_AMBIENT = 0x08
FW_FLAG_PARTIAL = 0x20
FW_FLAG_TIMEOUTS = 0x40


def firmware_chunk(scan_id: int, chunk_idx: int, chunk_count: int, first_idx: int,
                   samples: list[tuple[int, int, int, int, int]], *, t0_ms: int = 0,
                   period_us: int = 0, partial: bool = False,
                   timeouts: bool = False) -> bytes:
    """rvrScanFlushChunk(), transcribed line by line."""
    flags = (FW_VERSION
             | (FW_FLAG_HAS_SIGNAL if FW_OPT_SIGNAL else 0)
             | (FW_FLAG_HAS_AMBIENT if FW_OPT_AMBIENT else 0)
             | (FW_FLAG_PARTIAL if partial else 0)
             | (FW_FLAG_TIMEOUTS if timeouts else 0))
    out = bytearray([SCAN_MAGIC, flags])
    out += scan_id.to_bytes(2, "little")
    out += bytes([chunk_idx, chunk_count, first_idx, len(samples)])
    out += t0_ms.to_bytes(4, "little") + period_us.to_bytes(4, "little")
    for angle, mm, status, signal, ambient in samples:
        out += bytes([angle & 0xFF])
        out += mm.to_bytes(2, "little") + bytes([status])
        out += signal.to_bytes(2, "little") + ambient.to_bytes(2, "little")
    return bytes(out)


def test_firmware_chunk_layout_decodes():
    samples = [(a, 1000 + a, 0, 600, 40) for a in range(-90, -90 + FW_CHUNK_MAX)]
    data = firmware_chunk(297, 0, 3, 0, samples, period_us=33000)
    assert len(data) == 16 + FW_CHUNK_MAX * FW_SAMPLE_BYTES == FW_DATAGRAM_CAP

    chunk = decode_scan_chunk(data)
    assert chunk.scan_id == 297 and chunk.chunk_idx == 0 and chunk.chunk_count == 3
    assert chunk.first_idx == 0 and chunk.n_samples == FW_CHUNK_MAX
    assert chunk.period_us == 33000 and chunk.t0_ms == 0
    first = chunk.samples[0]
    assert first.angle_dd == -90
    assert first.range_mm == 1000 - 90
    assert first.signal_cmcps == 600 and first.ambient_cmcps == 40
    assert first.t_us is None, "t_us is off in the default firmware profile"
    assert chunk.datagram_size == FW_DATAGRAM_CAP
    assert chunk.has_signal and chunk.has_ambient and not chunk.has_t_us


def test_firmware_tiling_matches_the_host_plan():
    # firmware: chunkCount = ceil(n / FW_CHUNK_MAX); host chunk_plan must agree
    for n, expected_chunks in ((31, 1), (91, 2), (181, 3)):
        fw_count = (n + FW_CHUNK_MAX - 1) // FW_CHUNK_MAX
        assert fw_count == expected_chunks
        assert len(chunk_plan(n, FW_CHUNK_MAX)) == fw_count
    # 181 beams at 1 degree: 62/62/57 with u8-safe first_idx 0/62/124
    assert chunk_plan(181, FW_CHUNK_MAX) == [(0, 62), (62, 62), (124, 57)]


def test_firmware_partial_and_timeout_flags():
    samples = [(0, 0, 0xFE, 0, 0), (2, 1234, 0, 600, 40)]
    chunk = decode_scan_chunk(firmware_chunk(5, 0, 2, 0, samples,
                                             partial=True, timeouts=True))
    assert chunk.partial is True and chunk.has_timeouts is True
    assert chunk.samples[0].status == 0xFE

    beams = beams_from_assembly(ScanAssembly(scan_id=5, chunk_count=2,
                                             chunks={0: chunk}))
    assert beams.n_total == 2 and beams.n_valid == 1, "timeout samples are dropped"


def test_negative_angles_survive_the_i8_round_trip():
    samples = [(-90, 2500, 0, 300, 20), (90, 2500, 0, 300, 20)]
    chunk = decode_scan_chunk(firmware_chunk(1, 0, 1, 0, samples))
    assert [s.angle_dd for s in chunk.samples] == [-90, 90]
    assert chunk.n_samples == 2
