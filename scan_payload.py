"""Host-side codec + assembly for the binary scan-chunk protocol (wire spec v1).

Spec source: `jev-rover-research/runs/20260928-2220/v6-sweep-payload.md` §2
("Scan payload spec v1"). Pure functions and dataclasses, no sockets and no
scene imports: `link.py` sniffs the magic byte and routes binary datagrams
here, so this module can also be unit-tested standalone and reused by the M2
semantics/matcher layer.

Chunk header v1, 16 B (§2.2), all multi-byte fields little-endian:

    off 0    msg         u8  0x53 ('S'); JSON datagrams always start '{' 0x7B
    off 1    ver_flags   u8  bits0-1 version (=1); bit2 has_signal;
                             bit3 has_ambient; bit4 has_t_us;
                             bit5 partial (sweep aborted); bit6 timeouts
                             present; bit7 reserved (must be 0)
    off 2-3  scan_id     u16 LE   host-chosen, incremented per scan
    off 4    chunk_idx   u8  0-based
    off 5    chunk_count u8  total chunks of this scan (ceil(N/64))
    off 6    first_idx   u8  index of the first sample in this chunk
    off 7    n_samples   u8  1..SCAN_CHUNK_MAX (64)
    off 8-11 t0_ms       u32 LE  ms since scan start at first_idx (desmear anchor)
    off 12-15 period_us  u32 LE  nominal cadence achieved; 0 = unknown

Per-sample fields (§2.1), in table order; optional fields are present iff the
matching header flag bit is set, in the order signal, ambient, t_us:

    angle_dd      i8      -90..+90 deg relative (right +); -128 = no sample
    range_mm      u16 LE  0..4000 mm; 0 = no valid reading
    status        u8      VL53L1X RangeStatus 0..14, 0xFE timeout, 0xFF aborted
    signal_cmcps  u16 LE  optional (has_signal)
    ambient_cmcps u16 LE  optional (has_ambient)
    t_us          u16 LE  optional (has_t_us)

Decisions where §2 leaves freedom (documented, mirrored in the report):

* optional-flag encoding: one symbol name per flag; the encoder derives
  has_signal/has_ambient/has_t_us from the samples (a chunk must be uniform:
  either every sample carries an optional field or none does), and derives
  bit6 ("timeouts present") as `any(status == 0xFE)`.
* strict decode: wrong version, reserved bit7 set, bad chunk_idx/chunk_count,
  n_samples outside 1..64, any header/length mismatch, angle outside
  {-128} ∪ [-90, 90], or range_mm > 4000 raise `ScanPayloadError`. Nothing is
  silently accepted; the transport counts rejects instead of crashing.
* status is passed through as an opaque u8 (the semantic set is the RangeStatus
  table plus 0xFE/0xFF; filtering statuses 4/7/8/14 before matching is §2.1's
  contract for the matcher, not the codec's).

Chunk/datagram sizes at the recommended framing (chunk = 64 samples, §2.4):

    core (angle+range+status)          16 + 64*4 = 272 B   181 samples -> 740 B
    +signal                            16 + 64*6 = 400 B   181 samples -> 1102 B
    +ambient                           16 + 64*8 = 528 B   181 samples -> 1464 B
    +t_us                              16 + 64*10 = 656 B  181 samples -> 1826 B

The 512 B policy cap (§2.4) is met by the core and +signal variants at chunk
64; +ambient and +t_us meet it only with reduced chunks (62 / 49 samples),
which §2.4's "max samples per chunk for a 512 B datagram budget" table gives.
All variants are < the 1472 B IPv4 no-fragment limit.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterable

SCAN_MAGIC = 0x53            # 'S' — first binary message; §2.2 offset 0
SCAN_VERSION = 1             # ver_flags bits0-1; §2.2 offset 1
CHUNK_HEADER_LEN = 16        # §2.2
SCAN_CHUNK_MAX = 64          # RVR_SCAN_CHUNK_MAX; §2.4
NO_SAMPLE_ANGLE = -128       # §2.1
MAX_RANGE_MM = 4000          # §2.1 range_mm field range
STATUS_TIMEOUT = 0xFE        # §2.1
STATUS_ABORTED = 0xFF        # §2.1

FLAG_VERSION_MASK = 0b0000_0011
FLAG_HAS_SIGNAL = 0b0000_0100
FLAG_HAS_AMBIENT = 0b0000_1000
FLAG_HAS_T_US = 0b0001_0000
FLAG_PARTIAL = 0b0010_0000
FLAG_TIMEOUTS = 0b0100_0000
FLAG_RESERVED = 0b1000_0000

HEADER_STRUCT = struct.Struct("<BBHBBBBII")   # 16 B, §2.2
SAMPLE_CORE_STRUCT = struct.Struct("<bHB")    # angle, range_mm, status — 4 B
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")


class ScanPayloadError(ValueError):
    """Malformed or unsupported scan-chunk datagram (strict codec)."""


@dataclass(slots=True)
class ScanSample:
    angle_dd: int
    range_mm: int
    status: int
    signal_cmcps: int | None = None
    ambient_cmcps: int | None = None
    t_us: int | None = None


@dataclass(slots=True)
class ScanChunk:
    scan_id: int
    chunk_idx: int
    chunk_count: int
    first_idx: int
    t0_ms: int
    period_us: int
    samples: tuple[ScanSample, ...]
    partial: bool = False

    @property
    def n_samples(self) -> int:
        return len(self.samples)

    @property
    def has_signal(self) -> bool:
        return bool(self.samples) and self.samples[0].signal_cmcps is not None

    @property
    def has_ambient(self) -> bool:
        return bool(self.samples) and self.samples[0].ambient_cmcps is not None

    @property
    def has_t_us(self) -> bool:
        return bool(self.samples) and self.samples[0].t_us is not None

    @property
    def sample_size(self) -> int:
        return 4 + 2 * self.has_signal + 2 * self.has_ambient + 2 * self.has_t_us

    @property
    def datagram_size(self) -> int:
        return CHUNK_HEADER_LEN + self.n_samples * self.sample_size

    @property
    def has_timeouts(self) -> bool:
        return any(s.status == STATUS_TIMEOUT for s in self.samples)


@dataclass(slots=True)
class ScanAssembly:
    """One scan's reassembly state; `missing`/`complete` make losses visible."""
    scan_id: int
    chunk_count: int
    chunks: dict[int, ScanChunk] = field(default_factory=dict)
    t_last: float = 0.0

    @property
    def missing(self) -> list[int]:
        return [i for i in range(self.chunk_count) if i not in self.chunks]

    @property
    def complete(self) -> bool:
        return self.chunk_count > 0 and not self.missing

    @property
    def partial(self) -> bool:
        return any(c.partial for c in self.chunks.values())

    @property
    def quality(self) -> float:
        """Fraction of the announced chunks that actually arrived."""
        if self.chunk_count <= 0:
            return 0.0
        return (self.chunk_count - len(self.missing)) / self.chunk_count

    def samples(self) -> list[ScanSample]:
        out: list[ScanSample] = []
        for idx in sorted(self.chunks):
            out.extend(self.chunks[idx].samples)
        return out


