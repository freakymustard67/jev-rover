# v6 — ToF servo-sweep wire-payload spec v1 + 400 ms watchdog interleave verification

Run: 20260928-2220 · author: verification subagent (v6) · date: 2026-09-29
Scope: verify a v1 wire payload spec for the planned 180° ToF sweep and verify the
proposal's constraint *"a scan must not starve the 400 ms watchdog (interleave reads)"*
against the actual firmware source. Read-only on `/home/freakymustard/jev-rover`
(repo HEAD `9c33ec0`, `git status --porcelain` empty), experiments under
`/home/freakymustard/.hermes/cache/scratch/w5/v6/`.

Pinned inputs:
- `firmware/rover_esp32/rover_esp32.ino` — 949 lines, md5 `f61c89a28153514b4b725660260c6541`
- `docs/planning/semantics-layer-proposal.md` §10–§12, `docs/planning/sweep-validation.md`
- `tools/sim/tof_sim.py` (242 lines)
- `link.py`, `run.py`, `config.py`, `tests/test_link.py`
- VL53L1X local docs: `.../refs/taskA6/um2356.txt`, `.../refs/taskA6/ds_text.txt`
- Python used: `/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16)

---

## 0. Headline verdicts

| # | claim | status |
|---|---|---|
| 1 | No scan/servo code exists in the firmware; servo pin/PWM channel not allocated | **CONFIRMED** |
| 2 | Watchdog = `now - g_cmd.lastMs > 400` evaluated in the tick; `lastMs` is stamped at **UDP-poll time**, and `rvrPollUdp()` runs before the tick in every loop pass | **CONFIRMED** (`ino:649-652, 685-690, 934-943`) |
| 3 | With the host alive, a blocking sensor read of **any** length (tested to 1004 ms) does **not** trip the watchdog; it freezes the tick (reflex/slew/motors) for the block duration | **CONFIRMED** (tick_sim part A) |
| 4 | The "starvation" risk is real only for a scan that does not return to `loop()`; a whole-sweep-in-one-call freezes the safety layer for the full sweep (3.4 s @91×33 ms … 181.7 s @181×1000 ms) and delays the trip by the sweep (sim: 0.84 s @33 ms budget, 10.57 s @140 ms) | **CONFIRMED** |
| 5 | Interleaved non-blocking FSM (≤1 bounded I2C op + optional PWM write per pass, ≤5 ms) keeps the UDP service interval ≤~6 ms (margin ≥375 ms) and the command age at the tick ≤~56 ms (margin ≥344 ms), with the trip decision ≤425 ms | **CONFIRMED** (sim) / normative rule in §3.2(d) |
| 6 | VL53L1X timing budget range 20–1000 ms; 33 ms minimum for all distance modes; 140 ms for 4 m; ≤50 Hz; the blocking `WaitMeasurementDataReady` has no documented timeout | **CONFIRMED** (quotes §3.1/§4.1) |
| 7 | Payload: 4 B/sample min (`angle i8 + range u16 + status u8`) + 16 B chunk header; 181 samples = 740 B (core) / 1464 B (8 B full variant) — both < 1472 B IPv4 no-frag limit; chunk 64 → 272/400 B datagrams | **CONFIRMED** (payload_size.py) |
| 8 | The proposal's `{seq, angles, ranges}` sketch is under-specified (chunk framing, status, desmear timing) and `ranges[cm]` throws away sensor resolution (native unit is mm) | **CONFIRMED** (§6) |
| 9 | Today's host parser crashes (`UnicodeDecodeError`, **not caught** by `except json.JSONDecodeError`) or silently drops binary scan datagrams; magic-byte sniff required | **CONFIRMED** (check_parser.py) |
| 10 | Existing sensor is **VL53L0X**, not VL53L1X; its validity window is 2.0 m (`ino:214,416-418`) and the 4 m/25° assumptions need the L1X | **CONFIRMED** (§6 item 8) |
| 11 | The §10.1 "desmear robust to ±25% motion error" result does **not** survive a physically achievable sweep duration: 91 beams × 2° at 3.25 s → 13% success (22.3/29.4 cm) vs 100% at the assumed 1.0 s | **CONFIRMED** (desmear_recheck.py, §6 open risks) |
| 12 | The sim's "1 s sweep" for 91 beams is physically unreachable with a VL53L1X (≥33 ms/sample → ≥3.0 s) | **CONFIRMED** |
| 13 | Adafruit `VL53L0X::readRange()` block duration/timeout; ESP-IDF UDP receive-mailbox depth/drop policy; real I2C transaction times; servo make/speed | **UNVERIFIED** (no source/docs on this box) |

---

## 1. Ground truth extracted (with citations)

Firmware safety contract (`rover_esp32.ino`):
- `ino:19-24` — *"1) WATCHDOG. If no valid command has been accepted for RVR_WATCHDOG_MS (400 ms), the motors are cut immediately … It is pure arithmetic on millis() and cannot be blocked by a dead laptop, a dead router, or packet loss."*
- `ino:166-168` — `RVR_TICK_MS=20`, `RVR_TELEM_MS=50`, `RVR_WATCHDOG_MS=400`.
- `ino:236-237` — `RVR_RX_BUF=256`, `RVR_TX_BUF=384`.
- `ino:649-657` — on acceptance: `g_cmd.valid=true; g_cmd.lastMs = now;` … `g_peerIp = …` (the `now` is the loop-pass timestamp passed in from `loop()`).
- `ino:685-690` — watchdog evaluated **in the tick**: `g_watchdog = (uint32_t)(now - g_cmd.lastMs) > RVR_WATCHDOG_MS;`.
- `ino:934-943` — `loop()`: `now = millis(); rvrPollUdp(now);` **first**, then the tick.
- `ino:623-670` — `rvrPollUdp()` accepts up to `RVR_MAX_PACKETS_PER_TICK = 4` per pass (`ino:233`).
- `ino:557-559` — *"A rejected datagram must NOT refresh the watchdog and must NOT become the telemetry destination."*
- `ino:833-838` — telemetry is serialized into `char buf[RVR_TX_BUF]`; *"Never send a truncated datagram"*.
- `ino:402-418` — the front sensor read is a blocking single-shot (`g_lox.readRange()`) with `timeoutOccurred()` handling; `ino:420-423` — the HC-SR04 note: `pulseIn()` blocks up to 25 ms and *"That delay cannot hide a watchdog trip: the watchdog is evaluated from millis() and the tick checks it after this call returns."*
- `ino:420-423`/`ino:328-329` — no servo code anywhere: `grep -nE 'scan|sweep|servo'` → exit 1, no output; PWM/LEDC channels **0 and 1** are taken by the motors.
- `ino:375-378, 407-418` — the ToF in the firmware is an **Adafruit_VL53L0X** at `RVR_VL53L0X_ADDR`; `ino:58-63` default `RVR_DISTANCE_SENSOR RVR_SENSOR_VL53L0X`; `ino:214-219` `RVR_VL53L0X_MAX_M = 2.0f`, invalid marker 8190.

Host side:
- `run.py:188` — `--control-hz` default **20.0**; `run.py:187` `--perception-hz` default 15.0.
- `run.py:328` — `link.send(cmd, t)` every control iteration; `run.py:347-350` paces the loop to `now + 1/control_hz`.
- `link.py:75` — `UDPLink(..., ttl_ms: int = 400)`; `link.py:87-95` — JSON command `{t, seq, v, w, ttl_ms}`; `link.py:102` — `recvfrom(4096)`; `link.py:107-111` — `json.loads(data)` inside `except json.JSONDecodeError: continue`.
- `config.py:177-181` — `SweepConfig{enabled, sensor, match, desmear}` already exists; `SweepSensorConfig{kind, min_range_m: 0.04, max_range_m: 4.0}` (`config.py:162-166`); **no rate/step/scan fields yet**.
- `tests/test_link.py:17-51` — existing local-UDP round-trip pattern to extend.

Sensor docs (quoted lines are from the local copies):
- `um2356.txt:218-221` — *"The function VL53L1_WaitMeasurementDataReady() polls on the device interrupt status until ranging data are ready. This function blocks all other operations on the host as long as the function is not completed, because an internal polling is performed."*
- `um2356.txt:222-226` — *"The host can poll on the function VL53L1_GetMeasurementDataReady() … This function does not block other operations. It is the preferred and recommended method if the sensor is used in polling mode."*
- `um2356.txt:239-243` — *"The interrupt must be cleared by calling the driver function: VL53L1_ClearInterruptAndStartMeasurement() after reading the ranging data … it is mandatory to call this function after getting the ranging measurement."*
- `um2356.txt:259,262` — budget = *"time required by the sensor to perform one range measurement"*; *"The minimum and maximum timing budgets are [20 ms, 1000 ms]"*.
- `um2356.txt:255-257` — `VL53L1_WaitDeviceBooted()` *"should not be blocking for more than 4 ms, assuming 400 kHz I2C and 2 ms latency per transaction"* (the only blocking-bound statement in the doc; used as the I2C overhead estimate).
- `um2356.txt:480-494` — `SignalRateRtnMegaCps` *"16.16 fix point … divided by 65536"*, `AmbientRateRtnMegaCps` same, `RangeMilliMeter` *"16 bit integer … distance in millimeters"*, `RangeStatus` *"8 bit integer … A value of 0 means the ranging is valid"*.
- `um2356.txt:294-296, 332-334` — limit checks (sigma 15 mm → RangeStatus 1, signal 1 Mcps → RangeStatus 2); out-of-limit ⇒ RangeStatus ≠ 0.
- `um2356.txt:499-513` — RangeStatus table: 0 valid, 1 SIGMA_FAIL, 2 SIGNAL_FAIL, 4 OUTOFBOUNDS_FAIL, 5 HARDWARE_FAIL, 7 WRAP_TARGET_FAIL, 8 PROCESSING_FAIL, 14 RANGE_INVALID.
- `um2356.txt:728` — `-7 VL53L1_ERROR_TIME_OUT  Ranging is aborted due to timeout` (a driver error code, not a host-side timeout for the blocking wait).
- `ds_text.txt:21,63` — *"Up to 50 Hz ranging frequency"*; `ds_text.txt:384-389` — *"timing budget can be set from 20 ms up to 1000 ms. 20 ms … only in Short distance mode. 33 ms is the minimum timing budget which can work for all distance modes. 140 ms … allows the maximum distance of 4 m"*.
- `tof_sim.py:18` — sensor model 4 m, *"~1.5 cm + 1% noise, 5% dropouts, 2% outliers"*; `tof_sim.py:214-216` — 91 beams @2° (default `sweep_s=1.0`), 46 @4°, 31 @6° commented *"~1 s sweep at 30 Hz sensor"*; `sweep-validation.md:55-63` labels all desmear rows *"1 s sweep, 2° beams"*.

---

## 2. Deliverable 1 — Scan payload spec v1

### 2.1 Per-sample fields, with doc provenance

| field | type | units/range | why / source |
|---|---|---|---|
| `angle_dd` | `int8` | −90…+90 deg, relative (right +), −128 = no sample | proposal §10.2 (`semantics-layer-proposal.md:239`); 1° resolution matches the servo's repeatability |
| `range_mm` | `uint16` | 0…4000 mm (**mm, not cm**), 0 = no valid reading | sensor's native unit is mm (`um2356.txt:491`); cm would quantize 1 cm ≈ ⅔ of the modelled noise σ 1.5 cm (`tof_sim.py:18`) for free |
| `status` | `uint8` | 0…14 = VL53L1X RangeStatus, 0xFE = sample timeout, 0xFF = aborted | `um2356.txt:493-494,499-513`; host must drop 4/7/8/14 before matching; required because the matcher's outlier model otherwise ingests phase-wrap ranges |
| `signal_cmcps` | `uint16` | 0…65535 centi-MCPS, 0xFFFF saturated | `SignalRateRtnMegaCps` 16.16 (`um2356.txt:480-482`); 0.01 MCPS LSB keeps 100 counts at the 1 Mcps default limit (`um2356.txt:334`); **optional** |
| `ambient_cmcps` | `uint16` | 0…65535 centi-MCPS | `AmbientRateRtnMegaCps` 16.16 (`um2356.txt:483-485`); **optional** |
| `t_us` | `uint16` | ms since scan start, saturating | **optional**; only needed if the firmware cannot guarantee a constant cadence (see §2.3) |

All multi-byte fields little-endian. Sample `k` of a chunk has index `first_idx + k`.
Integers only; no floats anywhere in the binary path (avoids the `isfinite`/clamp style
checks the JSON path uses at `ino:584-599`).

### 2.2 Chunk header v1 (16 B)

| off | field | type | notes |
|---|---|---|---|
| 0 | `msg` | `u8 = 0x53 ('S')` | first binary message in this protocol (`link.py` is JSON-only today) |
| 1 | `ver_flags` | `u8` | bits0-1 version=1; bit2 has_signal; bit3 has_ambient; bit4 has_t_us; bit5 partial (sweep aborted); bit6 timeouts present; bit7 reserved |
| 2-3 | `scan_id` | `u16 LE` | host-chosen, incremented per scan; telemetry echoes it as `scan_seq` |
| 4 | `chunk_idx` | `u8` | 0-based |
| 5 | `chunk_count` | `u8` | total chunks of this scan |
| 6 | `first_idx` | `u8` | index of first sample in this chunk (0…180 for 1° steps) |
| 7 | `n_samples` | `u8` | samples in this chunk |
| 8-11 | `t0_ms` | `u32 LE` | ms since scan start at which `first_idx` was measured (desmear anchor) |
| 12-15 | `period_us` | `u32 LE` | nominal cadence actually achieved (desmear time model); 0 = unknown/use per-sample `t_us` |

### 2.3 Chunk / seq semantics (fire-and-forget, no acks — per proposal §10.2)

- One scan = `scan_id` with `chunk_count = ceil(N/64)` chunks. Chunks are emitted as
  they fill and once at sweep end; a `partial` flag marks an aborted sweep.
- The host assembles by `(scan_id, chunk_idx)`; a scan is complete when all
  `chunk_idx < chunk_count` have arrived. If a newer `scan_id` starts, or 500 ms pass
  with no new chunk, the scan is dropped or matched as *partial* with a quality penalty
  (host policy). Missing chunks are therefore **detectable**, never silent.
- Command repetition (no-acks philosophy): the host re-sends the same start command
  (same `scan_id`) at 20 Hz until telemetry shows `scan_seq == scan_id`, then stops.
  Firmware rule: `scan.id == lastStartedId` ⇒ ignore (idempotent); new id ⇒ start;
  `action:"stop"` ⇒ abort. A malformed `scan` object invalidates the whole datagram
  and must not refresh the watchdog (`ino:557-559`).
- Servo duty: servo must not be written more than once per pass; one `ledcWrite`.

### 2.4 Size and chunking math (`payload_size.py`, exact output)

```
== UDP/IP arithmetic ==
WiFi MTU 1500 - (IP 20 + UDP 8) = 1472 B : IPv4 no-fragment UDP payload limit
IPv4 max UDP payload (with IP fragmentation) = 65535 - 28 = 65507 B
host recv buffer link.py:102 = 4096 B (silent truncation if exceeded)

