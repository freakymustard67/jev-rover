# i9 — binary scan-datagram crash fix + host scan-chunk codec (wire spec v1)

Run: 20260929-0248 · i9 subagent · 2026-09-29
Inputs: `/home/freakymustard/jev-rover-research/runs/20260928-2220/v6-sweep-payload.md`
(§2 spec v1, §5 items 1–3) · PR tip `bec1d91fe17682ce38d24e334c94c67fa4bcc685`
verified by `git rev-parse HEAD` in the scratch clone
`/home/freakymustard/.hermes/cache/scratch/c6/i9-repo`.
Read-only on `/home/freakymustard/jev-rover` and `/tmp/opencode` (nothing written,
no git writes there). Python used for everything:
`/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16).

Deliverable state: **required scope done and green** (crash fix + codec + v6 §5
tests 1–3). Stretch items v6 §5.4–5 were not attempted inside the 30-min box;
they are listed as follow-ups in §6. Patch = 2 commits `bec1d91..e1da1a4`
exported to evidence as `0001-*.patch` + `0002-*.patch` (apply cleanly on a
pristine `bec1d91`; that re-applied tree also runs 120 passed).

## 0. Headline verdicts

| # | claim | status |
|---|---|---|
| 1 | Pre-fix, a spec-v1 binary chunk escapes `telemetry()` as `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd3 in position 8` (run.py loop would die) | **CONFIRMED, reproduced** — exact traceback §1 |
| 2 | Post-fix the same bytes raise nothing, are counted (`scan_rx_errors=1`), do not touch Hardware, and a later JSON packet still parses | **VERIFIED** §1 |
| 3 | Codec encodes/decodes spec-v1 exactly: 16 B header + 4..10 B samples LE, measured byte-for-byte against §2.2/§2.1 | **VERIFIED** §2 (all checks PASS) |
| 4 | Sizes: chunk 64 → **272/400 B** core/+signal; chunked N=181 totals **772/1134 B** (3 datagrams: 272+272+228); one-datagram math 740/1102/1464/1826 B matches §2.4; every datagram ≤1472 B | **VERIFIED, measured** §2 |
| 5 | 512 B policy: core/+signal meet it at chunk 64; +ambient/+t_us need chunks **62/49** (→512/506 B) — the spec's own `(<512 False, <1472 True)` cells at chunk 64 are 528/656 B | **VERIFIED** §2 |
| 6 | Missing chunk never silently accepted: `assemble_scan()`/`ScanAssembler` report `complete=False`, `missing=[…]`, `quality<1` (also on 0.5 s timeout / newer scan_id) | **VERIFIED** §4 |
| 7 | JSON telemetry path behavior unchanged; `tests/test_link.py` unmodified and green | **VERIFIED** (diffstat excludes it; 120 passed) |
| 8 | Suite: baseline 80 passed → patched **120 passed** (+40 new tests), no warnings | **VERIFIED** §4 |

## 1. Crash repro, pre-fix (RED) → post-fix (GREEN)

Repro script: `crash_repro.py` (evidence dir). It sends the exact
`check_parser.py` bytes `53 01 29 00 00 03 00 40 d3 2c 01 00` to a
UDPLink-bound loopback socket and calls `telemetry()` once.

```
$ PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
      crash_repro.py <clone>            # tree at bec1d91 (unpatched)