class ScanAssembler:
    """Stateful reassembly of the current scan (§2.3 host policy, minimal).

    `add()` returns a finished `ScanAssembly` exactly when the scan completes
    or when a newer `scan_id` supersedes an unfinished one; `poll()` returns
    the in-flight scan as an unfinished partial once `timeout_s` elapses with
    no new chunk. Both paths report `complete=False` + `missing`, so a lost
    chunk can never be mistaken for a whole scan.
    """

    def __init__(self, timeout_s: float = 0.5):
        self.timeout_s = float(timeout_s)
        self._cur: ScanAssembly | None = None

    @property
    def in_flight(self) -> ScanAssembly | None:
        return self._cur

    def add(self, chunk: ScanChunk, now: float) -> ScanAssembly | None:
        if self._cur is not None and chunk.scan_id != self._cur.scan_id:
            out, self._cur = self._cur, None
        else:
            out = None
        if self._cur is None:
            self._cur = ScanAssembly(scan_id=chunk.scan_id, chunk_count=chunk.chunk_count)
        elif chunk.chunk_count != self._cur.chunk_count:
            raise ScanPayloadError(
                f"chunk_count inconsistent within scan_id {chunk.scan_id}: "
                f"{chunk.chunk_count} != {self._cur.chunk_count}")
        self._cur.chunks[chunk.chunk_idx] = chunk
        self._cur.t_last = float(now)
        if self._cur.complete:
            out, self._cur = self._cur, None
        return out

    def poll(self, now: float) -> ScanAssembly | None:
        if self._cur is not None and (now - self._cur.t_last) > self.timeout_s:
            out, self._cur = self._cur, None
            return out
        return None


