# Jev Rover — Reference ESP32 Firmware

Reference firmware for a small differential-drive rover driven over WiFi (UDP) from a
laptop. It implements a fixed wire protocol, a 400 ms command watchdog, and an
onboard distance-sensor safety reflex.

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

Invalid JSON, missing required fields (`t`, `seq`, `v`, `w`), non-finite numbers, a
non-integral `seq`, or values out of the hard clamp range are **rejected** — a rejected
datagram does **not** feed the watchdog and does **not** become the telemetry
destination. It also does not stop the rover: the existing watchdog deadline just keeps
running, so a stream of garbage silences the motors 400 ms after the last *good*
command.

### Telemetry datagram (rover → laptop)

UTF-8 JSON, sent at 20 Hz while a telemetry destination is known.

```json
{"t": 12.34, "seq": 512, "dist_front_m": 0.42, "dist_rear_m": null,
 "enc_l": 1203, "enc_r": 1198, "v_meas": 0.33, "w_meas": -0.38,
 "batt_v": 7.41, "reflex": false, "watchdog": false, "uptime_s": 83.2}
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
   do not match your wiring. Then set the three feature switches in section 1 to match
   your car: `RVR_ENABLE_ENCODERS`, `RVR_ENABLE_BATTERY`, `RVR_ENABLE_REAR_SENSOR`
   (all default to 0).
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
`RVR_INVERT_LEFT`/`RVR_INVERT_RIGHT` are both `false`. The example pin map is
conflict-free for the default configuration but shares nothing with your car — read
section 3 of the sketch before flashing.