== sample sizes ==
A base {angle,range}     3 B  (angle_dd_i8=1+range_cm_u16=2)
B +status                4 B
C +signal                6 B
D +ambient               8 B
E +t_us (all)           10 B

== one datagram per whole sweep (16 B header + N samples) ==
variant                    180deg@1deg   180deg@2deg   180deg@5deg  fits 1472?
A base {angle,range}              559B          289B          127B         yes
B +status                         740B          380B          164B         yes
C +signal                        1102B          562B          238B         yes
D +ambient                       1464B          744B          312B         yes
E +t_us (all)                    1826B          926B          386B      chunk!

== recommended chunking (chunk = 64 samples) ==
B +status                16+64*4 =  272 B  (<512 True, <1472 True)  181 samples -> 3 chunks
C +signal                16+64*6 =  400 B  (<512 True, <1472 True)  181 samples -> 3 chunks
D +ambient               16+64*8 =  528 B  (<512 False, <1472 True)  181 samples -> 3 chunks

== max samples per chunk for a 512 B datagram budget ==
B +status   124   C +signal   82   D +ambient   62   E +t_us   49
```

**Recommendation:** `RVR_SCAN_CHUNK_MAX = 64`, datagram cap 512 B (not 1472), always
chunked from v1 — the current firmware TX path builds into `char[384]` and refuses
truncation (`ino:237,833-838`), so a dedicated binary buffer is needed anyway; make it
`uint8_t buf[16 + 64*10] = 656 B` (covers all flag combos, trivial on ESP32) and keep
every datagram ≤512 B so common WAN/VPN/server UDP buffers cannot bite. Per-sweep volume
at 1° steps: 724 B (core) to 1810 B (all fields) — a few hundred B/s at a 25 Hz sample
rate. *(The `payload_size.py` labels read `range_cm_u16`: the field is 2 B either way —
v1 sends **mm**.)*

Telemetry JSON: measured compact size today 179 B; with `scan_seq` + `scan_state` 212 B
→ 172 B headroom under `RVR_TX_BUF=384` (`ino:237`).

### 2.5 Host-side framing constraint (found during verification)

`link.py:107-111` does `json.loads(data)` with only `except json.JSONDecodeError`. A
binary chunk whose payload contains a byte ≥0x80 (any |angle| > 63°) raises
`UnicodeDecodeError` — a `ValueError` but **not** a `json.JSONDecodeError` — which escapes
`telemetry()` and would kill the `run.py` loop; an all-ASCII binary chunk is silently
dropped instead. Repro output (`check_parser.py`):

```
real chunk (angle=-45 -> 0xD3, range 300 cm)  -> UnicodeDecodeError (NOT caught by link.py:109 -> escapes telemetry(), crashes run.py loop)
all-ASCII chunk (angles 0,1,2)                -> JSONDecodeError (caught by link.py:109 -> packet silently skipped)
UnicodeDecodeError is ValueError: True | is json.JSONDecodeError: False
```

Required host change: sniff `data[0] == 0x53` **before** `json.loads`, route to the binary
decoder, and broaden the JSON guard to `except (json.JSONDecodeError, UnicodeDecodeError)`.
JSON command/telemetry packets always start with `{` (0x7b), so the sniff is unambiguous.

---

## 3. Deliverable 2 — Watchdog interleave timing analysis

### 3.1 What the watchdog actually measures (verified)

Trip condition: a control tick evaluates `(now - g_cmd.lastMs) > 400` (`ino:685-690`).
`lastMs` is stamped with the **poll timestamp**, not the packet arrival time
(`ino:649-652`), and `rvrPollUdp()` runs before the tick in every `loop()` pass
(`ino:934-943`). Therefore:

- while the host keeps sending at 20 Hz, the watchdog **cannot trip** because of a long
  blocking read — the queued datagram refreshes `lastMs` at the moment the loop polls.
  Simulated: blocking reads up to 1004 ms, host alive ⇒ **0 trips** (part A below).
- the damage of a blocking design is (i) the tick — and with it the watchdog decision,
  the reflex, the slew limiter, and motor updates — is frozen for the block; (ii) if the
  link dies, the trip is late by the remaining block; (iii) a scan that never returns to
  `loop()` freezes the safety layer for the whole sweep (the literal "starve the watchdog"
  failure).

### 3.2 Numbers

(a) **Per-read blocking worst case** (`timing_table.py`; budget+4 ms, the 4 ms from
`um2356.txt:255-257`):

```
budget ms   block = budget+4 ms  > 1 tick?  > watchdog?
       20                    24        yes           no
       33                    37        yes           no
       50                    54        yes           no
      140                   144        yes           no
      400                   404        yes          YES
     1000                  1004        yes          YES
```
Any blocking read ≥20 ms already exceeds the 20 ms tick; the API allows up to 1004 ms,
and there is **no documented host-side timeout** for `WaitMeasurementDataReady` — a
wedged sensor makes the block unbounded (`um2356.txt:255-257` bounds only
`WaitDeviceBooted`; `:728` is just an error code).

(b) **Samples × budget** (ideal sensor cadence, no servo):

```
samples  20 ms   33 ms   50 ms   140 ms
    37   0.74s   1.22s   1.85s    5.18s
    91   1.82s   3.00s   4.55s   12.74s
   181   3.62s   5.97s   9.05s   25.34s
```
Blocking-per-sample variant (budget+4 ms each) is 91×24 ms = 2.2 s … 181×1004 ms = 181.7 s.

(c) **Can any single blocking read fit the margin?** Define the trip-decision margin as
`W − (host gap)`: with the host at 20 Hz nominal (`run.py:188,347-350`) and 0 lost
packets, a stall must stay < 350 ms; with 2 consecutive lost packets (150 ms) < 250 ms;
with a pathological host stall, less. A blocking read of 24–144 ms *fits* the trip
margin for the common budgets but **blows the control-tick margin** (reflex blind for
24–144 ms) and does not bound the wedged-sensor case. A blocking read with budget 400 or
1000 ms does not fit even the trip margin, and a whole-sweep-in-one-call never fits.
Conclusion: **interleaving alone is necessary but a per-sample blocking read is still
wrong** — the read must be non-blocking/state-machine. `um2356.txt:222-226` provides the
non-blocking call (`VL53L1_GetMeasurementDataReady()`); there is no reason to block.

(d) **Simulated outcomes** (`tick_sim.py`; 20 ms tick, watchdog from `millis()`,
acceptance stamped at poll time, host 20 Hz):

```
== A. host alive, 20 Hz: blocking never trips (acceptance is stamped at poll time) ==
mode              bud    n trips   maxAge  sweep s     Hz  tickfreeze  tripLat
interleaved       33ms   91     0    49.6ms     3.24   28.1       0.5ms        -
blocking_sample   20ms  142     0    48.4ms     3.44   41.3      24.0ms        -
blocking_sample   33ms   86     0    49.2ms     3.20   26.9      37.0ms        -
blocking_sample  140ms   20     0    44.0ms     2.88    6.9     144.0ms        -
blocking_sample  400ms    7     0    49.4ms     2.83    2.5     404.0ms        -
blocking_sample 1000ms    2     0    48.4ms     2.01    1.0    1004.0ms        -

== B. host STOPS sending at 3.00 s: trip latency vs scan design ==
mode              bud    n trips   maxAge  sweep s     Hz  tickfreeze  tripLat
interleaved       33ms   91     1    ...        3.24   28.1       0.5ms    0.40s
blocking_sample  140ms   91     1    ...       13.12    6.9     144.0ms    0.51s
one_call_sweep    33ms   91     1    ...        3.37   27.0    3367.0ms    0.84s
one_call_sweep   140ms   91     1    ...       13.10    6.9   13104.0ms   10.57s

== C. interleaved: sweep shape = servo+budget limited ==
interleaved  33ms   91  (5 ms grid)  3.24 s  28.1 Hz   (per-pass work 0.5-3 ms)
interleaved  33ms  181  (5 ms grid)  6.44 s  28.1 Hz
interleaved  20ms   91  (5 ms grid)  3.24 s  28.1 Hz   (servo-limited: 2 deg @60 deg/s)
interleaved  33ms   91  (20 ms grid) 3.65 s  24.9 Hz
```
Note in part B: the trip fires ~0.4 s after the loop finally returns; stale datagrams
still in the receive queue are accepted and stamped at that moment (`ino:649-652`), so
the *measured* latency of a one-call sweep is (sweep end − last command) + ~0.4 s.
Raw rows for the quoted cases (sim window 20 s; `maxAge` is window-truncated after the
trip, so read `tripLat`, not `maxAge`):

```
mode              bud    n trips   maxAge  sweep s     Hz  tickfreeze  tripLat
interleaved       33ms   91     1 17041.4ms     3.24   28.1       0.5ms    0.40s
blocking_sample  140ms   91     1 16954.0ms    13.12    6.9     144.0ms    0.51s
one_call_sweep    33ms   91     1 16620.0ms     3.37   27.0    3367.0ms    0.84s
one_call_sweep   140ms   91     1  6880.0ms    13.10    6.9   13104.0ms   10.57s
```

**Exact interleave rule to put in the spec:**

> (1) `rvrPollUdp()` stays the first statement of every `loop()` pass.
> (2) The scan FSM is serviced from `loop()` and performs **at most one bounded I2C
> transaction and one servo PWM write per pass**; the whole sweep is never executed in
> one call.
> (3) No call on the scan path may block: `VL53L1_WaitMeasurementDataReady()` and any
> other wait/loop-inside function are forbidden — readiness is tested with
> `VL53L1_GetMeasurementDataReady()` (`um2356.txt:222-226`).
> (4) Per-pass scan work ≤ 5 ms (one 2–4 ms read burst + ≤0.5 ms readiness check).
> (5) A sample not ready within 1.5×budget is recorded as timeout (`status=0xFE`) and the
> sweep advances — a wedged sensor degrades the scan, never the loop.
> (6) The watchdog is untouched: same `RVR_WATCHDOG_MS=400`, same evaluation site
> (`rvrControlTick`), same `g_cmd.lastMs` stamping; the scan never writes `g_cmd` and
> never resets the watchdog itself.
> (7) Optional hard gate: if `g_watchdog` is true, abort the scan (state → IDLE).

Resulting guarantees: UDP serviced every ≤~6 ms; command age at the tick ≤ host gap +
6 ms (≤56 ms at 20 Hz) → margin ≥344 ms of the 400 ms contract; trip decision late by
≤25 ms (tick + work) → worst-case trip latency ≈425 ms vs 400 ms ideal; reflex/slew
cadence preserved at the 20 ms tick.

**Servo is usually the cadence bottleneck, not the ToF** (`timing_table.py`):

```
 step samples budget  servo ms  period ms  sweep s  eff Hz
    2      91  33ms      33.3        40     3.64    25.0
    2      91 140ms      33.3       140    12.74     7.1
    5      37  33ms      83.3       100     3.70    10.0
    6      31  33ms     100.0       100     3.10    10.0
```
(servo 60 °/s; a full 180° sweep costs ≥3.0 s of servo motion at that speed). `rate_hz`
must therefore be documented as *nominal/best-effort*; the firmware reports the achieved
`period_us` in the chunk header and the host uses that (not `rate_hz`) for desmear.

---

## 4. Deliverable 3 — Minimal firmware change sketch (text only, no repo writes)

```c
// --- new constants (near ino:236) -------------------------------------------
#define RVR_SERVO_PIN        25      // EXAMPLE pin; verify on your board
#define RVR_SERVO_CH         2       // motors own LEDC 0/1 (ino:328-329)
#define RVR_SERVO_HZ         50
#define RVR_SCAN_STEP_MIN_DD 1
#define RVR_SCAN_CHUNK_MAX   64      // <=512 B datagram budget
#define RVR_SCAN_TX_BUF      (16 + RVR_SCAN_CHUNK_MAX * 10)
static uint8_t g_scanBuf[RVR_SCAN_TX_BUF];

// --- scan request/state (near ino:500) -------------------------------------
struct RvrScanState {
  uint8_t  phase;        // 0 idle, 1 wait-ready, 2 flush
  uint16_t id;           // scan_id (host-chosen)
  int16_t  last_id;      // idempotence latch (ino:557-559 discipline)
  int8_t   a_deg, end_deg, step_dd;
  uint8_t  rate_hz;      // nominal only
  uint8_t  idx, n;       // sample index / total
  uint32_t t0_ms, t_phase, period_us, t_chunk;
  uint8_t  chunk[/*... record: */ 10];  // per-sample staging (angle,mm,status,sig,amb)
  uint8_t  chunk_n;
} g_scan;

// --- command parse (extend rvrParseCommand, ino:560-617) ------------------
// optional root["scan"] object: {id,action,start_deg,end_deg,step_deg,rate_hz}
// validate: id integral 0..65535, start<end, |a|<=90, step>=1, 1<=rate<=50;
// any violation -> return false (no watchdog refresh, ino:557-559).
// accepted + id != g_scan.last_id + action=="start" -> latch params, phase=1.

// --- FSM, called ONCE PER loop() PASS (NOT from the tick) ------------------
static void rvrScanStep(uint32_t now) {
  if (g_scan.phase == 0 || g_watchdog) return;            // rule 7
  if (now - g_scan.t_lastOp < 5) return;                  // rule 4: >=5 ms grid
  g_scan.t_lastOp = now;
  if (g_scan.phase == 1) {
    if (!g_l1x.GetMeasurementDataReady()) {               // NON-blocking (UM2356:222-226)
      if (now - g_scan.t_phase > (g_scan.budget_ms * 3) / 2) rvrScanRecord(0xFE, 0);
      return;
    }
    VL53L1_RangingMeasurementData_t m;
    g_l1x.GetRangingMeasurementData(&m);                  // bounded I2C burst
    g_l1x.ClearInterruptAndStartMeasurement();            // mandatory (UM2356:239-243)
    rvrScanRecord(m.RangeStatus, m.RangeMilliMeter);
    ledcWrite(RVR_SERVO_CH, rvrServoDuty(g_scan.a_deg += g_scan.step_dd)); // 1 write
    g_scan.t_phase = now;
  }
  if (g_scan.chunk_n >= RVR_SCAN_CHUNK_MAX || g_scan.idx >= g_scan.n) rvrScanFlush(now);
}

// --- flush (<=1 datagram per pass) ----------------------------------------
static void rvrScanFlush(uint32_t now) {
  // header 16 B per spec; then n_samples * (1+2+1[+2][+2]) little-endian
  // g_udp.beginPacket(g_peerIp, g_peerPort); write(buf, len); endPacket();
  // same destination logic as telemetry (ino:797-801, 841-843)
}

// --- loop() (ino:934-949) --------------------------------------------------
void loop() {
  const uint32_t now = millis();
  rvrPollUdp(now);         // unchanged, first
  rvrScanStep(now);        // NEW: bounded, one op per pass
  if (now - g_lastTickMs >= RVR_TICK_MS) { g_lastTickMs = now; rvrControlTick(now); }
  if (now - g_lastTelemMs >= RVR_TELEM_MS) { g_lastTelemMs = now; rvrSendTelemetry(now); }
}
```

Host side: `link.py` gains `encode_scan_command(cmd)` (JSON command + `scan` object),
`decode_scan_chunk(data) -> Chunk | None` (magic sniff first), `UDPLink.telemetry()`
gains the binary branch (and catches `UnicodeDecodeError`); `config.py`'s
`SweepSensorConfig` gains `step_deg`, `rate_hz`, `scan_daemon` fields; telemetry adds
`scan_seq`/`scan_state` (+33 B, measured).

---

## 5. Deliverable 4 — concrete offline test additions

1. `tests/test_link_scan_payload.py::test_scan_chunk_roundtrip` — extend the existing
   fake-ESP32 UDP pattern (`tests/test_link.py:17-51`): encode → socket → decode; assert
   int8 negative angles, `range_mm=4000`, status 0xFE, all flag combos, chunk tiling
   (`first_idx`/`n` cover `[0,N)`), and both a JSON telemetry packet and a binary chunk
   decode in one `telemetry()` drain.
2. `tests/test_link_scan_payload.py::test_binary_packet_never_escapes_parser` —
   regression for the found bug: feed the exact `check_parser.py` bytes; assert no
   exception escapes and a subsequent JSON telemetry packet still parses.
3. `tests/test_scan_chunk_limits.py` — property test over N ∈ {37,91,181} and all flag
   combos: `chunk_count == ceil(N/64)`, every datagram ≤512 B (and ≤1472 B), missing
   chunk ⇒ scan reported incomplete/partial (never silently accepted).
4. `tests/test_scan_cadence_model.py` — pure-Python port of `tick_sim.py`'s FSM:
   assert per-pass scan work ≤5 ms, trip latency ≤0.45 s after the last command,
   ≥1 sample per 45 ms at 33 ms budget, and that a one-call sweep variant fails the
   model check (doubles as the "no sweep in one call" regression).
5. `tests/test_scan_command_parse.py` — mirror `rvrParseCommand`'s validation for the
   `scan` object in Python (reject step<1, end≤start, |angle|>90, rate>50, non-integral
   id; a malformed scan invalidates the datagram and must not refresh the watchdog —
   the `ino:557-559` contract). Same cases must be repeated on hardware via serial/`nc`
   (README smoke list) since there is no C++ unit harness.
6. `tools/sim/tof_desmear_cadence.py` — rerun `tof_sim.run_trials` at the *achievable*
   sweep durations (see §6) and encode the operating envelope as assertions:
   31×6° @~1.0 s with 25% velocity error = pass; 91×2° @3.25 s driving = fail unless
   motion error ≲5%; 91×2° @3.25 s pivot 30°/s = pass.
7. Opt-in hardware smoke (like `tools/smoke_jev.py`): `tools/smoke_scan.py` — drive a
   scan at 20 Hz command cadence, assert `watchdog:false` for the entire sweep and no
   gap >25 ms in accepted-command age (read from telemetry), then stop the sender
   mid-sweep and assert the trip appears within 0.5 s.

---

## 6. Deliverable 5 — corrections to the proposal's sketch

§10.2, `semantics-layer-proposal.md:239` — `{seq, angles[deg int8 relative], ranges[cm
uint16]}` chunked if needed:

1. **Framing missing.** A bare `seq` cannot serve as a scan identity under chunking;
   v1 uses a 16 B header (`scan_id`, `chunk_idx`, `chunk_count`, `first_idx`, `n`).
2. **`status` missing.** Needed to discard RangeStatus 4/7/8/14 (`um2356.txt:499-513`)
   before the matcher; 1 B/sample is the cheapest correctness win in the whole spec.
3. **`cm` is the wrong unit.** The sensor reports mm (`um2356.txt:491`); cm throws away
   resolution for the same 2 B and is coarser than the modelled noise (`tof_sim.py:18`).
   Send **mm**.
4. **No timing field ⇒ desmear impossible as specified.** `sweep-validation.md:67-69`
   makes desmear mandatory; desmear needs each beam's sampling time. v1 adds `t0_ms` +
   `period_us` per chunk (or per-sample `t_us` when the cadence is uneven). `rate_hz` in
   the command is only nominal — the servo and the tick grid set the real cadence
   (§3.2(d)), and the *achieved* period must be what the matcher uses.
5. **"chunked if needed" should be mandatory.** Always chunk at ≤64 samples/≤512 B: it
   bounds the firmware buffer, limits loss granularity, and matches the existing
   "never send a truncated datagram" discipline (`ino:833-838`).
6. **Binary framing collides with the JSON parser** until `link.py:107-111` is fixed
   (§2.5) — someone must own that change in the same PR as the first binary packet.
7. **Command needs an id + action.** With fire-and-forget UDP and no acks, a one-shot
   start command can be lost; make the start idempotent on `scan_id` and repeat until
   telemetry echoes `scan_seq`.
8. **Sensor identity.** The 4 m / VL53L1X numbers only apply if the front ToF is
   upgraded; today's `Adafruit_VL53L0X` path caps validity at 2.0 m (`ino:214,416-418`)
   and `SweepSensorConfig.max_range_m=4.0` (`config.py:166`) would be a lie for it.

§10.2, `semantics-layer-proposal.md:238` — *"a scan must not starve the 400 ms watchdog
(interleave reads)"*: the constraint **stands**, but the stated mechanism is off. With
this firmware a long blocking read cannot trip the watchdog while the host is alive
(§3.1); interleaving is required because a non-interleaved scan freezes the control tick
(reflex, slew, motors) and, if the link dies, delays the trip by the block/sweep
(sim: 0.40 s interleaved vs 0.84 s and 10.57 s for one-call sweeps). The normative rule
in §3.2(d) is the precise form.

### Open risks / unverified

- **§10.1 desmear robustness vs real cadence (largest risk).** Rerunning the project's own
  simulator at physically achievable sweep durations (`desmear_recheck.py`):

```
sweep 1.00s v=0.45 err_v=+0%   pos 3.0/ 6.8 cm  yaw 1.1/2.6  100%
sweep 1.00s v=0.45 err_v=+25%  pos 6.8/11.1 cm  yaw 1.4/3.2  100%
sweep 3.25s v=0.45 err_v=+0%   pos 3.2/ 8.2 cm  yaw 1.4/2.3   93%
sweep 3.25s v=0.45 err_v=+25%  pos 22.3/29.4cm  yaw 3.7/7.9   13%   <-- collapse
sweep 3.60s v=0.45 err_v=+25%  pos 22.0/27.8cm  yaw 3.6/7.7   10%
sweep 1.00s pivot 30deg/s      pos 2.8/ 4.9 cm  yaw 1.1/2.2  100%
sweep 3.25s pivot 30deg/s      pos 2.4/ 5.6 cm  yaw 1.1/2.1  100%
```
  91 beams × 2° cannot complete in 1.0 s (≥33 ms/sample, `ds_text.txt:386`; measured
  cadence 28.1 Hz → 3.24 s). While driving, use 31 beams × 6° (~1.0 s at 33 ms budget
  with a fast servo) or make the motion estimate accurate to a few percent (encoder
  `v_meas`, `ino:738-755`); keep 91-beam 2° sweeps for stationary/scan-turn work.
- UNVERIFIED locally: Adafruit VL53L0X `readRange()` block duration and internal timeout
  (library not vendored; the ino's own comment at `:420-423` is the only local statement);
  ESP-IDF UDP receive-mailbox depth/drop policy (assumed "retains queued datagrams, drop
  newest on overflow"); real I2C transaction times (4 ms/2 ms figures are the UM2356
  estimate for a different call); servo model/speed (60 °/s assumed); whether the servo
  PWM channel conflicts with the Arduino-ESP32 core 3.x `ledcAttach` API (`ino:269-274`).
- The 512 B datagram cap is a policy choice (WAN/VPN-safe), not a measured limit.

---

## 7. Evidence appendix — exact commands and outputs

All scripts live in `/home/freakymustard/.hermes/cache/scratch/w5/v6/`
(`payload_size.py`, `timing_table.py`, `tick_sim.py`, `desmear_recheck.py`,
`check_parser.py`); full outputs for the first two are inlined in §2.4 and §3.2,
`tick_sim.py` in §3.2(d), `desmear_recheck.py` in §6, `check_parser.py` in §2.5.
Commands:

```
$ /home/freakymustard/jev-rover/.venv/bin/python payload_size.py      # §2.4
$ /home/freakymustard/jev-rover/.venv/bin/python timing_table.py      # §3.2(a)(b)(d)
$ /home/freakymustard/jev-rover/.venv/bin/python tick_sim.py          # §3.2(d)
$ /home/freakymustard/jev-rover/.venv/bin/python desmear_recheck.py   # §6
$ /home/freakymustard/jev-rover/.venv/bin/python check_parser.py      # §2.5
$ cd /home/freakymustard/jev-rover && grep -nE 'scan|sweep|servo' firmware/rover_esp32/rover_esp32.ino
  (exit 1, no output = no scan/sweep/servo code exists)
$ md5sum firmware/rover_esp32/rover_esp32.ino   # f61c89a28153514b4b725660260c6541
$ git rev-parse --short HEAD                    # 9c33ec0  (porcelain empty)
```
