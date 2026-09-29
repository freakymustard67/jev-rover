"""v6 §5 item 3: chunk-count math, datagram size caps, missing-chunk visibility.

Framing rules pinned to spec v1 (v6-sweep-payload.md §2):

* chunk <= 64 samples; default framing gives chunk_count == ceil(N/64) (§2.3).
* every datagram beats the 1472 B IPv4 no-fragment limit (§2.4).
* the 512 B policy cap is met at chunk 64 by the core and +signal variants
  (272 / 400 B) and by +ambient / +t_us only with reduced chunks — 62 / 49
  samples, exactly §2.4's "max samples per chunk for a 512 B datagram budget";
  at chunk 64 those variants are 528 / 656 B, the spec's own
  "(<512 False, <1472 True)" cells.
* a scan with a missing chunk is reported incomplete/partial (`missing`,
  `quality`, or a mixed-scan_id error) — never silently accepted as a scan.
"""
import math
from dataclasses import replace

import pytest

from scan_payload import (
    CHUNK_HEADER_LEN,
    SCAN_CHUNK_MAX,
    ScanAssembler,
    ScanChunk,
    ScanPayloadError,
    ScanSample,
    assemble_scan,
    chunk_plan,
    decode_scan_chunk,
    encode_scan_chunk,
)

N_LIST = (37, 91, 181)
FLAG_COMBOS = [
    (False, False, False), (True, False, False), (False, True, False),
    (False, False, True), (True, True, False), (True, False, True),
    (False, True, True), (True, True, True),
]


def _sample(i, sig, amb, tus):
    return ScanSample(angle_dd=-90 + i, range_mm=1000 + i, status=0,
                      signal_cmcps=(500 + i) if sig else None,
                      ambient_cmcps=(30 + i) if amb else None,
                      t_us=(i * 33) if tus else None)


def _chunks(n, flags, chunk_max=SCAN_CHUNK_MAX):
    sig, amb, tus = flags
    plan = chunk_plan(n, chunk_max)
    return [
        ScanChunk(scan_id=42, chunk_idx=k, chunk_count=len(plan), first_idx=first,
                  t0_ms=first * 33, period_us=33000,
                  samples=tuple(_sample(first + j, sig, amb, tus) for j in range(count)))
        for k, (first, count) in enumerate(plan)
    ]


@pytest.mark.parametrize("n", N_LIST)
def test_chunk_count_is_ceil_n_over_64(n):
    plan = chunk_plan(n)
    assert len(plan) == math.ceil(n / SCAN_CHUNK_MAX)
    # (first_idx, n_samples) tile [0, N) exactly once, contiguously
    covered = []
    first = 0
    for f, c in plan:
        assert f == first and 1 <= c <= SCAN_CHUNK_MAX
        covered.extend(range(f, f + c))
        first += c
    assert covered == list(range(n))


@pytest.mark.parametrize("flags", FLAG_COMBOS)
@pytest.mark.parametrize("n", N_LIST)
def test_every_datagram_within_limits(n, flags):
    sig, amb, tus = flags
    size = 4 + 2 * sig + 2 * amb + 2 * tus
    for chunk in _chunks(n, flags):
        data = encode_scan_chunk(chunk)
        assert decode_scan_chunk(data).samples == chunk.samples
        assert len(data) == CHUNK_HEADER_LEN + chunk.n_samples * size
        assert len(data) <= 1472, "must beat the IPv4 no-fragment limit (§2.4)"
        if not amb and not tus:
            assert len(data) <= 512, "core/+signal meet the 512 B policy (§2.4)"


@pytest.mark.parametrize("flags", [(True, True, False), (True, True, True),
                                   (False, True, False), (False, False, True)])
def test_reduced_chunks_keep_ambient_and_t_us_within_512(flags):
    sig, amb, tus = flags
    size = 4 + 2 * sig + 2 * amb + 2 * tus
    safe_max = min(SCAN_CHUNK_MAX, (512 - CHUNK_HEADER_LEN) // size)  # 62 / 49 per §2.4
    for n in N_LIST:
        for chunk in _chunks(n, flags, chunk_max=safe_max):
            data = encode_scan_chunk(chunk)
            assert len(data) <= 512
            assert decode_scan_chunk(data).samples == chunk.samples


def test_chunk_64_overage_for_ambient_and_t_us_is_the_spec_numbers():
    """§2.4: D +ambient 528 B / E +t_us 656 B at chunk 64 "(<512 False, <1472 True)"."""
    for flags, big in [((True, True, False), 16 + 64 * 8), ((True, True, True), 16 + 64 * 10)]:
        sizes = [len(encode_scan_chunk(c)) for c in _chunks(181, flags)]
        assert big in sizes and big > 512 and big <= 1472


def test_missing_chunk_is_never_silently_accepted():
    chunks = _chunks(181, (False, False, False))
    assert len(chunks) == 3
    partial = assemble_scan([chunks[0], chunks[2]])          # chunk 1 lost
    assert partial.complete is False
    assert partial.missing == [1]
    assert partial.quality == pytest.approx(2 / 3)
    full = assemble_scan(chunks)
    assert full.complete is True and full.missing == [] and full.quality == 1.0
    assert len(full.samples()) == 181


def test_assembler_reports_timeout_and_supersede_as_partial():
    chunks = _chunks(181, (False, False, False))
    a = ScanAssembler(timeout_s=0.5)
    assert a.add(chunks[0], 0.0) is None
    assert a.in_flight is not None and not a.in_flight.complete
    assert a.poll(0.4) is None                     # within timeout: still in flight
    out = a.poll(0.6)                              # 0.6 s > 0.5 s: closed as partial
    assert out is not None and out.complete is False and out.missing == [1, 2]
    assert a.poll(0.7) is None                     # reported exactly once
    # a newer scan_id supersedes an unfinished scan: reported, not swallowed
    b = ScanAssembler()
    assert b.add(chunks[0], 0.0) is None
    out = b.add(replace(chunks[0], scan_id=43), 0.1)
    assert out is not None and out.scan_id == 42 and out.complete is False
    # a full set completes and is returned by the completing add()
    c = ScanAssembler()
    results = [c.add(ch, float(k)) for k, ch in enumerate(chunks)]
    assert results[0] is None and results[1] is None
    assert results[2] is not None and results[2].complete is True


def test_inconsistent_and_mixed_scan_ids_raise_not_assemble():
    chunks = _chunks(91, (False, False, False))
    with pytest.raises(ScanPayloadError):
        assemble_scan([chunks[0], replace(chunks[1], scan_id=43)])
    d = ScanAssembler()
    assert d.add(chunks[0], 0.0) is None
    with pytest.raises(ScanPayloadError):          # same scan_id, chunk_count lies
        d.add(replace(chunks[1], chunk_count=5), 0.1)
