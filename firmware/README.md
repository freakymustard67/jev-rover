# Jev Rover — Reference ESP32 Firmware

Reference firmware for a small differential-drive rover driven over WiFi (UDP) from a
laptop. It implements a fixed wire protocol, a 400 ms command watchdog, and an
onboard distance-sensor safety reflex.

An optional **servo ToF scan mode (v1)** sweeps a time-of-flight sensor through the
front hemisphere and streams the samples back as binary chunk datagrams. It is
**hardware-unverified** and never weakens the watchdog or the reflex — see
[Scan mode (v1, hardware-unverified)](#scan-mode-v1-hardware-unverified).

> **This is reference firmware to adapt to your own car — not a drop-in replacement
> for your exact board.**
> Every pin number and most tuning constants are **EXAMPLE VALUES** chosen to be
> plausible, not to match your hardware. You are expected to edit the config block at
> the top of `rover_esp32/rover_esp32.ino` to match your wiring. The *protocol* is the
> only part that is a hard contract.

---

## Files

| File | Purpose |
| --- | --- |
| `rover_esp32/rover_esp32.ino` | The firmware (single sketch). |
| `rover_esp32/secrets.h.example` | Credential template. Copy to `secrets.h` and edit. |
| `rover_esp32/secrets.h` | **You create this.** Never commit it (see below). |

---

## Protocol

The rover is a **UDP server on port 4210**. The laptop sends commands; the rover
replies with telemetry to the **IP and port of the most recent valid command sender**.
If no command has ever been received, the rover sends telemetry nowhere.

### Command datagram (laptop → rover)

UTF-8 JSON. The laptop is expected to send at ~20 Hz.

```json
{"t": 12.34, "seq": 512, "v": 0.35, "w": -0.4, "ttl_ms": 400}
```

| Field | Type | Units | Meaning |
| --- | --- | --- | --- |
| `t` | float | s | Laptop's monotonic clock at send time. Echoed back verbatim in telemetry as `t`, so the laptop can match a telemetry sample to the command it answers. |
| `seq` | int | — | Laptop command sequence number. **Must be integral.** Echoed back in telemetry. |
| `v` | float | m/s | Forward velocity setpoint. **Clamped to `[-0.6, 0.6]`.** |
| `w` | float | rad/s | Yaw rate setpoint (positive = counter-clockwise / left turn, sign depends on your motor polarity). **Clamped to `[-2.1, 2.1]`.** |
| `ttl_ms` | int | ms | Laptop's intended command lifetime. Optional and informational: the ESP32's own 400 ms watchdog always governs and is never extended by this field. |
| `scan` | object or absent | — | Optional servo ToF sweep request (v1): `{"id","action","start_deg","end_deg","step_deg","rate_hz"}`. A present-but-malformed object rejects the **whole** datagram. See [Scan mode (v1, hardware-unverified)](#scan-mode-v1-hardware-unverified). |

Invalid JSON, missing required fields (`t`, `seq`, `v`, `w`), non-finite numbers, a
non-integral `seq`, values out of the hard clamp range, or a **malformed `scan`
object** are **rejected** — a rejected datagram does **not** feed the watchdog and
does **not** become the telemetry destination. It also does not stop the rover: the
existing watchdog deadline just keeps running, so a stream of garbage silences the
motors 400 ms after the last *good* command.

### Telemetry datagram (rover → laptop)

UTF-8 JSON, sent at 20 Hz while a telemetry destination is known.

```json
{"t": 12.34, "seq": 512, "dist_front_m": 0.42, "dist_rear_m": null,
 "enc_l": 1203, "enc_r": 1198, "v_meas": 0.33, "w_meas": -0.38,
 "batt_v": 7.41, "reflex": false, "watchdog": false, "uptime_s": 83.2,
 "scan_seq": 0, "scan_state": "idle"}
```

| Field | Type | Units | Meaning |
| --- | --- | --- | --- |
| `t` | float | s | Echo of the `t` field of the last accepted command (0.0 before any command). Use `uptime_s` for the rover's own clock. |
| `seq` | int | — | Echo of the last accepted command's `seq` (0 if none yet). This is how you correlate a telemetry sample with the command it answers. |
| `dist_front_m` | float | m | Front distance. **Never null.** Reported as `0.0` when the front sensor read failed or the sensor never initialised — the rover is then stopped with `reflex: true`, so combine those two fields to detect a dead sensor. |
| `dist_rear_m` | float or `null` | m | Rear distance. `null` unless rear sensing is enabled **and** the reading is valid. |
| `enc_l`, `enc_r` | int or `null` | counts | Cumulative encoder counts. `null` when `RVR_ENABLE_ENCODERS` is 0. |
| `v_meas` | float | m/s | Estimated linear velocity. 0.0 when encoders are disabled. |
| `w_meas` | float | rad/s | Estimated yaw rate. 0.0 when encoders are disabled. |
| `batt_v` | float | V | Pack voltage after divider math. 0.0 when battery sensing is disabled. |
| `reflex` | bool | — | Safety reflex is intervening, or intervened within the last `RVR_REFLEX_HOLD_MS` (300 ms default — see below). |
| `watchdog` | bool | — | No valid command for > 400 ms; motors are stopped. |
| `uptime_s` | float | s | Seconds since boot. |
| `scan_seq` | int | — | `scan.id` of the sweep currently active or most recently finished; `0` when no sweep has run yet (so start scan ids at 1 if you care about that distinction). |
| `scan_state` | string | — | `"idle"`, `"scanning"` (includes the brief arming phase before the servo moves), or `"flushing"` (a result chunk is being sent). |

### Protocol points the spec left open, and what we chose

- **Telemetry `t`** is the *echo* of the command's `t`, not rover time (rover time is
  already in `uptime_s`, so echoing is the non-redundant choice and identifies which
  command a sample answers). If your laptop expects rover time in `t`, change one line in
  `rvrSendTelemetry()`.
- **`dist_front_m` is never null** because the protocol types it as `<float>`. A failed
  front-sensor read is reported as `0.0` + `reflex: true`.
- **`ttl_ms` is accepted but not enforced**; the ESP32 watchdog always owns the deadline.
- **`reflex` is latched for 300 ms** (`RVR_REFLEX_HOLD_MS`) so a single 20 ms
  intervention is still visible in 20 Hz telemetry. The latch affects the *flag only*:
  motion gating is recomputed every tick. Set `RVR_REFLEX_HOLD_MS = 0` for strict
  per-tick semantics.
- Floats are serialized by ArduinoJson v7 with round-trip precision (no fixed 2-decimal
  rounding), so `batt_v` reflects what was actually measured.
- **Scan `start`/`stop` carry the full `scan` object.** Every field (including the
  angles, step and rate of a `stop`) is validated regardless of the action, so a
  malformed object is always caught. A `stop` does not have to match a scan id.
- **Scan ids must increase.** A repeated `start` with the same `id` is ignored on
  purpose (fire-and-forget idempotence: re-send until `scan_seq` echoes it).
- **Angles are integers on the wire** (`angle_dd` is an i8), so fractional
  `start_deg`/`end_deg` are rounded to whole degrees. A range that rounds to a single
  point becomes a 1-sample sweep.
- **`t0_ms` is the sample's *read* time** — when the measurement became visible to the
  firmware (one integration window after the sensor started it). Inter-sample spacing
  for desmear is unaffected.

---

## Scan mode (v1, hardware-unverified)

A servo-swept time-of-flight sensor. On command, the rover sweeps a ToF through
`[start_deg, end_deg]` of the **front hemisphere (−90°…+90° relative to heading)**,
records one range sample per step, and streams the samples back as binary chunk
datagrams. Commands and telemetry stay JSON.

> **HARDWARE-UNVERIFIED.** This feature has never been compiled or run against real
> hardware — there is no servo and no VL53L1X on the machine it was written on. The
> wire format below is the contract; the sensor glue, servo numbers and timing
> constants are examples to validate on your bench (checklist at the end of this
> section). Do not trust a sweep for anything safety-relevant.

### What scan mode does not change

The 400 ms watchdog and the front-sensor reflex are **untouched**: same
`RVR_WATCHDOG_MS`, same evaluation in `rvrControlTick()`, same `g_cmd.lastMs`
stamping. The scan never writes `g_cmd` and never resets the watchdog. A laptop that
dies mid-sweep still gets the motors cut ~400 ms after the last valid command, and a
tripped watchdog aborts the sweep (rule 7 below).

### Command

The `scan` object is added to the normal command datagram:

```json
{"t": 12.34, "seq": 512, "v": 0.0, "w": 0.0, "ttl_ms": 400,
 "scan": {"id": 123, "action": "start",
          "start_deg": -90, "end_deg": 90, "step_deg": 6, "rate_hz": 20}}
```

| Field | Type | Rules |
| --- | --- | --- |
| `id` | int | Integral, `0…65535`. Identifies the sweep; echoed by telemetry as `scan_seq`. |
| `action` | string | Exactly `"start"` or `"stop"`. |
| `start_deg`, `end_deg` | number | Within `[-90, 90]` and `start_deg < end_deg`. Rounded to whole degrees for the sweep. |
| `step_deg` | int | Integral, `1…180`. The sweep has `floor((end-start)/step) + 1` samples. |
| `rate_hz` | number | `1…50`. **Nominal only** — the achieved cadence is sensor/servo limited and is reported per chunk as `period_us`. |

Rules:

- A malformed `scan` object (missing/wrong-typed field, out-of-range value, unknown
  action) makes the **whole datagram invalid**: no watchdog refresh and no
  telemetry-destination change.
- `start` is **idempotent on `id`**: a repeat of the same `id` is ignored. A **new**
  `id` aborts any sweep in progress and starts the new one — so increment ids per
  sweep. The intended host behaviour is to re-send the same start at command cadence
  until telemetry shows `scan_seq == id`.
- `stop` aborts the running sweep, flushing what has been measured. It is ignored if
  no sweep is active.
- One sweep at a time; result datagrams go to the same destination as telemetry.

### Result datagrams (rover → laptop, binary)

Sent only while a sweep is running or when it completes/aborts, one chunk per
datagram. The first byte is `0x53` (`'S'`); JSON datagrams always start with `{`
(`0x7B`), so a receiver can sniff the type unambiguously. All multi-byte fields are
little-endian.

**Header (16 bytes)**

| Offset | Field | Type | Meaning |
| --- | --- | --- | --- |
| 0 | `msg` | u8 | `0x53` (`'S'`) |
| 1 | `ver_flags` | u8 | bits 0-1 version (=1); bit 2 `has_signal`; bit 3 `has_ambient`; bit 4 `has_t_us`; bit 5 `partial` (sweep aborted); bit 6 any `0xFE` timeout in this chunk; bit 7 reserved (0) |
| 2 | `scan_id` | u16 | Echoes the command's `scan.id` |
| 4 | `chunk_idx` | u8 | 0-based |
| 5 | `chunk_count` | u8 | Total chunks of this sweep (framing-dependent: `ceil(N/62)` with the default optionals, up to `ceil(N/64)` with fewer columns) |
| 6 | `first_idx` | u8 | Index of the first sample in this chunk |
| 7 | `n_samples` | u8 | Samples in this chunk, 1…64 |
| 8 | `t0_ms` | u32 | ms since sweep start at which `first_idx` was **read** (desmear anchor) |
| 12 | `period_us` | u32 | Achieved cadence so far (average sample spacing); 0 = unknown |

**Samples (uniform layout within a chunk, in index order)**

| Field | Type | Meaning |
| --- | --- | --- |
| `angle_dd` | i8 | −90…+90° relative to heading (right positive); −128 = no sample (not emitted by this firmware) |
| `range_mm` | u16 | 0…4000 mm; 0 = no valid reading (**mm**, not cm) |
| `status` | u8 | Sensor `RangeStatus` (pass-through; the host drops 4/7/8/14), `0xFE` = sample timeout, `0xFF` = aborted |
| `signal_cmcps` | u16 | *if `has_signal`*: return signal rate in centi-MCPS, `0xFFFF` saturated; 0 = unknown |
| `ambient_cmcps` | u16 | *if `has_ambient`*: ambient rate in centi-MCPS |
| `t_us` | u16 | *if `has_t_us`*: ms since sweep start, saturating (compile-time off by default) |

Framing and sizes:

- Core sample = 4 B (`angle_dd` + `range_mm` + `status`); each optional column adds 2 B.
- Firmware defaults: `RVR_SCAN_OPT_SIGNAL=1`, `RVR_SCAN_OPT_AMBIENT=1`,
  `RVR_SCAN_OPT_T_US=0` → 8 B/sample, so the 512 B datagram policy caps a chunk at
  **62 samples** (16 + 62×8 = 512 B). With fewer optionals the cap rises to the spec
  maximum of 64 samples; the staging buffer is sized for the worst case (656 B).
- Chunks are emitted as they fill, plus a final flush at sweep end and a partial
  flush (bit 5 set) on abort, where the in-flight slot is stamped `status=0xFF`.
- Datagrams are never truncated: if a chunk somehow exceeded the policy cap the
  firmware would drop it rather than send it short (same discipline as telemetry).
- Missing chunks are detectable: any chunk carries `chunk_count`, so a receiver can
  see which `chunk_idx` never arrived.

### Interleave guarantees (why a sweep cannot starve the watchdog)

These are the firmware rules (the ones verified by the 400 ms watchdog analysis):

1. `rvrPollUdp()` stays the first statement of every `loop()` pass.
2. The scan FSM is serviced from `loop()` and performs **at most one bounded I2C
   transaction and at most one servo PWM write per pass**; the whole sweep is never
   executed in one call.
3. No call on the scan path may block. Readiness is polled with the non-blocking
   `VL53L1_GetMeasurementDataReady()`; `VL53L1_WaitMeasurementDataReady()` and any
   other wait/loop-inside function are forbidden on the scan path.
4. Per-pass scan work is ≤ 5 ms, and scan operations are separated by a ≥ 5 ms grid
   (`RVR_SCAN_GRID_MS`, `millis()` arithmetic).
5. A sample not ready within 1.5× the timing budget is recorded as a timeout
   (`status=0xFE`) and the sweep advances; a wedged sensor degrades the sweep, never
   the loop.
6. The watchdog is untouched: same `RVR_WATCHDOG_MS=400`, same evaluation site
   (`rvrControlTick()`), same `g_cmd.lastMs` stamping; the scan never writes `g_cmd`
   and never resets the watchdog.
7. If the watchdog is tripped (`g_watchdog`), the sweep aborts (state → idle),
   emitting a partial flush if samples are pending.

Resulting worst cases (L1X path, from the analysis): UDP serviced every ≤ ~6 ms;
command age at the tick ≤ host gap + ~6 ms; the trip decision is late by ≤ ~25 ms
(worst-case trip ≈ 425 ms instead of 400 ms). The L0X fallback is explicitly weaker
(below).

### Sensor selection and caveats

`RVR_SCAN_SENSOR` selects the scan sensor at compile time (section 1 of the sketch):

| | `RVR_SCAN_SENSOR_VL53L1X` (default) | `RVR_SCAN_SENSOR_VL53L0X` (fallback) |
| --- | --- | --- |
| Driver | ST ULD API (`VL53L1_Dev_t`, `vl53l1_api.h` / `vl53l1_platform.h`) | the existing `Adafruit_VL53L0X` front sensor |
| Range | 4 m class; statuses pass through and the host filters | **2.0 m validity** (`RVR_VL53L0X_MAX_M`); the 4 m numbers do not apply |
| Readiness | non-blocking `VL53L1_GetMeasurementDataReady()` | **no readiness poll** — `readRange()` waits inside the driver |
| Status | full `RangeStatus` unchanged | `0` on success, `0xFE` when the driver times out or returns no usable reading |
| Interleave rules | fully satisfied | **best effort / bench only**: rules 3–5 cannot be guaranteed |
| Timing | honours `RVR_SCAN_BUDGET_MS` (33 ms default) | same constant used as minimum slot pacing |

Caveats:

- The L1X glue in section 9 of the sketch is **hardware-unverified**: the ST ULD
  platform bring-up is port-specific and the function names follow UM2356
  (`VL53L1_DataInit`, `VL53L1_StaticInit`,
  `VL53L1_SetMeasurementTimingBudgetMicroSeconds`, `VL53L1_StartMeasurement`,
  `VL53L1_ClearInterruptAndStartMeasurement`, ...). Wire your port's initialisation
  into `rvrScanSensorBegin()` — it is the only place the sketch calls the sensor API.
- Both the VL53L1X and the VL53L0X power up at I2C address `0x29`. Running the scan
  L1X and the L0X front sensor on one bus requires re-addressing one of them
  (XSHUT); this firmware does not do that. An HC-SR04 front sensor avoids the clash.
- `rate_hz` is nominal. The servo is usually the cadence bottleneck (a full ±90°
  sweep costs seconds at hobby-servo speed) and the sensor budget sets the floor; use
  each chunk's `period_us`/`t0_ms`, not `rate_hz`, for desmear.
- The L0X fallback emits `0` for signal/ambient (unknown).
- The firmware drops one ready sample after the servo moves to the start angle, but
  the servo can still be travelling through the first samples of a long sweep.
  Desmear (or a slow sweep) is what makes a moving sweep usable.

### Bench smoke checklist (wheels off the ground)

There is no automated test for this hardware path; do these by hand.

1. **Send a sweep.** With the rover streaming telemetry and receiving commands, send
   a start at ~20 Hz while the sweep runs, e.g.:

   ```sh
   python3 - <<'EOF'
   import json, socket, time
   s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
   dst = ("<rover-ip>", 4210)
   cmd = {"t": 0.0, "seq": 0, "v": 0.0, "w": 0.0, "ttl_ms": 400,
          "scan": {"id": 1, "action": "start", "start_deg": -90, "end_deg": 90,
                   "step_deg": 6, "rate_hz": 20}}
   for seq in range(200):
       cmd["t"] = time.time(); cmd["seq"] = seq
       s.sendto(json.dumps(cmd).encode(), dst)
       time.sleep(0.05)
   EOF
   ```

   One-shot is not enough: without command traffic the watchdog aborts the sweep by
   design (rule 7). A raw one-liner also triggers a (short-lived) sweep:

   ```sh
   printf '{"t":1,"seq":1,"v":0,"w":0,"ttl_ms":400,"scan":{"id":1,"action":"start","start_deg":-90,"end_deg":90,"step_deg":6,"rate_hz":20}}' | nc -u <rover-ip> 4210
   ```

2. **Decode the chunks.** Sniff UDP on the laptop. Every result datagram must start
   with `0x53`; `n_samples` must never exceed 64 (62 with the default optionals);
   `chunk_idx < chunk_count`; and the samples must decode per the byte tables above
   (`angle_dd`, `range_mm`, `status`, then the optional columns the flags advertise).
3. **Telemetry shows the sweep.** `scan_seq` equals the command `id` while it runs;
   `scan_state` is `"scanning"` and then `"idle"` when it finishes.
4. **Pull the laptop mid-sweep.** The motors must cut and telemetry must report
   `watchdog: true` within ~0.5 s. The sweep must abort (partial flush), never delay
   the trip.
5. **The control tick is undisturbed.** During a sweep, telemetry must keep arriving
   at 20 Hz with no gaps and the rover must keep answering commands at the normal
   cadence (v/w changes take effect immediately). Compare telemetry arrival jitter
   with and without a sweep; scope or serial-log the loop if you can.
6. **Wedged sensor.** Unplug the scan sensor (L1X path) and start a sweep: the sweep
   must complete with everything `status=0xFE` while `watchdog` stays false — a dead
   sensor must degrade the scan, not the loop.
7. **Malformed scan.** Send `"step_deg": 0` (or a `stop` without the other fields):
   the datagram must be rejected — no `scan_seq` change — and, if it is the only
   traffic, the motors must cut 400 ms after the last *valid* command.
8. **Sweep shape.** Watch the servo: it parks mid-range at boot, goes to `start_deg`
   on command, and steps across to `end_deg`.

---

## Safety design (read this before trusting the rover)

Two independent mechanisms, both **owned by the ESP32**, neither dependent on the
network being healthy:

### 1. Command watchdog (400 ms)

The loop runs every `RVR_TICK_MS` (20 ms). If the age of the last *accepted* command
exceeds `RVR_WATCHDOG_MS` (400 ms), the rover cuts the motors immediately
(`stopImmediately()`, no PWM ramp — a safety stop is deliberately deterministic) and
reports `"watchdog": true`. This holds even if WiFi is up but the laptop crashed, the
router dropped, or packets are being lost. It also covers the never-received-a-command
case at boot: the rover powers up with the motors cut.

### 2. Distance safety reflex (highest priority, after the watchdog)

Applied to the post-watchdog command every tick, before mixing:

| Condition | Action | Telemetry |
| --- | --- | --- |
| `dist_front_m < 0.18 m` and commanded `v > 0` | force `v = 0` (yaw `w` still allowed: you can rotate away) | `reflex: true` |
| `dist_front_m < 0.35 m` and `v > 0` | cap `v` to `0.15 m/s` | `reflex: true` |
| sensor read **failed** (init failed, VL53L0X timeout/no measurement, out-of-range) | stop **all** motion (`v = 0`, `w = 0`), unramped | `reflex: true` |

**A failed sensor read is treated as invalid, not as "far away":** the rover stops all
motion (including rotation) and raises `reflex`. It does not merely stop forward
motion, because an invalid front reading gives no information about which direction is
safe. Reverse (`v < 0`) is never blocked by the proximity reflex — that is the intended
escape path.

Reflex thresholds are `RVR_REFLEX_STOP_M` and `RVR_REFLEX_SLOW_M`; they live in the
firmware and are not remotely configurable by design. There is also **no compiled-in
bypass** for a failed front sensor, on purpose: a dead sensor keeps the rover parked.

### 3. Stopping behaviour

| Stop cause | How it stops |
| --- | --- |
| Watchdog trip, failed sensor read | `stopImmediately()` — PWM cut in the same tick |
| Proximity reflex (`v` forced to 0) | velocity slew limiter, braking side: ~120 ms from full speed |
| Laptop sends `v = 0`, `w = 0` | same slew limiter, gentle |

The slew limiter is intentionally asymmetric: `RVR_SLEW_UP_V_PER_TICK` (0.02/tick,
≈600 ms to full speed) makes starts smooth, while `RVR_SLEW_DOWN_V_PER_TICK`
(0.10/tick) brakes fast enough for a proximity reflex to actually work.

Neither mechanism can be blocked by a stalled sensor or a busy network: the watchdog
check and the reflex are plain arithmetic on the last known values, evaluated every
tick before anything touches the motor driver.

---

## Wiring notes (EXAMPLE VALUES — adapt all of these)

Configuration lives in one block at the top of `rover_esp32.ino`.

### Motor driver (TB6612FNG or L298N style)

Two direction pins + one PWM pin per side. TB6612 also has a `STBY`/enable pin — wire it
high or to a GPIO. L298N has an onboard 5 V regulator and no STBY.

| Signal | Example GPIO |
| --- | --- |
| Left PWM | 25 |
| Left IN1 / IN2 | 26, 27 |
| Right PWM | 33 |
| Right IN1 / IN2 | 32, 14 |
| TB6612 STBY (optional) | `-1` (unused — tie STBY to 3V3, or set a GPIO) |

If a side spins the wrong way, swap its two direction pins in the config — this is
faster and safer than editing the mixing code. `RVR_INVERT_LEFT` / `RVR_INVERT_RIGHT`
flip a whole side if your gearbox is mirrored.

> **Power:** never drive motors from the ESP32's regulator. Use a separate motor
> supply with a common ground to the ESP32. TB6612 `VM` tolerates roughly 2.5–13.5 V;
> L298N typically wants more headroom than a 2S pack provides at the low end. Match
> your pack to your driver and your motors.

### Distance sensor

Selected with `RVR_DISTANCE_SENSOR`. Default is VL53L0X over I2C. Rear sensing
(`RVR_ENABLE_REAR_SENSOR`, off by default) is implemented **only** for the HC-SR04
driver: two VL53L0X sensors share I2C address `0x29`, so combining the two fails at
compile time with an explanatory `#error` rather than silently misbehaving.

**VL53L0X (default)**

| Signal | Example |
| --- | --- |
| SDA | GPIO 21 |
| SCL | GPIO 22 |
| XSHUT | not used in single-sensor mode |

Requires the `Adafruit_VL53L0X` library (Library Manager → also pulls in `Adafruit BusIO`). Default I2C address `0x29`. The driver is initialized in `setup()`; if init fails the rover reports `dist_front_m: 0.0` with `reflex: true` and refuses to drive — a sensor that did not come up is treated as a dead sensor, not an absent one.

**HC-SR04 (fallback)**

Set `#define RVR_DISTANCE_SENSOR RVR_SENSOR_HCSR04` and wire TRIG/ECHO (example GPIO 5 and 17; GPIO 16/17 do not exist on ESP32-WROVER modules). Caveats:

- HC-SR04 runs at 5 V logic — **use a divider or level shifter on ECHO** before it reaches a 3.3 V ESP32 pin.
- `pulseIn` blocks for up to `RVR_US_TIMEOUT_US` (~25 ms) on a miss, which is longer than a 20 ms tick. The firmware tolerates this: the watchdog is evaluated from `millis()` before and after the read, so a slow sensor cannot silently extend the watchdog window.
- A pulse timeout means "no echo came back", i.e. nothing within ~4 m, which is *information*, not a failure. It is reported as `dist_front_m: 4.0` rather than invalid, otherwise the rover could never drive in the open. **The trade-off: a disconnected HC-SR04 also produces a timeout and therefore looks exactly like open space.** If that matters to you, use the VL53L0X (the default) or add a second check.

### Scan servo (HARDWARE-UNVERIFIED)

Only needed for [scan mode](#scan-mode-v1-hardware-unverified) — the rover drives fine
without it.

| Signal | Example |
| --- | --- |
| Servo signal | GPIO 4 (`RVR_SERVO_PIN`) |
| LEDC channel | 2 (`RVR_SERVO_CH` — the motors own 0 and 1) |

A standard analog servo, 500–2400 µs pulses at 50 Hz. The pulse endpoints are mapped
over the **command's** `start_deg…end_deg`, so a narrow sweep uses the servo's full
travel. Power the servo from a 5 V rail (not the ESP32's 3V3 regulator), share ground,
and couple the ToF rigidly to the servo horn.

> The sweep analysis offered GPIO 25 as an example pin, but 25 is `RVR_MOTOR_L_PWM` in
> this file's map, so the firmware ships GPIO 4 instead. GPIO 4 is the rear HC-SR04
> TRIG only when `RVR_ENABLE_REAR_SENSOR` + HC-SR04 are selected — pick any free
> output-capable pin and change `RVR_SERVO_PIN`.

### Encoders (optional)

Set `RVR_ENABLE_ENCODERS` to 1 and wire both channels. Example pins: left A/B = 18/19,
right A/B = 23/13. Channel A gets the interrupt, channel B is only read for direction,
so decoding is x1 — `RVR_ENCODER_CPR` is counts *as counted* per wheel revolution, and
a wrong value corrupts `v_meas`/`w_meas` telemetry only, never motion. GPIO 34–39 have no
internal pull-ups; if your encoder needs them, use pins that do (18/19/23/13 have them)
or fit external resistors. Avoid GPIO 34–39 for encoders for another reason: they are
input-only.

This decoding is good enough for telemetry, not for closed-loop control. There is no
PI controller on wheel speed — see "Known simplifications".

Set `RVR_ENABLE_ENCODERS` to 0 (the default) when no encoders are fitted:
`enc_l`/`enc_r` are then reported as `null` and `v_meas`/`w_meas` as `0.0`.

### Battery sense (optional)

ADC pin with a resistor divider. `RVR_BATT_DIVIDER` is the ratio
`(R_top + R_bottom) / R_bottom` — the factor that converts the measured pin voltage back
to pack voltage. Example: 100 kΩ over 33 kΩ gives `(100 + 33) / 33 ≈ 4.03`. Measure it,
do not trust the nominal resistor values.

`RVR_ENABLE_BATTERY` defaults to **0** (report `0.0`) because a floating ADC pin reads
noise — set it to 1 only once the divider is actually wired. The example pin (GPIO 34) is
on ADC1, which is required: ADC2 does not work while WiFi is active.

> **Check the divider before you connect it.** The ADC tap must stay below 3.3 V at the
> highest pack voltage, or the pin is damaged. Size for your worst case, not the nominal
> pack.

> ESP32 ADC is non-linear near the rails, so `batt_v` is fine for "is the pack sagging?"
> and not much more. Add a 100 nF cap from the ADC tap to ground if readings are jumpy.

---

## Getting the ESP32 on the network

The laptop needs the rover's IP to send commands. Two options:

1. **DHCP reservation** on your router for the ESP32's MAC. Recommended; no code change.
2. **Static IP** via `secrets.h` — uncomment `ROVER_STATIC_IP` there. Then the laptop
   can hardcode `192.168.4.20` (example) as the destination.

The rover does not advertise itself, and there is no discovery protocol. Whatever IP it
gets, put it in the laptop's config.

---

## Flashing

1. **Arduino IDE 2.x** (or `arduino-cli`) with the **esp32 board package** installed
   (Boards Manager → "esp32 by Espressif").
2. Install libraries via Library Manager:
   - `ArduinoJson` — **v7.x** (the code uses the v7 API; v6 will not compile cleanly).
   - `Adafruit_VL53L0X` and `Adafruit BusIO` — only if using the VL53L0X.
   - For the default **scan** sensor, the ST VL53L1X ULD API headers
     (`vl53l1_api.h`, `vl53l1_platform.h`). These are not in Library Manager; get them
     from ST (or adapt `rvrScanSensorBegin()` to whichever L1X library you have — it
     is the only function that touches the sensor). No L1X on your bench? Set
     `RVR_SCAN_SENSOR` to `RVR_SCAN_SENSOR_VL53L0X` and the sweep reuses the Adafruit
     front sensor (2 m range, explicitly best-effort).
3. Create your credentials file:
   ```sh
   cd rover_esp32
   cp secrets.h.example secrets.h
   # edit secrets.h with your SSID / password
   ```
4. **Gitignore `secrets.h`.** From the repo root:
   ```sh
   echo 'firmware/rover_esp32/secrets.h' >> .gitignore
   ```
   Confirm with `git status` that it is untracked before your first commit. If it was
   ever committed, rotate the password — it is in the history.
5. Open `rover_esp32.ino`. Sections 3 and 4 at the top hold every pin and tuning
   constant — they are all **example values**, so walk through them and fix the ones that
   do not match your wiring. Then set the feature switches in section 1 to match your
   car: `RVR_ENABLE_ENCODERS`, `RVR_ENABLE_BATTERY`, `RVR_ENABLE_REAR_SENSOR`
   (all default to 0) and `RVR_SCAN_SENSOR` (default VL53L1X, see above).
6. Select the board (`ESP32 Dev Module` or your actual variant), pick the port, upload.
7. Watch the serial monitor at **115200 baud** for the boot banner and IP address.

> **PWM API note:** this firmware uses `ledcSetup()` / `ledcAttachPin()` / `ledcWrite()`,
> which work on Arduino-ESP32 **2.x and 3.x**. On 3.x they are deprecated (warning only)
> in favour of `ledcAttach(pin, freq, resolution)`; the comment in `RvrMotor::begin()`
> says which two lines to replace.
>
> **L298N note:** lower `RVR_PWM_FREQ_HZ` to ~5000. The 20 kHz default is for the
> TB6612FNG, which switches fast enough to keep it inaudible.

### First test, wheels off the ground

- Power up with **no** laptop connected. The motors must be dead and telemetry idle
  (`watchdog`) — no command was ever received.
- Power up with the front sensor **unplugged** (VL53L0X default). The serial log must say
  `FRONT SENSOR INIT FAILED`, and once a laptop starts commanding forward motion the
  rover must refuse to move in **any** axis: `reflex: true`, `dist_front_m: 0.0`.
- Send one command, then stop sending. Within 400 ms the motors must stop and telemetry
  must show `watchdog: true`.
- Send garbage (`echo 'not json' | nc -u <rover-ip> 4210`). The rover must not move and
  must not treat it as a command.
- Put a hand (or a piece of card) ~0.15 m in front of the sensor and command `v = 0.3`.
  The wheels must not spin forward and telemetry must show `reflex: true`. Commanding
  `v = -0.3` must still work (reverse is the escape path).
- Move the card to ~0.30 m: forward motion is allowed but capped. Watch `v_meas` or the
  wheels — commanded forward speed must not exceed roughly `0.15 m/s`.
- Cover the sensor entirely / unplug it while driving: the rover must stop **all**
  motion (rotation included) and report `reflex: true`.
- If you fitted the scan servo/ToF: run the bench smoke checklist in
  [Scan mode (v1, hardware-unverified)](#scan-mode-v1-hardware-unverified) before
  trusting a sweep.

> **Bench-testing without a front sensor:** there is deliberately no compile-time bypass
> for a dead sensor (a bypass switch is a switch someone will leave off). If you must run
> the drivetrain with no sensor fitted, edit `RvrDistanceSensor::read()` temporarily —
> and remember that doing so removes the only thing standing between the car and a wall.

---

## Tuning cheat-sheet

All in the config block of `rover_esp32.ino`:

| Constant | Effect |
| --- | --- |
| `RVR_MAX_V`, `RVR_MAX_W` | Hard clamps. Protocol contract says 0.6 m/s and 2.1 rad/s — do not raise these without changing the laptop side. |
| `RVR_WATCHDOG_MS` | 400 ms per contract. Raise only if your link is genuinely worse than that. |
| `RVR_TICK_MS` | Control loop period. 20 ms = 50 Hz, comfortably faster than the laptop's 20 Hz. |
| `RVR_TELEM_MS` | Telemetry period. 50 ms = 20 Hz per contract. |
| `RVR_SLEW_UP_V_PER_TICK` | Max acceleration of a wheel target per tick. Lower = smoother, mushier starts. |
| `RVR_SLEW_DOWN_V_PER_TICK` | Max deceleration per tick. Keep this well above the up-rate so the reflex can stop the car. |
| `RVR_PWM_FULLSCALE_MPS` | Wheel speed that maps to 100% PWM. Set to what your car actually does flat out. |
| `RVR_PWM_RAMP_STEP` | Max PWM change per tick inside each motor. Lower = softer motor starts. |
| `RVR_PWM_DEADBAND` | PWM below this is emitted as 0 to avoid motor whine/buzz at standstill. Also the minimum controllable speed (deadband × full-scale). |
| `RVR_PWM_FREQ_HZ`, `RVR_PWM_RES_BITS` | PWM carrier and resolution. 20 kHz keeps it inaudible; L298N users should drop to ~5 kHz. |
| `RVR_TRACK_M`, `RVR_WHEEL_DIAM_M` | Geometry: wheel separation for mixing and `w_meas`, wheel diameter for encoder distance. |
| `RVR_ENCODER_CPR` | Counts per wheel revolution (x1, as counted). Measure it; a wrong value only corrupts `*_meas` telemetry, not motion. |
| `RVR_REFLEX_STOP_M`, `RVR_REFLEX_SLOW_M`, `RVR_REFLEX_SLOW_V` | Reflex thresholds and cap, 0.18 m / 0.35 m / 0.15 m/s per contract. |
| `RVR_REFLEX_HOLD_MS` | How long the `reflex` flag stays true after an intervention (observability). 0 = strict per-tick. |
| `RVR_BATT_DIVIDER`, `RVR_BATT_CAL` | Divider ratio and a trim multiplier for `batt_v`. |
| `RVR_HCSR04_NO_ECHO_M` | Distance reported when an HC-SR04 sees no echo. Lower it if your car must stop when it can "see" nothing within 4 m. |
| `RVR_SCAN_GRID_MS` | Minimum spacing between scan operations (interleave rule 4). Lower = more scan CPU; do not go below 5. |
| `RVR_SCAN_BUDGET_MS` | L1X timing budget (33 ms default; ≥33 ms works in all distance modes, 140 ms for the full 4 m). Drives the 1.5× timeout. |
| `RVR_SCAN_TIMEOUT_MS` | Derived: 1.5× the budget. A sample not ready by then is recorded as `status=0xFE`. |
| `RVR_SCAN_OPT_SIGNAL`, `RVR_SCAN_OPT_AMBIENT`, `RVR_SCAN_OPT_T_US` | Compile-time optional sample columns (default 1/1/0). They change the per-sample size and therefore samples per chunk (8 B → 62/chunk). |
| `RVR_SCAN_CHUNK_HARD`, `RVR_SCAN_DATAGRAM_CAP` | Spec hard cap (64 samples) and the 512 B policy cap; the effective chunk size is derived and `static_assert`ed. |
| `RVR_SCAN_MAX_SAMPLES` | Sweep-size guard, 181 = ±90° at 1° steps. |
| `RVR_SERVO_HZ`, `RVR_SERVO_RES_BITS` | Servo frame rate (50 Hz) and LEDC resolution (16 bits ≈ 0.3 µs duty steps). |
| `RVR_SERVO_PULSE_MIN_US`, `RVR_SERVO_PULSE_MAX_US` | Pulse endpoints, mapped over the commanded `start_deg…end_deg`. |
| `RVR_SERVO_PARK_US` | Pulse written at boot, before any sweep (mid-range). |
| `RVR_SERVO_PIN`, `RVR_SERVO_CH` | Servo GPIO and LEDC channel; see "Wiring notes". |
| `RVR_SCAN_I2C_ADDR` | Address the L1X scan sensor is expected to answer on (see the scan sensor caveats). |

---

## Known simplifications

- **Open loop.** Encoder counts feed telemetry only; there is no PI controller on wheel
  speed. Add one inside `RvrDrive::update()` if you need it.
- **No acknowledgements, no retries, no session.** UDP is fire-and-forget by design; the
  watchdog, not the transport, is the reliability mechanism.
- **No authentication, no encryption.** Anything on the same network that knows the port
  can drive the car; there is no pairing, no token, no replay protection. Treat the rover
  as a LAN toy on a network you control, and never put the port on the open internet.
- **Single client.** Telemetry goes to the most recent valid command source, forever (it
  is never expired, per the protocol). A second laptop silently steals the telemetry
  stream; whoever commands last wins.
- **The reflex only watches the front sensor.** A rear sensor is telemetry-only: the
  firmware will happily reverse into something.
- **Scan mode is hardware-unverified.** The L1X glue, servo numbers and timing
  constants are examples to validate; the L0X fallback cannot honour the non-blocking
  interleave rules. See "Scan mode (v1, hardware-unverified)".
- **Scans are fire-and-forget.** No acknowledgements or retries; a missing chunk is
  detectable via `chunk_idx`/`chunk_count`, and an aborted sweep sends what it has with
  the partial bit rather than retrying.
- **The reflex acts on commands, not on physics.** It sets `v = 0` in the control path,
  so a car already rolling fast on a slippery surface still needs the braking distance
  the slew limiter gives it. Keep `RVR_PWM_FULLSCALE_MPS` honest.
- **`w` sign convention.** Positive `w` is intended as counter-clockwise, but this
  depends on your motor polarity. Verify on blocks and flip `RVR_INVERT_LEFT`/`_RIGHT`
  or the mixing signs if it is backwards.
- **No OTA, no config persistence.** Reflash to change constants.

---

## Defaults to review before flashing

`RVR_MOTOR_STBY` is `-1` by default (TB6612 users: either tie STBY to 3V3 or set a GPIO).
`RVR_INVERT_LEFT`/`RVR_INVERT_RIGHT` are both `false`. `RVR_SERVO_PIN` is GPIO 4 and
`RVR_SERVO_CH` is 2 for the optional scan servo — check both against your wiring (and
against whatever pins you keep for the motors and sensors). The example pin map is
conflict-free for the default configuration but shares nothing with your car — read
section 3 of the sketch before flashing.