def chunk_plan(n_samples: int, chunk_max: int = SCAN_CHUNK_MAX) -> list[tuple[int, int]]:
    """Chunk a scan of `n_samples` into (first_idx, n) pairs, §2.3/§2.4.

    Default framing (`chunk_max=64`) yields exactly ceil(N/64) chunks. A
    smaller `chunk_max` (e.g. 62 for +ambient, 49 for +t_us) is what §2.4
    requires when the 512 B datagram policy must be kept.
    """
    if not (1 <= n_samples <= 255):
        raise ScanPayloadError(f"n_samples out of range: {n_samples!r}")
    if not (1 <= chunk_max <= 255):
        raise ScanPayloadError(f"chunk_max out of range: {chunk_max!r}")
    plan: list[tuple[int, int]] = []
    first = 0
    while first < n_samples:
        count = min(chunk_max, n_samples - first)
        plan.append((first, count))
        first += count
    return plan


def _check_u8(value: int | None, name: str) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ScanPayloadError(f"{name} out of u8 range: {value!r}")


def _check_u16(value: int | None, name: str) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise ScanPayloadError(f"{name} out of u16 range: {value!r}")


def _check_u32(value: int, name: str) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ScanPayloadError(f"{name} out of u32 range: {value!r}")


def _check_angle(angle: int) -> None:
    if angle != NO_SAMPLE_ANGLE and not -90 <= angle <= 90:
        raise ScanPayloadError(f"angle out of spec (-90..90 or -128): {angle!r}")


def _check_status(status: int) -> None:
    _check_u8(status, "status")


def encode_scan_chunk(chunk: ScanChunk) -> bytes:
    """Serialize one chunk to spec-v1 wire bytes (strict, §2.2/§2.4)."""
    _check_u16(chunk.scan_id, "scan_id")
    _check_u8(chunk.chunk_idx, "chunk_idx")
    _check_u8(chunk.chunk_count, "chunk_count")
    if not 1 <= chunk.chunk_count <= 0xFF:
        raise ScanPayloadError(f"chunk_count must be >= 1: {chunk.chunk_count!r}")
    if not 0 <= chunk.chunk_idx < chunk.chunk_count:
        raise ScanPayloadError(
            f"chunk_idx {chunk.chunk_idx} not < chunk_count {chunk.chunk_count}")
    _check_u8(chunk.first_idx, "first_idx")
    _check_u32(chunk.t0_ms, "t0_ms")
    _check_u32(chunk.period_us, "period_us")
    n = chunk.n_samples
    if not 1 <= n <= SCAN_CHUNK_MAX:
        raise ScanPayloadError(f"n_samples must be 1..{SCAN_CHUNK_MAX}: {n!r}")

    has_signal = all(s.signal_cmcps is not None for s in chunk.samples)
    has_ambient = all(s.ambient_cmcps is not None for s in chunk.samples)
    has_t_us = all(s.t_us is not None for s in chunk.samples)
    if any(s.signal_cmcps is not None for s in chunk.samples) != has_signal:
        raise ScanPayloadError("optional field signal_cmcps must be present on every sample or none")
    if any(s.ambient_cmcps is not None for s in chunk.samples) != has_ambient:
        raise ScanPayloadError("optional field ambient_cmcps must be present on every sample or none")
    if any(s.t_us is not None for s in chunk.samples) != has_t_us:
        raise ScanPayloadError("optional field t_us must be present on every sample or none")

    flags = SCAN_VERSION | (FLAG_PARTIAL if chunk.partial else 0)
    flags |= FLAG_HAS_SIGNAL if has_signal else 0
    flags |= FLAG_HAS_AMBIENT if has_ambient else 0
    flags |= FLAG_HAS_T_US if has_t_us else 0
    if chunk.has_timeouts:
        flags |= FLAG_TIMEOUTS

    out = bytearray(HEADER_STRUCT.pack(
        SCAN_MAGIC, flags, chunk.scan_id, chunk.chunk_idx, chunk.chunk_count,
        chunk.first_idx, n, chunk.t0_ms, chunk.period_us))
    for s in chunk.samples:
        _check_angle(s.angle_dd)
        if not 0 <= s.range_mm <= MAX_RANGE_MM:
            raise ScanPayloadError(f"range_mm out of spec 0..{MAX_RANGE_MM}: {s.range_mm!r}")
        _check_status(s.status)
        out += SAMPLE_CORE_STRUCT.pack(s.angle_dd, s.range_mm, s.status)
        if has_signal:
            _check_u16(s.signal_cmcps, "signal_cmcps")
            out += _U16.pack(s.signal_cmcps)
        if has_ambient:
            _check_u16(s.ambient_cmcps, "ambient_cmcps")
            out += _U16.pack(s.ambient_cmcps)
        if has_t_us:
            _check_u16(s.t_us, "t_us")
            out += _U16.pack(s.t_us)
    return bytes(out)