```
Pre-fix (raw capture `crash_repro_pre_raw.txt`, exit 1):
```
Traceback (most recent call last):
  File ".../crash_repro.py", line 38, in <module>
    hw = link.telemetry(1.0)
  File ".../i9-repo/link.py", line 108, in telemetry
    pkt = json.loads(data)
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd3 in position 8: invalid continuation byte
EXCEPTION ESCAPED telemetry():
...
VERDICT: crash escaped (RED)
```
This is the v6 §2.5 failure: `except json.JSONDecodeError` (link.py:109 pre-fix)
cannot catch `UnicodeDecodeError` (a `ValueError`, not a JSONDecodeError —
`check_parser_out.txt` re-confirms `is JSONDecodeError: False`).

Post-fix (raw capture `crash_repro_post_raw.txt`, exit 0):
```
telemetry() returned without raising: Hardware(dist_front_m=None, ...)
follow-up JSON parsed: dist_front_m=0.5 batt_v=7.4 watchdog_ok=True
stats: {'link': 'udp', ..., 'scan_rx': 0, 'scan_rx_errors': 1}
VERDICT: no exception escaped (GREEN)
```
Fix in the patched tree (`e1da1a4`):
* `link.py:131-143` — sniff `data[0] == 0x53` before `json.loads` (JSON always
  starts `{` 0x7B), decode via `scan_payload.decode_scan_chunk`, count rejects
  in `scan_rx_errors`, expose via `last_scan` + assembler. Binary traffic never
  reaches `last_hw`/`telemetry_rx`.
* `link.py:147` — JSON guard broadened to
  `except (json.JSONDecodeError, UnicodeDecodeError)` (belt and braces: a
  non-UTF-8 non-scan datagram is dropped, not fatal). Nothing else on the JSON
  path changed.

## 2. Codec conformance vs v6 §2 (measured, `codec_conformance_out.txt`)

Header, one +t_us chunk (`scan_id=0x0129`, `t0=1000 ms`, `period=33000 µs`,
`samples=(-45, 4000, 0xFE, 600, 40, 7)`), measured bytes
`53 5d 29 01 00 03 00 01 e8 03 00 00 e8 80 00 00 d3 a0 0f fe 58 02 28 00 07 00`:

| off | field | type | spec | measured | §  |
|---|---|---|---|---|---|
| 0 | `msg` | u8 | 0x53 ('S') | 0x53 | §2.2 |
| 1 | `ver_flags` | u8 | ver=1; bit2 signal; bit3 ambient; bit4 t_us; bit5 partial; bit6 timeouts; bit7 reserved | 0x5d = 1\|4\|8\|16\|64 (all flags + timeouts) | §2.2 |
| 2-3 | `scan_id` | u16 LE | | 0x0129 = 297 | §2.2 |
| 4 | `chunk_idx` | u8 | 0-based | 0 | §2.2 |
| 5 | `chunk_count` | u8 | ceil(N/64) | 3 | §2.2 |
| 6 | `first_idx` | u8 | | 0 | §2.2 |
| 7 | `n_samples` | u8 | 1..64 | 1 | §2.2 |
| 8-11 | `t0_ms` | u32 LE | | 1000 (`e8 03 00 00`) | §2.2 |
| 12-15 | `period_us` | u32 LE | | 33000 (`e8 80 00 00`) | §2.2 |
| 16 | `angle_dd` | i8 | −90..+90, −128 = no sample | −45 (`d3`) | §2.1 |
| 17-18 | `range_mm` | u16 LE | 0..4000 **mm** | 4000 (`a0 0f`) | §2.1 |
| 19 | `status` | u8 | RangeStatus / 0xFE / 0xFF | 0xFE | §2.1 |
| 20-21 | `signal_cmcps` | u16 LE | optional (bit2) | 600 | §2.1 |
| 22-23 | `ambient_cmcps` | u16 LE | optional (bit3) | 40 | §2.1 |
| 24-25 | `t_us` | u16 LE | optional (bit4) | 7 | §2.1 |

Size table (all check PASS, `codec_conformance_out.txt` §B/§C):

| variant (flags signal/ambient/t_us) | B/sample | chunk-64 datagram | §2.4 | chunked N=181 (3 dgrams) | one-datagram math §2.4 |
|---|---|---|---|---|---|
| core {angle,range,status} | 4 | **272** | 272 | 772 = 272+272+228 | 740 |
| +signal | 6 | **400** | 400 | 1134 | 1102 |
| +ambient (and +signal) | 8 | 528 (→ 512 B at chunk 62) | 528 `(<512 False)` | 1496 | 1464 |
| +t_us (all) | 10 | 656 (→ 506 B at chunk 49) | 656 `(<512 False)` | 1858 | 1826 |

Every chunked datagram ≤1472 B (max 656 B); `chunk_count` = 1/2/3 for
N=37/91/181. Strict decode rejections (measured messages): the 12 B
`check_parser.py` packet → `short packet: 12 B < 16 B header`; trailing byte →
`length mismatch … 27 B`; version 2 → `unsupported version 2`; bit7 →
`reserved flag bit7 set`; angle +100, range 5000, n_samples>64, chunk_idx ≥
chunk_count also rejected (unit tests).

## 3. API surface chosen for M2 consumers

New module **`scan_payload.py`** (392 lines; chosen over growing `link.py`
because it is a pure, socket-free codec — importable by tests and the M2
matcher without a transport, and `link.py` stays transport-only):

* Constants: `SCAN_MAGIC=0x53`, `SCAN_VERSION=1`, `CHUNK_HEADER_LEN=16`,
  `SCAN_CHUNK_MAX=64`, `NO_SAMPLE_ANGLE=-128`, `MAX_RANGE_MM=4000`,
  `STATUS_TIMEOUT=0xFE`, `STATUS_ABORTED=0xFF`, flag bits.
* `ScanSample(angle_dd, range_mm, status, signal_cmcps=None, ambient_cmcps=None, t_us=None)`
* `ScanChunk(scan_id, chunk_idx, chunk_count, first_idx, t0_ms, period_us,
  samples: tuple[ScanSample, ...], partial=False)` + props `n_samples`,
  `has_signal/ambient/t_us`, `sample_size`, `datagram_size`, `has_timeouts`
* `encode_scan_chunk(chunk) -> bytes` — strict (u8/u16/u32 bounds, angle
  −128 ∪ [−90,90], range ≤4000, 1..64 samples, uniform optionals)
* `decode_scan_chunk(data) -> ScanChunk` (raises `ScanPayloadError`),
  `is_scan_chunk(data) -> bool` (0x53 sniff)
* `chunk_plan(n, chunk_max=64) -> [(first_idx, n), …]`
* `ScanAssembly` (`.missing/.complete/.partial/.quality/.samples()`),
  `assemble_scan(chunks)`, `ScanAssembler(timeout_s=0.5)`
  (`.add(chunk, now) -> finished|None`, `.poll(now)`, `.in_flight`)

`link.UDPLink` additions (least-invasive: `telemetry()` still returns
`Hardware`): attributes `last_scan`, `scan_rx`, `scan_rx_errors`,
`scan_assembler`, `scan_results`; `take_scans() -> list[ScanAssembly]`;
`stats()` gains `scan_rx`/`scan_rx_errors` (additive).

Spec decisions where §2 left freedom (all documented in the module docstring):
optional-field order derived as signal→ambient→t_us (matches §2.1 table and
header bit order 2,3,4); encoder derives bit6 as `any(status==0xFE)`; decoder
tolerates either bit6 polarity (recomputed from samples) so a firmware
inconsistency cannot kill a chunk; `status` is passed through opaquely (the
matcher owns dropping 4/7/8/14 per §2.1); 0.5 s assembler timeout per §2.3.

## 4. Tests and suite

New: `tests/test_link_scan_payload.py` (5 tests, 212 lines) +
`tests/test_scan_chunk_limits.py` (35 tests, 147 lines) = **40 added**.
They cover v6 §5 items 1–3:
* UDP roundtrip through the fake-ESP32 pattern of `tests/test_link.py:17-51`:
  exact sample equality for all flag combos, −90/−128 angles, range_mm=4000,
  status 0xFE, partial flag; 181-sample scan tiled over 3 datagrams reassembles
  complete (−90…+90); one `telemetry()` drain handles a JSON packet **and** a
  binary chunk.
* Regression: the exact `check_parser.py` bytes do not raise, are counted as
  rejected scan traffic, leave Hardware untouched, and a subsequent JSON packet
  parses. Plus strict-decode rejections (truncation, trailing bytes, version 2,
  reserved bit, angle +100, range 5000, n_samples>64).
* Limits: `chunk_count == ceil(N/64)` and contiguous tiling for N∈{37,91,181};
  ≤1472 B for all 8 flag combos; ≤512 B core/+signal at chunk 64 and
  +ambient/+t_us at 62/49; the 528/656 chunk-64 overage pinned as the spec's
  own numbers; missing chunk ⇒ `complete=False` + `missing` + `quality<1`
  (via `assemble_scan`, 0.5 s timeout, newer-scan_id supersede); mixed
  scan_ids / lying `chunk_count` raise.

Commands and result (full verbose capture `suite_full.txt`):
```
$ cd <clone e1da1a4> && PYTHONDONTWRITEBYTECODE=1 \
    /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider
80 passed  (baseline, bec1d91)   →   120 passed in 15.02s  (patched; no warnings)
```
Patch applicability proof: `git am` of both patches on a pristine `bec1d91`
worktree applied cleanly and that tree also ran **120 passed**.

## 5. Decisions & uncertainties

* Sniff **and** broadened `except` are both applied (a malformed non-0x53
  binary datagram must not be able to crash the loop either).
* Rejected scan datagrams increment `scan_rx_errors` instead of disappearing —
  loss is visible, matching the "never silently accepted" spirit of §2.3.
* Decode is strict (spec bounds enforced); if real firmware later emits values
  outside spec (e.g. range >4000 from a sensor bug) a whole chunk is dropped
  and the scan reports partial — chosen deliberately over silent acceptance;
  revisit with hardware in hand.
* **Unverified against real hardware**: no ESP32/firmware scan code exists yet
  (v6 verdict 1), so the codec is exercised only against its own encoder and
  the spec's numbers; UDP loopback only. The 512 B datagram cap remains a
  policy choice, not a measured link limit (§2.4/§6). `encode_scan_command`
  and telemetry `scan_seq`/`scan_state` (v6 §4) are not implemented (not in
  required scope).

## 6. Follow-ups (out of scope here)

Firmware scan FSM per v6 §4 (non-blocking, ≤1 I2C op + 1 `ledcWrite`/pass);
`config.py SweepSensorConfig` step/rate fields; host `encode_scan_command` +
telemetry `scan_seq`; v6 §5.4 cadence-model test (pure-Python port of
`tick_sim.py` FSM) and §5.5 command-validation mirror — the two stretch items,
not attempted in this box; §5.6 `tools/sim/tof_desmear_cadence.py`; §5.7
`tools/smoke_scan.py` hardware smoke.

## 7. Evidence (all in `i9-scan-link-evidence/`, sha256 in `sha256sums.txt`)

| artifact | note | sha256 |
|---|---|---|
| `0001-fix-link-…patch` | commit `448f3c5` (fix + codec) | `acd7e22fe72a6b4e580f4ef19c81e26a8a8e59732151c2eb37cff9d52ae69db2` |
| `0002-test-link-…patch` | commit `e1da1a4` (tests) | `c70ac877cbe3d5ecd4bb6d6448c03c2b4cad59d7996df4cc1282c95236259980` |
| `crash_repro.py` / `crash_repro_pre_raw.txt` / `crash_repro_post_raw.txt` / `crash_repro_transcript.txt` | RED traceback + GREEN transcript | `e8345b1674ced28010c8f139ffd9533f2fa689158274e823952347cd84538cc0`, `fe7cd4e11b9df732c1dac9340f59085d966dc2a0a4848e7eff0d71652ab21d72`, `9d9dc152d44c10f31ae024c9f7ae327094e18be3acbb81bfc1012dd2038102bc`, `71001926910a76688f75195d78622ad317007fd199e98ac0ccfd39968ec0f95d` |
| `codec_conformance.py` / `codec_conformance_out.txt` | layout/size/strictness table (all PASS) | `0fac89aee3e19c3a85fbff907761aa3ca31c790b0485b55d191310a80888cf9d`, `7a11f5f9539cad42f983cb1ca31feeb06528d8b03d3be6585fa2e54e87649ab0` |
| `check_parser.py` / `check_parser_out.txt` | original v6 repro bytes/verdicts | `457e872ac9a3de9a321cd2155608cd49498842a921ad5a23696ccb0451a34fd4`, `8003f27fe114da53bcbd87798d9dd6ab322e5ffaeafe741c00f9a539bc07ebdd` |
| `suite_full.txt` | 120 passed (verbose) | `ed0056a0aec2ca314a6c1bc200e6c5598d2e599a49cebc48db1e5deaac788e0a` |
| `diffstat.txt` | 4 files, +804/−3; `test_link.py` untouched | `142fcdb27454e8c478b7ea6430b56d718c562306fe26690d62b81c6a12369575` |

Key source citations in the patched tree: `link.py:131-143` (sniff + route +
error counting), `link.py:147` (broadened JSON guard), `link.py:164-166`
(0.5 s partial close), `link.py:171-175` (`take_scans`), `link.py:105-109`
(new attrs); `scan_payload.py:66-69` (constants), `:263` (encode), `:318`
(sniff), `:323` (decode), `:219` (`chunk_plan`), `:177` (`ScanAssembler`),
`:379` (`assemble_scan`).

Exact commands used (all from the scratch clone §header, evidence scripts by
absolute path): clone/fetch/checkout as in the task brief; `pytest -q`;
`git format-patch -o <evidence> bec1d91..HEAD` (note: the local branch `prtip`
advanced with the commits, so the base commit was used explicitly);
`git am` verification on a fresh `bec1d91` branch.