def is_scan_chunk(data: bytes | bytearray | memoryview) -> bool:
    """Cheap sniff: spec-v1 binary chunks start with 0x53, JSON with '{' (§2.5)."""
    return len(data) >= 1 and data[0] == SCAN_MAGIC


def decode_scan_chunk(data: bytes | bytearray | memoryview) -> ScanChunk:
    """Parse one spec-v1 chunk; raise ScanPayloadError on any violation."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"expected bytes-like, got {type(data).__name__}")
    data = bytes(data)
    if len(data) < CHUNK_HEADER_LEN:
        raise ScanPayloadError(
            f"short packet: {len(data)} B < {CHUNK_HEADER_LEN} B header")
    if data[0] != SCAN_MAGIC:
        raise ScanPayloadError(f"bad magic: 0x{data[0]:02x} != 0x{SCAN_MAGIC:02x}")
    flags = data[1]
    if flags & FLAG_VERSION_MASK != SCAN_VERSION:
        raise ScanPayloadError(f"unsupported version {flags & FLAG_VERSION_MASK}")
    if flags & FLAG_RESERVED:
        raise ScanPayloadError("reserved flag bit7 set")
    (_, _, scan_id, chunk_idx, chunk_count, first_idx, n,
     t0_ms, period_us) = HEADER_STRUCT.unpack_from(data, 0)
    if chunk_count == 0:
        raise ScanPayloadError("chunk_count == 0")
    if chunk_idx >= chunk_count:
        raise ScanPayloadError(f"chunk_idx {chunk_idx} >= chunk_count {chunk_count}")
    if not 1 <= n <= SCAN_CHUNK_MAX:
        raise ScanPayloadError(f"n_samples outside 1..{SCAN_CHUNK_MAX}: {n}")

    size = 4 + 2 * bool(flags & FLAG_HAS_SIGNAL) + 2 * bool(flags & FLAG_HAS_AMBIENT) \
        + 2 * bool(flags & FLAG_HAS_T_US)
    want = CHUNK_HEADER_LEN + n * size
    if len(data) != want:
        raise ScanPayloadError(
            f"length mismatch: header says {n} samples x {size} B = {want} B, got {len(data)} B")

    samples: list[ScanSample] = []
    off = CHUNK_HEADER_LEN
    for _ in range(n):
        angle, rng, status = SAMPLE_CORE_STRUCT.unpack_from(data, off)
        off += 4
        sig = amb = tus = None
        if flags & FLAG_HAS_SIGNAL:
            sig = _U16.unpack_from(data, off)[0]
            off += 2
        if flags & FLAG_HAS_AMBIENT:
            amb = _U16.unpack_from(data, off)[0]
            off += 2
        if flags & FLAG_HAS_T_US:
            tus = _U16.unpack_from(data, off)[0]
            off += 2
        _check_angle(angle)
        if rng > MAX_RANGE_MM:
            raise ScanPayloadError(f"range_mm out of spec 0..{MAX_RANGE_MM}: {rng}")
        samples.append(ScanSample(angle_dd=angle, range_mm=rng, status=status,
                                  signal_cmcps=sig, ambient_cmcps=amb, t_us=tus))
    return ScanChunk(scan_id=scan_id, chunk_idx=chunk_idx, chunk_count=chunk_count,
                     first_idx=first_idx, t0_ms=t0_ms, period_us=period_us,
                     samples=tuple(samples), partial=bool(flags & FLAG_PARTIAL))


def assemble_scan(chunks: Iterable[ScanChunk]) -> ScanAssembly:
    """Assemble one scan's chunks; missing chunks stay visible in `missing`."""
    it = iter(chunks)
    try:
        first = next(it)
    except StopIteration:
        raise ScanPayloadError("no chunks to assemble") from None
    asm = ScanAssembly(scan_id=first.scan_id, chunk_count=first.chunk_count)
    for c in (first, *it):
        if c.scan_id != asm.scan_id:
            raise ScanPayloadError(
                f"mixed scan_ids in assembly: {c.scan_id} != {asm.scan_id}")
        asm.chunks[c.chunk_idx] = c
    return asm
