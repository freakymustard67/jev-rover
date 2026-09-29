/* =====================================================================
 * rover_esp32.ino
 *
 * Reference firmware for a small differential-drive rover commanded over
 * WiFi/UDP from a laptop.
 *
 * ---------------------------------------------------------------------
 * WHAT THIS IS
 * ---------------------------------------------------------------------
 * A single-file ESP32 sketch that implements the fixed UDP protocol on
 * port 4210 (see firmware/README.md for the protocol tables):
 *
 *   laptop -> rover : {"t","seq","v","w","ttl_ms"}      ~20 Hz
 *   rover -> laptop : {"t","seq","dist_front_m", ...}   ~20 Hz
 *
 * ---------------------------------------------------------------------
 * SAFETY DESIGN (the two things that matter)
 * ---------------------------------------------------------------------
 * 1) WATCHDOG.  If no valid command has been accepted for
 *    RVR_WATCHDOG_MS (400 ms), the motors are cut immediately and
 *    telemetry reports "watchdog": true. This also covers the state
 *    before the first command ever arrives. It is pure arithmetic on
 *    millis() and cannot be blocked by a dead laptop, a dead router, or
 *    packet loss.
 *
 * 2) REFLEX.  Code-owned and higher priority than the laptop. Evaluated
 *    once per control tick against the post-watchdog command, before
 *    anything touches the motor driver:
 *      - dist_front_m < 0.18 m and commanded v > 0  -> force v = 0
 *      - dist_front_m < 0.35 m and commanded v > 0  -> cap v to 0.15 m/s
 *      - front sensor read failed / not initialised  -> stop ALL motion
 *        (v = 0 AND w = 0) and set reflex = true.
 *    A failed read is treated as INVALID, never as "far away": an
 *    invalid reading carries no information about which direction is
 *    safe, so the rover holds still. Reverse (v < 0) is not blocked by
 *    the proximity reflex -- backing away is the escape path.
 *
 * Neither mechanism depends on the sensor or the network being healthy:
 * both are evaluated every tick from the last known values.
 *
 * ---------------------------------------------------------------------
 * OPTIONAL SERVO TOF SCAN  (v1, HARDWARE-UNVERIFIED; see README)
 * ---------------------------------------------------------------------
 * A commanded servo sweep of a time-of-flight sensor ("scan"). It is
 * serviced from loop() one bounded step at a time and never weakens the
 * two mechanisms above: the scan path cannot block the loop (no wait
 * functions), performs at most one bounded I2C burst and one servo PWM
 * write per pass, and aborts the moment the watchdog trips. The interleave
 * rules and the wire format are in firmware/README.md, "Scan mode".
 *
 * ---------------------------------------------------------------------
 * THIS IS REFERENCE FIRMWARE
 * ---------------------------------------------------------------------
 * Every pin and tuning constant in section 2 below is an EXAMPLE VALUE
 * chosen to be plausible, not to match your board. Adapt them to your
 * wiring before flashing. The protocol is the only hard contract.
 * See firmware/README.md for wiring, flashing, and behaviour notes.
 *
 * Libraries: WiFi.h / WiFiUdp.h (ESP32 core), ArduinoJson v7,
 *            Adafruit_VL53L0X (+ Adafruit BusIO) when using the default
 *            distance sensor, and the ST ULD VL53L1X API headers when
 *            using the default scan sensor. No other dependencies.
 * ===================================================================== */

// =====================================================================
//  1. HARDWARE SELECTION  (must be defined before the #includes)
// =====================================================================

#define RVR_SENSOR_VL53L0X 1
#define RVR_SENSOR_HCSR04  2

// EXAMPLE default: a VL53L0X time-of-flight sensor on I2C.
// Switch to RVR_SENSOR_HCSR04 for the HC-SR04 (pulseIn) fallback.
#define RVR_DISTANCE_SENSOR RVR_SENSOR_VL53L0X

// --- scan sensor (servo ToF sweep; HARDWARE-UNVERIFIED, see README) ---
#define RVR_SCAN_SENSOR_VL53L1X 1   // ST ULD API shape; 4 m class; non-blocking ready poll
#define RVR_SCAN_SENSOR_VL53L0X 2   // Adafruit VL53L0X (the front sensor); 2 m; best effort

// Which ToF the servo sweep reads.
//   VL53L1X (default): needs the ST ULD API headers (section 2). This is
//     the only path that can honour the non-blocking interleave rules.
//   VL53L0X: reuses the Adafruit VL53L0X front-sensor driver; requires
//     RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X (checked below). The
//     Adafruit API has no readiness poll, so that path is bench-only and
//     its scan timing guarantees are weaker. Its 2.0 m validity applies;
//     the 4 m numbers do not.
#define RVR_SCAN_SENSOR RVR_SCAN_SENSOR_VL53L1X

// Rear distance is optional and disabled by default. It is only
// implemented for the HC-SR04 driver: two VL53L0X sensors share one I2C
// address and would need XSHUT re-addressing, which this reference
// firmware deliberately does not do.
#define RVR_ENABLE_REAR_SENSOR 0

// Set to 1 once quadrature encoders are fitted (see section 3 for pins).
// With 0, telemetry reports enc_l/enc_r as null and v_meas/w_meas as 0.0.
#define RVR_ENABLE_ENCODERS 0

// Set to 1 once the battery divider is actually wired to the ADC pin.
// With 0, batt_v is reported as 0.0. (A floating ADC pin reads noise, so
// 0 is the honest default for a car without a divider.)
#define RVR_ENABLE_BATTERY 0

// Wireless firmware updates (ArduinoOTA). The FIRST flash must be over USB;
// after that the rover accepts updates on the LAN (mDNS name ROVER_HOSTNAME,
// default `jev-rover.local`, espota port 3232). The watchdog keeps the motors
// cut during a flash unless valid commands keep arriving, so do not flash
// while driving. See "Wireless updates (OTA)" in firmware/README.md.
#define RVR_OTA 1

#if RVR_ENABLE_REAR_SENSOR && (RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X)
#error "RVR_ENABLE_REAR_SENSOR=1 needs the HC-SR04 driver: two VL53L0X sensors share I2C address 0x29 and re-addressing via XSHUT is not implemented. Set RVR_ENABLE_REAR_SENSOR to 0, or set RVR_DISTANCE_SENSOR to RVR_SENSOR_HCSR04."
#endif

#if (RVR_SCAN_SENSOR != RVR_SCAN_SENSOR_VL53L1X) && (RVR_SCAN_SENSOR != RVR_SCAN_SENSOR_VL53L0X)
#error "RVR_SCAN_SENSOR must be RVR_SCAN_SENSOR_VL53L1X or RVR_SCAN_SENSOR_VL53L0X."
#endif

// The L0X scan fallback is just the Adafruit VL53L0X front driver, so it
// can only be compiled when that driver is selected for the front sensor.
#if (RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L0X) && (RVR_DISTANCE_SENSOR != RVR_SENSOR_VL53L0X)
#error "RVR_SCAN_SENSOR_VL53L0X reuses the Adafruit VL53L0X front-sensor driver, which is only compiled in when RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X. Either set RVR_DISTANCE_SENSOR to RVR_SENSOR_VL53L0X, or use RVR_SCAN_SENSOR_VL53L1X for the sweep."
#endif

#if RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L0X
#warning "Scan fallback: the Adafruit VL53L0X has no non-blocking readiness poll, so the scan interleave guarantees are weaker (bench use only; see README)."
#endif

// =====================================================================
//  2. INCLUDES
// =====================================================================

#include <Arduino.h>
#include <math.h>

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>   // REQUIRES v7.x
#include <Wire.h>

#if RVR_OTA
  #include <ArduinoOTA.h>
#endif

#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
  #include <Adafruit_VL53L0X.h>
#endif

#if RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L1X
  // HARDWARE-UNVERIFIED: the ST ULD VL53L1X API. Ports differ in header
  // names and platform bring-up; section 9 uses the function names from
  // UM2356 and isolates every hardware call in three small functions.
  #include <vl53l1_api.h>
  #include <vl53l1_platform.h>
#endif

// Credentials live in secrets.h, which must NOT be committed. Copy
// secrets.h.example to secrets.h and fill it in.
#if defined(__has_include)
  #if __has_include("secrets.h")
    #include "secrets.h"
  #else
    #error "secrets.h not found. Copy firmware/rover_esp32/secrets.h.example to secrets.h in this same folder and fill in your SSID/password."
  #endif
#else
  #include "secrets.h"
#endif

// OTA defaults for secrets.h files written before the OTA fields existed.
#ifndef ROVER_OTA_PASSWORD
  #define ROVER_OTA_PASSWORD ""
#endif
#ifndef ROVER_HOSTNAME
  #define ROVER_HOSTNAME "jev-rover"
#endif

// =====================================================================
//  3. PIN MAP  ---  ALL VALUES ARE EXAMPLES. CHANGE THEM.
// =====================================================================
// This map is conflict-free for the default configuration on a 38-pin
// ESP32 DevKitC. It avoids the flash pins (6-11), the strapping pins
// (0, 2, 12, 15) and the serial pins (1, 3). Remap freely: nothing below
// is a requirement, only a suggestion.

// --- motor driver (TB6612FNG or L298N style: 2 direction pins + PWM) ---
#define RVR_MOTOR_L_PWM 25   // left  PWM
#define RVR_MOTOR_L_IN1 26   // left  direction A
#define RVR_MOTOR_L_IN2 27   // left  direction B
#define RVR_MOTOR_R_PWM 33   // right PWM
#define RVR_MOTOR_R_IN1 32   // right direction A
#define RVR_MOTOR_R_IN2 14   // right direction B

// TB6612FNG STBY pin. Set to -1 when there is none (L298N), or wire STBY
// to 3V3. If you want software control, set this to a free GPIO.
#define RVR_MOTOR_STBY (-1)

// --- scan servo (servo ToF sweep; HARDWARE-UNVERIFIED) ---
// A standard analog servo (SG90/MG90S class) on its own LEDC channel. The
// motors own LEDC channels 0 and 1 (see RvrDrive::begin), so the servo must
// use another one. The pulse endpoints are mapped over the COMMAND's
// start_deg..end_deg (section 4), not over the servo's mechanical range.
//
// Pin note: the sweep analysis offered GPIO 25 as an example, but 25 is
// RVR_MOTOR_L_PWM in this map, so this file uses GPIO 4 instead -- free in
// the default configuration (it is only the rear HC-SR04 TRIG pin when
// RVR_ENABLE_REAR_SENSOR and the HC-SR04 are selected). Any free
// output-capable GPIO works.
#define RVR_SERVO_PIN 4
#define RVR_SERVO_CH  2

// I2C address the scan sensor is expected to answer on. A VL53L1X and a
// VL53L0X both power up on 0x29: if both sit on this bus, one of them must
// be re-addressed (XSHUT); this reference firmware does not do that for you.
#define RVR_SCAN_I2C_ADDR 0x29

// --- VL53L0X over I2C (default distance sensor) ---
#define RVR_I2C_SDA 21
#define RVR_I2C_SCL 22
#define RVR_VL53L0X_ADDR 0x29

// --- HC-SR04 (fallback distance sensor) ---
// NOTE: 5 V logic. Put a divider or level shifter on ECHO. GPIO 16/17 are
// unavailable on ESP32-WROVER modules (PSRAM uses them).
#define RVR_HCSR04_TRIG_PIN 5
#define RVR_HCSR04_ECHO_PIN 17
#define RVR_HCSR04_REAR_TRIG_PIN 4    // only used if RVR_ENABLE_REAR_SENSOR
#define RVR_HCSR04_REAR_ECHO_PIN 16   // only used if RVR_ENABLE_REAR_SENSOR

// --- encoders (optional) ---
// Channel A gets the interrupt, channel B is only read for direction, so
// this is x1 decoding: RVR_ENCODER_CPR counts *as counted* per wheel
// revolution. Calibrate it by hand if v_meas/w_meas matter to you.
// GPIO 34-39 have no internal pull-ups: use external ones if needed.
#define RVR_ENC_L_A 18
#define RVR_ENC_L_B 19
#define RVR_ENC_R_A 23
#define RVR_ENC_R_B 13

// --- battery sense (optional) ---
// ADC tap of a resistor divider. MUST stay below 3.3 V at the highest
// pack voltage, otherwise the pin is damaged. Use an ADC1 pin (32-39):
// ADC2 does not work while WiFi is active.
#define RVR_BATT_ADC_PIN 34

// =====================================================================
//  4. TUNING CONSTANTS  ---  values are EXAMPLE VALUES
// =====================================================================

// --- loop timing ---
constexpr uint32_t RVR_TICK_MS  = 20;   // control loop period, 50 Hz
constexpr uint32_t RVR_TELEM_MS = 50;   // telemetry period, 20 Hz
constexpr uint32_t RVR_WATCHDOG_MS = 400;  // protocol contract: do not raise casually

// --- protocol clamps (hard contract with the laptop software) ---
constexpr float RVR_MAX_V = 0.6f;   // m/s
constexpr float RVR_MAX_W = 2.1f;   // rad/s
constexpr float RVR_CMD_SLACK = 0.001f;  // rounding tolerance on the clamps

// --- proximity reflex (hard contract) ---
constexpr float RVR_REFLEX_STOP_M = 0.18f;  // closer than this, v>0 is forced to 0
constexpr float RVR_REFLEX_SLOW_M = 0.35f;  // closer than this, v>0 is capped
constexpr float RVR_REFLEX_SLOW_V = 0.15f;  // the cap, m/s

// The reflex flag is latched for this long so a single 20 ms intervention
// is still visible in 20 Hz telemetry. This latches the FLAG ONLY: motion
// gating is recomputed every tick. Set to 0 for strict per-tick reporting.
constexpr uint32_t RVR_REFLEX_HOLD_MS = 300;

// --- geometry and drivetrain ---
constexpr float RVR_TRACK_M = 0.15f;         // wheel-to-wheel separation, m
constexpr float RVR_WHEEL_DIAM_M = 0.065f;   // wheel diameter, m
constexpr float RVR_ENCODER_CPR = 1200.0f;   // encoder counts per wheel revolution (x1)
constexpr bool  RVR_INVERT_LEFT  = false;    // flip a side if its gearbox is mirrored
constexpr bool  RVR_INVERT_RIGHT = false;

// Speed that maps to 100% PWM. Set this to what the car actually does at
// full throttle; the protocol clamp (RVR_MAX_V) is applied separately.
constexpr float RVR_PWM_FULLSCALE_MPS = 0.6f;

// Slew limiter on wheel targets. Asymmetric on purpose: accelerate
// gently, brake decisively (a proximity reflex must actually stop in
// time, not take 600 ms to wind down).
constexpr float RVR_SLEW_UP_V_PER_TICK   = 0.02f;   // 0.6 m/s in ~600 ms
constexpr float RVR_SLEW_DOWN_V_PER_TICK = 0.10f;   // 0.6 m/s -> 0 in ~120 ms

// --- PWM ---
constexpr uint32_t RVR_PWM_FREQ_HZ  = 20000;  // above audible range; L298N users: use ~5000
constexpr uint8_t  RVR_PWM_RES_BITS = 10;
constexpr uint32_t RVR_PWM_MAX = (1u << RVR_PWM_RES_BITS) - 1u;
constexpr float RVR_PWM_RAMP_STEP = 0.05f;   // max PWM change per tick (~1.0 -> 0 in 400 ms)
constexpr float RVR_PWM_DEADBAND  = 0.06f;   // below this, output is 0 (no motor whine)

// --- sensor validity window ---
// A reading below MIN or above MAX is treated as INVALID (no information).
// Each driver gets its own upper bound: the VL53L0X stops being trustworthy
// past ~2 m, while the HC-SR04's timeout-derived maximum is ~4.3 m.
constexpr float RVR_SENSOR_MIN_M = 0.02f;     // closer than this is a bogus reading
constexpr float RVR_VL53L0X_MAX_M = 2.0f;     // VL53L0X useful range
constexpr float RVR_HCSR04_MAX_M = 4.5f;      // HC-SR04 timeout-derived maximum
constexpr float RVR_SPEED_OF_SOUND_MPS = 343.0f;
constexpr uint32_t RVR_US_TIMEOUT_US = 25000;   // HC-SR04 round-trip timeout (~4 m)
constexpr float RVR_HCSR04_NO_ECHO_M = 4.0f;    // reported when the echo times out (must stay < RVR_HCSR04_MAX_M)
constexpr uint16_t RVR_VL53L0X_INVALID_MM = 8190;  // Adafruit driver's "no reading" value

// --- scan: servo ToF sweep (v1; HARDWARE-UNVERIFIED) ---
// See README "Scan mode (v1, hardware-unverified)" for the wire format and
// the interleave rules these constants implement. Every value here is an
// example to validate on your bench.
constexpr uint32_t RVR_SCAN_GRID_MS = 5;      // >= this between two scan ops (rule 4)
constexpr uint32_t RVR_SCAN_BUDGET_MS = 33;   // VL53L1X timing budget; >=33 ms works in all distance modes
constexpr uint32_t RVR_SCAN_TIMEOUT_MS = (RVR_SCAN_BUDGET_MS * 3u + 1u) / 2u;  // 1.5x budget -> status 0xFE
constexpr uint16_t RVR_SCAN_MAX_SAMPLES = 181;  // -90..+90 deg at 1 deg steps
constexpr uint16_t RVR_SCAN_DATAGRAM_CAP = 512; // policy cap for result datagrams
constexpr uint8_t  RVR_SCAN_CHUNK_HARD = 64;    // spec hard max: samples per datagram

// Compile-time optional sample columns (protocol v1 ver_flags bits 2..4).
// These are #defines on purpose: they are consumed by #if sections in the
// sample staging and sensor code, which a `constexpr` cannot reach.
// Default: signal + ambient (8 B/sample). t_us is compile-time off.
#define RVR_SCAN_OPT_SIGNAL  1
#define RVR_SCAN_OPT_AMBIENT 1
#define RVR_SCAN_OPT_T_US    0

// Protocol constants (v1).
constexpr uint8_t RVR_SCAN_MSG = 0x53;      // 'S' first byte of a chunk
constexpr uint8_t RVR_SCAN_VERSION = 1;     // ver_flags bits 0-1
constexpr uint8_t RVR_SCAN_FLAG_HAS_SIGNAL  = 0x04;
constexpr uint8_t RVR_SCAN_FLAG_HAS_AMBIENT = 0x08;
constexpr uint8_t RVR_SCAN_FLAG_HAS_T_US    = 0x10;
constexpr uint8_t RVR_SCAN_FLAG_PARTIAL     = 0x20;  // sweep aborted
constexpr uint8_t RVR_SCAN_FLAG_TIMEOUTS    = 0x40;  // any 0xFE in this chunk

// Per-sample size: angle_dd i8 + range_mm u16 + status u8 [+ optionals].
constexpr uint8_t RVR_SCAN_SAMPLE_BYTES =
    4u + 2u * RVR_SCAN_OPT_SIGNAL + 2u * RVR_SCAN_OPT_AMBIENT + 2u * RVR_SCAN_OPT_T_US;

// Effective chunk size: the 512 B datagram cap can cut 64 down (e.g. to 62
// with signal+ambient on), but never below 1.
constexpr uint8_t RVR_SCAN_CHUNK_MAX =
    (((RVR_SCAN_DATAGRAM_CAP - 16u) / RVR_SCAN_SAMPLE_BYTES) < RVR_SCAN_CHUNK_HARD)
        ? (uint8_t)((RVR_SCAN_DATAGRAM_CAP - 16u) / RVR_SCAN_SAMPLE_BYTES)
        : RVR_SCAN_CHUNK_HARD;

// Staging buffer sized for the spec's worst case (16 B header + 64 x 10 B).
constexpr size_t RVR_SCAN_BUF_BYTES = 16u + (size_t)RVR_SCAN_CHUNK_HARD * 10u;

// Result status codes (protocol v1).
constexpr uint8_t RVR_SCAN_STATUS_TIMEOUT = 0xFE;  // sample not ready in 1.5x budget
constexpr uint8_t RVR_SCAN_STATUS_ABORTED = 0xFF;  // sweep aborted mid-slot

static_assert(RVR_SCAN_OPT_SIGNAL <= 1 && RVR_SCAN_OPT_AMBIENT <= 1 && RVR_SCAN_OPT_T_US <= 1,
              "scan optional-column switches are boolean");
static_assert(RVR_SCAN_CHUNK_MAX >= 1 && RVR_SCAN_CHUNK_MAX <= RVR_SCAN_CHUNK_HARD,
              "scan chunk size out of range");
static_assert(16u + (size_t)RVR_SCAN_CHUNK_MAX * RVR_SCAN_SAMPLE_BYTES <= RVR_SCAN_DATAGRAM_CAP,
              "scan result datagram would exceed the 512 B policy cap");

// --- scan servo (HARDWARE-UNVERIFIED) ---
constexpr uint32_t RVR_SERVO_HZ = 50;             // analog hobby-servo frame rate
constexpr uint8_t  RVR_SERVO_RES_BITS = 16;       // ~0.3 us duty steps at 50 Hz
constexpr uint32_t RVR_SERVO_PULSE_MIN_US = 500;  // pulse at start_deg
constexpr uint32_t RVR_SERVO_PULSE_MAX_US = 2400; // pulse at end_deg
constexpr uint32_t RVR_SERVO_PERIOD_US = 1000000u / RVR_SERVO_HZ;
constexpr uint32_t RVR_SERVO_PARK_US = (RVR_SERVO_PULSE_MIN_US + RVR_SERVO_PULSE_MAX_US) / 2u;

// Minimum time one sample slot may take. The L1X paces itself with the
// timing budget; the L0X fallback has no readiness poll, so its slots are
// paced by the budget instead of recording the same measurement repeatedly.
// RVR_SCAN_HAS_SAMPLE_MIN exists because #if cannot test a constexpr.
#if RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L0X
  #define RVR_SCAN_HAS_SAMPLE_MIN 1
  constexpr uint32_t RVR_SCAN_SAMPLE_MIN_MS = RVR_SCAN_BUDGET_MS;
#else
  #define RVR_SCAN_HAS_SAMPLE_MIN 0
  constexpr uint32_t RVR_SCAN_SAMPLE_MIN_MS = 0u;
#endif

// --- battery divider ---
// RVR_BATT_DIVIDER = (R_top + R_bottom) / R_bottom. Example: 100k over
// 33k -> (100+33)/33 = 4.03. MEASURE the tap voltage, do not trust
// nominal resistor values.
constexpr float RVR_BATT_DIVIDER = 4.03f;
constexpr float RVR_BATT_CAL = 1.0f;        // fine trim, 1.0 = leave alone
constexpr float RVR_BATT_ALPHA = 0.2f;      // EMA filter, 0..1
constexpr float RVR_ADC_REF_V = 3.3f;       // nominal; the ESP32 ADC is not precise
constexpr float RVR_ADC_COUNTS = 4095.0f;

// --- housekeeping ---
constexpr uint16_t RVR_UDP_PORT = 4210;
constexpr uint8_t  RVR_MAX_PACKETS_PER_TICK = 4;  // don't let a flood starve control
constexpr uint32_t RVR_SERIAL_BAUD = 115200;
constexpr uint32_t RVR_WIFI_TIMEOUT_MS = 30000;
constexpr size_t   RVR_RX_BUF = 256;
constexpr size_t   RVR_TX_BUF = 384;

// =====================================================================
//  5. SMALL HELPERS
// =====================================================================

static inline float rvrClamp(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

// =====================================================================
//  6. MOTOR  (swap this class if your driver is not TB6612FNG/L298N style)
// =====================================================================

class RvrMotor {
public:
  // `invert` flips the whole side electrically-mirrored gearboxes.
  void begin(uint8_t pwmPin, uint8_t in1Pin, uint8_t in2Pin, uint8_t ledcChannel, bool invert) {
    _pwmPin = pwmPin;
    _in1 = in1Pin;
    _in2 = in2Pin;
    _ch = ledcChannel;
    _invert = invert;
    _ramped = 0.0f;

    pinMode(_in1, OUTPUT);
    pinMode(_in2, OUTPUT);
    digitalWrite(_in1, LOW);
    digitalWrite(_in2, LOW);

    // NOTE: Arduino-ESP32 core 3.x deprecates ledcSetup()/ledcAttachPin()
    // in favour of ledcAttach(pin, freq, res). Both work on 3.x; on 3.x
    // you may replace the two calls below with ledcAttach(_pwmPin,
    // RVR_PWM_FREQ_HZ, RVR_PWM_RES_BITS).
    ledcSetup(_ch, RVR_PWM_FREQ_HZ, RVR_PWM_RES_BITS);
    ledcAttachPin(_pwmPin, _ch);
    ledcWrite(_ch, 0);
  }

  // norm in [-1, +1]. The output ramps toward it by at most
  // RVR_PWM_RAMP_STEP per call, so the car never jerks into motion.
  void apply(float norm) {
    norm = rvrClamp(norm, -1.0f, 1.0f);
    if (_invert) norm = -norm;
    _ramped += rvrClamp(norm - _ramped, -RVR_PWM_RAMP_STEP, RVR_PWM_RAMP_STEP);
    writeOutput(_ramped);
  }

  // Immediate, unramped stop. Deliberately bypasses the ramp: a safety
  // stop must be deterministic and independent of ramp state, and an
  // instant PWM cut is a coast, not a mechanical shock.
  void hardStop() {
    _ramped = 0.0f;
    writeOutput(0.0f);
  }

private:
  void writeOutput(float norm) {
    float mag = fabsf(norm);
    if (mag < RVR_PWM_DEADBAND) mag = 0.0f;   // avoid buzz at standstill

    // TB6612FNG / L298N truth table: IN1 PWM'd, IN2 LOW -> forward;
    // IN1 LOW, IN2 PWM'd -> reverse. Both LOW -> coast.
    digitalWrite(_in1, (norm > 0.0f) ? HIGH : LOW);
    digitalWrite(_in2, (norm < 0.0f) ? HIGH : LOW);

    uint32_t duty = (uint32_t)(mag * (float)RVR_PWM_MAX + 0.5f);
    if (duty == 0u) {
      digitalWrite(_in1, LOW);
      digitalWrite(_in2, LOW);
    }
    ledcWrite(_ch, duty);
  }

  uint8_t _pwmPin = 0;
  uint8_t _in1 = 0;
  uint8_t _in2 = 0;
  uint8_t _ch = 0;
  bool    _invert = false;
  float   _ramped = 0.0f;
};

// =====================================================================
//  7. DIFFERENTIAL DRIVE  (mixing + slew limiter + PWM ramping)
// =====================================================================

class RvrDrive {
public:
  void begin() {
    _left.begin(RVR_MOTOR_L_PWM, RVR_MOTOR_L_IN1, RVR_MOTOR_L_IN2, 0, RVR_INVERT_LEFT);
    _right.begin(RVR_MOTOR_R_PWM, RVR_MOTOR_R_IN1, RVR_MOTOR_R_IN2, 1, RVR_INVERT_RIGHT);
#if RVR_MOTOR_STBY >= 0
    pinMode(RVR_MOTOR_STBY, OUTPUT);
    digitalWrite(RVR_MOTOR_STBY, HIGH);
#endif
    _targetL = _targetR = _slewedL = _slewedR = 0.0f;
  }

  // Body-frame setpoint -> wheel targets. Positive w is intended as
  // counter-clockwise (left turn); verify on blocks and flip RVR_INVERT_*
  // or the signs here if your car turns the other way.
  void setTarget(float v, float w) {
    const float halfTrack = RVR_TRACK_M * 0.5f;
    const float maxWheel = RVR_MAX_V + RVR_MAX_W * halfTrack;
    _targetL = rvrClamp(v - w * halfTrack, -maxWheel, maxWheel);
    _targetR = rvrClamp(v + w * halfTrack, -maxWheel, maxWheel);
  }

  // Slew-limit the wheel targets, then hand them to the motors.
  void update() {
    _slewedL += rvrClamp(_targetL - _slewedL, -RVR_SLEW_DOWN_V_PER_TICK, RVR_SLEW_UP_V_PER_TICK);
    _slewedR += rvrClamp(_targetR - _slewedR, -RVR_SLEW_DOWN_V_PER_TICK, RVR_SLEW_UP_V_PER_TICK);
    _left.apply(_slewedL / RVR_PWM_FULLSCALE_MPS);
    _right.apply(_slewedR / RVR_PWM_FULLSCALE_MPS);
  }

  // Used by the watchdog and by an invalid front sensor reading.
  void stopImmediately() {
    _targetL = _targetR = _slewedL = _slewedR = 0.0f;
    _left.hardStop();
    _right.hardStop();
  }

private:
  RvrMotor _left;
  RvrMotor _right;
  float _targetL = 0.0f;
  float _targetR = 0.0f;
  float _slewedL = 0.0f;
  float _slewedR = 0.0f;
};

// =====================================================================
//  8. DISTANCE SENSOR ABSTRACTION
// =====================================================================

#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
static Adafruit_VL53L0X g_lox;
#endif

class RvrDistanceSensor {
public:
  // Returns false when the sensor did not come up. A sensor that failed
  // to initialise is a DEAD sensor, not an absent one: read() will keep
  // returning false and the safety layer will keep the car stopped.
  bool begin(uint8_t trigPin, uint8_t echoPin) {
#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
    (void)trigPin;
    (void)echoPin;
    _ok = g_lox.begin(RVR_VL53L0X_ADDR);
#else
    _trig = trigPin;
    _echo = echoPin;
    pinMode(_trig, OUTPUT);
    pinMode(_echo, INPUT);
    digitalWrite(_trig, LOW);
    _ok = true;
#endif
    return _ok;
  }

  // true  -> `meters` holds a usable reading
  // false -> the reading is INVALID (no information about distance)
  bool read(float& meters) {
    if (!_ok) {
      return false;
    }

#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
    const uint16_t mm = g_lox.readRange();
    if (g_lox.timeoutOccurred()) {
      return false;
    }
    if (mm == 0 || mm >= RVR_VL53L0X_INVALID_MM) {
      return false;   // driver's "no measurement" marker
    }
    const float m = (float)mm / 1000.0f;
    if (m < RVR_SENSOR_MIN_M || m > RVR_VL53L0X_MAX_M) {
      return false;
    }
#else
    // NOTE: pulseIn() blocks for up to RVR_US_TIMEOUT_US (~25 ms), which
    // is longer than a control tick. That delay cannot hide a watchdog
    // trip: the watchdog is evaluated from millis() and the tick checks
    // it after this call returns.
    digitalWrite(_trig, LOW);
    delayMicroseconds(3);
    digitalWrite(_trig, HIGH);
    delayMicroseconds(10);
    digitalWrite(_trig, LOW);
    const unsigned long us = pulseIn(_echo, HIGH, RVR_US_TIMEOUT_US);

    // An HC-SR04 timeout means "no echo came back", i.e. nothing within
    // its ~4 m range: the sensor is working and the way ahead is clear.
    // Reporting it as invalid would make the car undrivable in the open,
    // so it is reported as RVR_HCSR04_NO_ECHO_M instead. The trade-off:
    // a *disconnected* HC-SR04 also produces a timeout and therefore
    // looks like open space. See README "HC-SR04 caveats".
    if (us == 0) {
      meters = RVR_HCSR04_NO_ECHO_M;
      return true;
    }
    const float m = ((float)us * 0.001f) * 0.5f * RVR_SPEED_OF_SOUND_MPS;
    if (m < RVR_SENSOR_MIN_M || m > RVR_HCSR04_MAX_M) {
      return false;
    }
#endif

    meters = m;
    return true;
  }

private:
  bool _ok = false;
  uint8_t _trig = 0;
  uint8_t _echo = 0;
};

// =====================================================================
//  9. SCAN SENSOR ABSTRACTION  (ToF for the servo sweep)
// ---------------------------------------------------------------------
//  HARDWARE-UNVERIFIED. Nothing in this section has been compiled or run
//  against real hardware; both options below are shapes to adapt.
//
//  These three functions are the whole sensor surface the scan FSM uses:
//    rvrScanSensorBegin()  -- setup() only; may block internally
//    rvrScanSensorReady()  -- NON-BLOCKING readiness poll
//    rvrScanSensorFetch()  -- one bounded read burst
//  The FSM never calls VL53L1_WaitMeasurementDataReady() or any other
//  wait/loop-inside function: readiness is polled with the non-blocking
//  VL53L1_GetMeasurementDataReady(), as required by interleave rule 3.
// =====================================================================

#if RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L1X

// ST ULD device handle. The ST API is plain C: every call takes &g_l1x.
// If your port wraps it in a C++ class (common on Arduino), adapt the
// three functions below -- nothing else in this file touches the sensor.
static VL53L1_Dev_t g_l1x;

// Setup-time bring-up, following UM2356 "mandatory ranging functions".
// NOTE: this runs ONCE from setup(), before loop() -- the one place where
// blocking calls are acceptable. It is unreachable from the scan FSM.
// VL53L1_WaitDeviceBooted() is bounded to ~4 ms by UM2356 and is the only
// wait-like call in this file; it never runs on the scan path.
static bool rvrScanSensorBegin() {
  // Platform bring-up is port-specific (I2C is already up from setup()).
  // Typical ST ULD ports want one call here to bind the bus/address, e.g.:
  //   VL53L1_CommsInitialise(&g_l1x, RVR_SCAN_I2C_ADDR);   // port-specific
  // Add yours before WaitDeviceBooted if your port needs it.
  VL53L1_WaitDeviceBooted(&g_l1x);
  if (VL53L1_DataInit(&g_l1x) != VL53L1_ERROR_NONE) return false;
  if (VL53L1_StaticInit(&g_l1x) != VL53L1_ERROR_NONE) return false;
  if (VL53L1_SetMeasurementTimingBudgetMicroSeconds(
          &g_l1x, (uint32_t)RVR_SCAN_BUDGET_MS * 1000u) != VL53L1_ERROR_NONE) {
    return false;
  }
  if (VL53L1_StartMeasurement(&g_l1x) != VL53L1_ERROR_NONE) return false;
  return true;
}

// NON-BLOCKING readiness poll (UM2356: "does not block other operations").
static bool rvrScanSensorReady() {
  uint8_t ready = 0;
  const VL53L1_Error rc = VL53L1_GetMeasurementDataReady(&g_l1x, &ready);
  return (rc == VL53L1_ERROR_NONE) && (ready != 0);
}

#if RVR_SCAN_OPT_SIGNAL || RVR_SCAN_OPT_AMBIENT
// ST FixPoint16.16 MCPS -> centi-MCPS (u16), saturating at 0xFFFF.
static uint16_t rvrScanCentiMcps(uint32_t fix1616) {
  const uint32_t c = (uint32_t)(((uint64_t)fix1616 * 100u) / 65536u);
  return (c > 65535u) ? (uint16_t)65535u : (uint16_t)c;
}
#endif

// One bounded burst: read the measurement, then clear the interrupt and
// start the next ranging (both mandatory per UM2356). Range statuses are
// passed through unchanged: the HOST filters invalid ones (4/7/8/14),
// which is the v1 contract.
static bool rvrScanSensorFetch(uint16_t* outMm, uint8_t* outStatus,
                               uint16_t* outSignal, uint16_t* outAmbient) {
  VL53L1_RangingMeasurementData_t m;
  if (VL53L1_GetRangingMeasurementData(&g_l1x, &m) != VL53L1_ERROR_NONE) {
    return false;
  }
  // Best effort: if this fails, the sensor simply has no fresh data and the
  // slot times out (rule 5). Either way the loop is never blocked.
  (void)VL53L1_ClearInterruptAndStartMeasurement(&g_l1x);

  *outMm = (m.RangeMilliMeter > 4000u) ? (uint16_t)4000u : (uint16_t)m.RangeMilliMeter;
  *outStatus = (uint8_t)m.RangeStatus;
#if RVR_SCAN_OPT_SIGNAL
  *outSignal = rvrScanCentiMcps(m.SignalRateRtnMegaCps);
#else
  *outSignal = 0;
#endif
#if RVR_SCAN_OPT_AMBIENT
  *outAmbient = rvrScanCentiMcps(m.AmbientRateRtnMegaCps);
#else
  *outAmbient = 0;
#endif
  return true;
}

#elif RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L0X

// ---------------------------------------------------------------------
// VL53L0X fallback -- BEST EFFORT / BENCH ONLY, and HARDWARE-UNVERIFIED.
// It reuses the Adafruit VL53L0X front-sensor object (g_lox). The Adafruit
// API has NO non-blocking readiness poll: readRange() waits inside the
// driver, bounded only by the driver's own timeout, so this path cannot
// promise interleave rules 3-5 the way the VL53L1X path does. Range
// numbers: 2.0 m validity (RVR_VL53L0X_MAX_M), NOT the L1X's 4 m.
// ---------------------------------------------------------------------

static bool rvrScanSensorBegin() {
  // g_lox is initialised by the front-sensor code in setup(); nothing to do.
  return true;
}

static bool rvrScanSensorReady() {
  // No poll exists; the FSM's RVR_SCAN_SAMPLE_MIN_MS pacing (== the timing
  // budget) is what stops this path from recording one reading forever.
  return true;
}

static bool rvrScanSensorFetch(uint16_t* outMm, uint8_t* outStatus,
                               uint16_t* outSignal, uint16_t* outAmbient) {
  *outSignal = 0;    // not provided by the L0X path; 0 = unknown
  *outAmbient = 0;
  const uint16_t mm = g_lox.readRange();   // bounded by the Adafruit driver
  if (g_lox.timeoutOccurred() || mm == 0 || mm >= RVR_VL53L0X_INVALID_MM ||
      mm > (uint16_t)(RVR_VL53L0X_MAX_M * 1000.0f)) {
    *outMm = 0;
    *outStatus = RVR_SCAN_STATUS_TIMEOUT;  // no usable sample this slot
    return true;
  }
  *outMm = mm;
  *outStatus = 0;    // success (the Adafruit driver has no RangeStatus field)
  return true;
}

#endif

// =====================================================================
// 10. SCAN SERVO  (HARDWARE-UNVERIFIED)
// ---------------------------------------------------------------------
//  Same LEDC attach style as RvrMotor::begin(): ledcSetup + ledcAttachPin,
//  then ledcWrite. The servo has its own channel (RVR_SERVO_CH) because the
//  motors own 0 and 1. A write is cheap, but the FSM still performs AT MOST
//  ONE per loop() pass (interleave rule 2).
// =====================================================================

// Writes one pulse width (us) as a duty cycle on RVR_SERVO_CH. ONE ledcWrite.
static void rvrServoWritePulseUs(uint32_t pulseUs) {
  if (pulseUs > RVR_SERVO_PERIOD_US) pulseUs = RVR_SERVO_PERIOD_US;  // defensive
  const uint32_t duty =
      (pulseUs * ((1u << RVR_SERVO_RES_BITS) - 1u)) / RVR_SERVO_PERIOD_US;
  ledcWrite(RVR_SERVO_CH, duty);
}

// Pulse for an angle: start_deg -> RVR_SERVO_PULSE_MIN_US, end_deg ->
// RVR_SERVO_PULSE_MAX_US, linear in between (the scan's endpoints, not the
// servo's mechanical limits). A degenerate span (start == end after
// rounding) parks at the mid pulse.
static uint32_t rvrServoPulseUsFor(int16_t angleDd, int16_t startDd, int16_t endDd) {
  const int32_t span = (int32_t)endDd - (int32_t)startDd;
  if (span <= 0) return RVR_SERVO_PARK_US;
  int32_t rel = (int32_t)angleDd - (int32_t)startDd;
  if (rel < 0) rel = 0;
  if (rel > span) rel = span;
  return RVR_SERVO_PULSE_MIN_US +
         ((uint32_t)rel * (RVR_SERVO_PULSE_MAX_US - RVR_SERVO_PULSE_MIN_US)) /
             (uint32_t)span;
}

// Convenience: one servo write for `angleDd` of the active sweep.
static void rvrServoWriteAngle(int16_t angleDd, int16_t startDd, int16_t endDd) {
  rvrServoWritePulseUs(rvrServoPulseUsFor(angleDd, startDd, endDd));
}

// Setup-time init; call once from setup(). Parks the servo mid-range until a
// command asks for a sweep.
static void rvrServoBegin() {
  ledcSetup(RVR_SERVO_CH, RVR_SERVO_HZ, RVR_SERVO_RES_BITS);
  ledcAttachPin(RVR_SERVO_PIN, RVR_SERVO_CH);
  rvrServoWritePulseUs(RVR_SERVO_PARK_US);
}

// =====================================================================
// 11. GLOBAL STATE
// =====================================================================

static WiFiUDP g_udp;

static RvrDrive g_drive;
static RvrDistanceSensor g_frontSensor;
#if RVR_ENABLE_REAR_SENSOR
static RvrDistanceSensor g_rearSensor;
#endif

static char g_rxBuf[RVR_RX_BUF];

// --- last accepted command (the only thing the watchdog trusts) ---
struct RvrCommandState {
  bool     valid;
  uint32_t lastMs;
  float    t;
  int32_t  seq;
  float    v;
  float    w;
  int32_t  ttlMs;
};
static RvrCommandState g_cmd = {false, 0, 0.0f, 0, 0.0f, 0.0f, 0};

// --- telemetry destination: whoever last sent a valid command ---
static IPAddress g_peerIp;
static uint16_t  g_peerPort = 0;
static bool      g_telemTargetValid = false;

// --- sensor + estimator state (all consumed by the safety layer) ---
static bool  g_frontValid = false;
static float g_frontM = 0.0f;
static bool  g_rearValid = false;
static float g_rearM = 0.0f;
static float g_vMeas = 0.0f;
static float g_wMeas = 0.0f;
static float g_battV = 0.0f;

// --- flags reported in telemetry ---
static bool     g_watchdog = false;
static bool     g_reflexFlag = false;
static uint32_t g_lastReflexMs = 0;

// --- encoder counters ---
static volatile int32_t g_encCount[2] = {0, 0};
static int32_t g_encL = 0;
static int32_t g_encR = 0;
static int32_t g_encLPrev = 0;
static int32_t g_encRPrev = 0;

// --- timing ---
static uint32_t g_bootMs = 0;
static uint32_t g_lastTickMs = 0;
static uint32_t g_lastTelemMs = 0;
static uint32_t g_lastRejectLogMs = 0;
static uint32_t g_lastSensorLogMs = 0;

// --- transition logging (serial stays quiet at 50 Hz) ---
static bool g_loggedWatchdog = false;
static bool g_loggedReflex = false;

// --- scan (servo ToF sweep; v1, HARDWARE-UNVERIFIED) ---
// Phases: the FSM advances at most one bounded step per loop() pass.
enum RvrScanPhase : uint8_t {
  RVR_SCAN_IDLE     = 0,   // no sweep; the last scan id stays in telemetry
  RVR_SCAN_ARMED    = 1,   // params latched; servo move + first slot next pass
  RVR_SCAN_SCANNING = 2,   // waiting for / recording samples
  RVR_SCAN_FLUSHING = 3,   // one result datagram pending, sent on the grid
};

struct RvrScanParams {
  uint16_t id;        // host-chosen scan id (telemetry echoes it as scan_seq)
  int8_t   startDd;   // degrees; -90..+90 (validated on receipt)
  int8_t   endDd;     // degrees; >= startDd after rounding
  uint16_t stepDd;    // degrees; 1..180
  uint8_t  rateHz;    // nominal only; the achieved cadence is sensor-limited
};

struct RvrScanState {
  RvrScanPhase phase;
  bool     startPending;     // a start request sits in `next`
  bool     stopPending;      // action "stop" seen; abort on the next pass
  bool     haveLastStarted;  // idempotence latch for scan.id
  uint16_t lastStartedId;
  RvrScanParams next;        // pending start request
  RvrScanParams act;         // active (or most recent) sweep
  uint16_t lastId;           // telemetry scan_seq; 0 = none yet
  uint16_t n;                // samples in this sweep
  uint16_t idx;              // samples recorded so far
  int16_t  angleDd;          // angle of the slot being measured
  uint8_t  chunkIdx;         // 0-based
  uint8_t  chunkCount;       // ceil(n / RVR_SCAN_CHUNK_MAX)
  uint8_t  chunkN;           // samples staged in g_scanBuf
  uint8_t  chunkFirstIdx;    // index of the first staged sample
  bool     chunkTimeout;     // a staged sample has status 0xFE
  bool     finalFlush;       // the staged chunk ends a completed sweep
  bool     partialFlush;     // send the staged chunk with the partial bit
  bool     discardFirst;     // drop the first sample taken after the servo move
  uint32_t periodUs;         // achieved cadence so far (0 = unknown)
  uint32_t tStartMs;         // millis() at sweep start
  uint32_t tOpMs;            // last scan operation (RVR_SCAN_GRID_MS grid)
  uint32_t tSlotMs;          // when the current sample slot began waiting
  uint32_t tChunkFirstMs;    // ms since tStartMs of the chunk's first sample
  uint32_t tFirstMs;         // ms since tStartMs of the sweep's first sample
  uint32_t tLastMs;          // ms since tStartMs of the latest sample
};
static RvrScanState g_scan;                    // zero-initialised: phase == IDLE
static uint8_t g_scanBuf[RVR_SCAN_BUF_BYTES];  // 16 B header + staged samples

// =====================================================================
// 12. ENCODER INTERRUPTS  (must stay tiny and never block)
// =====================================================================

void IRAM_ATTR rvrEncLeftIsr() {
  // Channel B only carries direction in this x1 decoding.
  if (digitalRead(RVR_ENC_L_A) == digitalRead(RVR_ENC_L_B)) {
    g_encCount[0]--;
  } else {
    g_encCount[0]++;
  }
}

void IRAM_ATTR rvrEncRightIsr() {
  if (digitalRead(RVR_ENC_R_A) == digitalRead(RVR_ENC_R_B)) {
    g_encCount[1]--;
  } else {
    g_encCount[1]++;
  }
}

// =====================================================================
// 13. COMMAND PARSING  (all outputs are primitives so that this file
//     survives Arduino's automatic prototype generation)
// =====================================================================

// True only for something that looks like a JSON number. Strings,
// arrays, objects and null are rejected. (A JSON true/false survives
// this test and coerces to 1/0; every accepted value is clamped below,
// so that is bounded and harmless.)
static bool rvrIsNumber(JsonVariant v) {
  if (v.isNull()) return false;
  if (v.is<const char*>()) return false;
  if (v.is<JsonArray>() || v.is<JsonObject>()) return false;
  return true;
}

// Returns true only for a well-formed, in-range command. A rejected
// datagram must NOT refresh the watchdog and must NOT become the
// telemetry destination. That includes a datagram whose optional "scan"
// object is present but malformed: it invalidates the WHOLE datagram.
static bool rvrParseCommand(const char* json, size_t len,
                           float* outT, int32_t* outSeq,
                           float* outV, float* outW, int32_t* outTtlMs,
                           bool* outScanPresent, bool* outScanStop,
                           int32_t* outScanId, int32_t* outScanStartDd,
                           int32_t* outScanEndDd, int32_t* outScanStepDd,
                           int32_t* outScanRateHz) {
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, json, len);
  if (err) {
    return false;
  }

  JsonObject root = doc.as<JsonObject>();
  if (root.isNull()) {
    return false;
  }

  // Required by the protocol.
  if (!rvrIsNumber(root["t"]))   return false;
  if (!rvrIsNumber(root["seq"])) return false;
  if (!rvrIsNumber(root["v"]))   return false;
  if (!rvrIsNumber(root["w"]))   return false;

  const float t = root["t"].as<float>();
  const float seqF = root["seq"].as<float>();
  const float v = root["v"].as<float>();
  const float w = root["w"].as<float>();
  if (!isfinite(t) || !isfinite(seqF) || !isfinite(v) || !isfinite(w)) {
    return false;
  }

  // seq is a counter, not a timestamp: it must be integral. Do not put
  // it in `int` before this check, or a huge float wraps into a
  // plausible-looking sequence number.
  const float seqRounded = roundf(seqF);
  if (fabsf(seqF - seqRounded) > 0.0f) {
    return false;
  }

  // Hard clamp range is part of the contract: out of range is malformed,
  // not "clamped silently". A small slack absorbs float rounding.
  if (fabsf(v) > RVR_MAX_V + RVR_CMD_SLACK) return false;
  if (fabsf(w) > RVR_MAX_W + RVR_CMD_SLACK) return false;

  // ttl_ms is optional and informational: the ESP32 watchdog always
  // governs, and it will never be extended past RVR_WATCHDOG_MS.
  int32_t ttlMs = 0;
  if (!root["ttl_ms"].isNull()) {
    if (!rvrIsNumber(root["ttl_ms"])) return false;
    const float ttlF = root["ttl_ms"].as<float>();
    if (!isfinite(ttlF)) return false;
    ttlMs = (int32_t)rvrClamp(ttlF, 0.0f, 60000.0f);
  }

  // ---- optional scan object (protocol v1; README "Scan mode") ----
  // Both actions carry the full object, so every field is validated here
  // regardless of the action. Any violation makes the datagram invalid.
  bool scanPresent = false;
  bool scanStop = false;
  int32_t scanId = 0;
  int32_t scanStartDd = 0;
  int32_t scanEndDd = 0;
  int32_t scanStepDd = 0;
  int32_t scanRateHz = 0;

  if (!root["scan"].isNull()) {
    if (!root["scan"].is<JsonObject>()) return false;
    JsonObject so = root["scan"].as<JsonObject>();

    // id: integral, 0..65535.
    if (!rvrIsNumber(so["id"])) return false;
    const float idF = so["id"].as<float>();
    if (!isfinite(idF)) return false;
    const float idR = roundf(idF);
    if (fabsf(idF - idR) > 0.0f) return false;
    if (idR < 0.0f || idR > 65535.0f) return false;

    // action: exactly "start" or "stop".
    if (!so["action"].is<const char*>()) return false;
    const char* action = so["action"].as<const char*>();
    if (strcmp(action, "start") != 0 && strcmp(action, "stop") != 0) return false;
    scanStop = (strcmp(action, "stop") == 0);

    // start_deg/end_deg: finite numbers inside [-90, 90], start < end.
    if (!rvrIsNumber(so["start_deg"]) || !rvrIsNumber(so["end_deg"])) return false;
    const float startF = so["start_deg"].as<float>();
    const float endF = so["end_deg"].as<float>();
    if (!isfinite(startF) || !isfinite(endF)) return false;
    if (startF < -90.0f || startF > 90.0f) return false;
    if (endF < -90.0f || endF > 90.0f) return false;
    if (!(startF < endF)) return false;

    // step_deg: integral, 1..180.
    if (!rvrIsNumber(so["step_deg"])) return false;
    const float stepF = so["step_deg"].as<float>();
    if (!isfinite(stepF)) return false;
    const float stepR = roundf(stepF);
    if (fabsf(stepF - stepR) > 0.0f) return false;
    if (stepR < 1.0f || stepR > 180.0f) return false;

    // rate_hz: finite, 1..50. Nominal only (see README): the sweep cadence
    // is sensor/servo limited and is reported per chunk as period_us.
    if (!rvrIsNumber(so["rate_hz"])) return false;
    const float rateF = so["rate_hz"].as<float>();
    if (!isfinite(rateF)) return false;
    if (rateF < 1.0f || rateF > 50.0f) return false;

    // Degrees are stored as integers: the result payload is int8 degrees,
    // and the servo is coarser than 0.5 deg anyway.
    scanStartDd = (int32_t)rvrClamp(roundf(startF), -90.0f, 90.0f);
    scanEndDd = (int32_t)rvrClamp(roundf(endF), -90.0f, 90.0f);
    scanStepDd = (int32_t)stepR;
    scanRateHz = (int32_t)roundf(rateF);
    scanId = (int32_t)idR;
    scanPresent = true;
  }

  *outT = t;
  *outSeq = (int32_t)rvrClamp(seqRounded, -2147483647.0f, 2147483647.0f);
  *outV = v;
  *outW = w;
  *outTtlMs = ttlMs;
  *outScanPresent = scanPresent;
  *outScanStop = scanStop;
  *outScanId = scanId;
  *outScanStartDd = scanStartDd;
  *outScanEndDd = scanEndDd;
  *outScanStepDd = scanStepDd;
  *outScanRateHz = scanRateHz;
  return true;
}

// Latches an accepted scan request. Pure state: NO I/O, no servo write, no
// datagram, and no g_cmd / watchdog writes -- the FSM (section 14) does all
// of that, one bounded step per loop() pass.
static void rvrScanAcceptCommand(bool stop, uint16_t id, int8_t startDd, int8_t endDd,
                                 uint16_t stepDd, uint8_t rateHz) {
  if (stop) {
    g_scan.startPending = false;   // the action that arrives last wins
    if (g_scan.phase != RVR_SCAN_IDLE) {
      g_scan.stopPending = true;
    }
    return;
  }

  // Idempotence: the host repeats the same start until telemetry echoes it
  // as scan_seq; a repeat must never restart or disturb the running sweep.
  if (g_scan.haveLastStarted && g_scan.lastStartedId == id) {
    return;
  }
  g_scan.haveLastStarted = true;
  g_scan.lastStartedId = id;

  g_scan.next.id = id;
  g_scan.next.startDd = startDd;
  g_scan.next.endDd = endDd;
  g_scan.next.stepDd = stepDd;
  g_scan.next.rateHz = rateHz;
  g_scan.stopPending = false;
  g_scan.startPending = true;      // the FSM starts it (or replaces)
}

// =====================================================================
// 14. SCAN FSM  (servo ToF sweep; v1, HARDWARE-UNVERIFIED)
// ---------------------------------------------------------------------
//  Serviced from loop() once per pass, after rvrPollUdp(). One pass does
//  at most:
//    * ONE bounded I2C burst (readiness poll + sample fetch), never a wait;
//    * ONE servo PWM write (ledcWrite);
//    * ONE result datagram;
//  and never more often than every RVR_SCAN_GRID_MS (millis() arithmetic).
//  The whole sweep is never executed in one call. The watchdog is
//  untouched: this section never writes g_cmd, g_cmd.lastMs or g_watchdog,
//  and it never resets the watchdog.
// =====================================================================

// Writes a u32 little-endian (the binary scan path must not depend on the
// host's byte order).
static void rvrPutU32LE(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xFFu);
  p[1] = (uint8_t)((v >> 8) & 0xFFu);
  p[2] = (uint8_t)((v >> 16) & 0xFFu);
  p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

// Telemetry scan_state. ARMED reports as "scanning": the sweep is under way
// (the servo move and the first slot are simply deferred to the next pass).
static const char* rvrScanStateName() {
  if (g_scan.phase == RVR_SCAN_SCANNING || g_scan.phase == RVR_SCAN_ARMED) {
    return "scanning";
  }
  if (g_scan.phase == RVR_SCAN_FLUSHING) {
    return "flushing";
  }
  return "idle";
}

// Stages one sample into g_scanBuf for the slot being measured, then
// advances the sweep (servo + slot clock) unless `advance` is false (the
// 0xFF "aborted" sample stamped at abort time). No I2C here.
static void rvrScanRecordSample(uint32_t now, uint8_t status, uint16_t rangeMm,
                                uint16_t signal, uint16_t ambient, bool advance) {
  const uint32_t t = now - g_scan.tStartMs;   // ms since sweep start
  if (g_scan.chunkN == 0) g_scan.tChunkFirstMs = t;
  if (g_scan.idx == 0) g_scan.tFirstMs = t;
  g_scan.tLastMs = t;
  g_scan.discardFirst = false;

  // Defensive: chunks are flushed as soon as they are full, so this is
  // never true. Dropping a sample beats overrunning the buffer.
  if (g_scan.chunkN >= RVR_SCAN_CHUNK_MAX) return;

  size_t o = 16u + (size_t)g_scan.chunkN * RVR_SCAN_SAMPLE_BYTES;
  g_scanBuf[o + 0] = (uint8_t)(int8_t)g_scan.angleDd;
  g_scanBuf[o + 1] = (uint8_t)(rangeMm & 0xFFu);
  g_scanBuf[o + 2] = (uint8_t)(rangeMm >> 8);
  g_scanBuf[o + 3] = status;
  o += 4u;
#if RVR_SCAN_OPT_SIGNAL
  g_scanBuf[o++] = (uint8_t)(signal & 0xFFu);
  g_scanBuf[o++] = (uint8_t)(signal >> 8);
#else
  (void)signal;
#endif
#if RVR_SCAN_OPT_AMBIENT
  g_scanBuf[o++] = (uint8_t)(ambient & 0xFFu);
  g_scanBuf[o++] = (uint8_t)(ambient >> 8);
#else
  (void)ambient;
#endif
#if RVR_SCAN_OPT_T_US
  // Optional t_us: ms since sweep start, saturating at 0xFFFF. With the
  // header's t0_ms/period_us this is only needed if the cadence wobbles.
  const uint32_t tus = (t > 65535u) ? 65535u : t;
  g_scanBuf[o++] = (uint8_t)(tus & 0xFFu);
  g_scanBuf[o++] = (uint8_t)(tus >> 8);
#endif

  g_scan.chunkN++;
  if (status == RVR_SCAN_STATUS_TIMEOUT) g_scan.chunkTimeout = true;
  g_scan.idx++;

  if (advance && g_scan.idx < g_scan.n) {
    g_scan.angleDd = (int16_t)(g_scan.angleDd + (int16_t)g_scan.stepDd);
    rvrServoWriteAngle(g_scan.angleDd, g_scan.act.startDd, g_scan.act.endDd);  // ONE write
    g_scan.tSlotMs = now;   // the next slot starts waiting now
  }
}

// Latches g_scan.next as the active sweep. Pure state: the FSM's ARMED
// branch performs the servo move and opens the first slot.
static void rvrScanBeginAccepted() {
  g_scan.act = g_scan.next;
  g_scan.startPending = false;
  g_scan.stopPending = false;

  const int32_t span = (int32_t)g_scan.act.endDd - (int32_t)g_scan.act.startDd;
  int32_t n = (span / (int32_t)g_scan.act.stepDd) + 1;
  if (n < 1) n = 1;
  if (n > (int32_t)RVR_SCAN_MAX_SAMPLES) n = (int32_t)RVR_SCAN_MAX_SAMPLES;

  g_scan.n = (uint16_t)n;
  g_scan.idx = 0;
  g_scan.angleDd = (int16_t)g_scan.act.startDd;
  g_scan.chunkIdx = 0;
  g_scan.chunkCount =
      (uint8_t)((n + (int32_t)RVR_SCAN_CHUNK_MAX - 1) / (int32_t)RVR_SCAN_CHUNK_MAX);
  g_scan.chunkN = 0;
  g_scan.chunkFirstIdx = 0;
  g_scan.chunkTimeout = false;
  g_scan.finalFlush = false;
  g_scan.partialFlush = false;
  g_scan.discardFirst = true;
  g_scan.periodUs = 0;
  g_scan.tStartMs = 0;
  g_scan.tSlotMs = 0;
  g_scan.tChunkFirstMs = 0;
  g_scan.tFirstMs = 0;
  g_scan.tLastMs = 0;
  g_scan.lastId = g_scan.act.id;   // telemetry scan_seq: the active sweep
  g_scan.phase = RVR_SCAN_ARMED;
}

// Sends the staged chunk (<= one datagram) and advances the chunk
// bookkeeping. Also the single place that finishes a sweep.
static void rvrScanFlushChunk(uint32_t now) {
  g_scan.tOpMs = now;

  const uint8_t n = g_scan.chunkN;
  const size_t len = 16u + (size_t)n * RVR_SCAN_SAMPLE_BYTES;

  // Achieved cadence estimate (desmear): average spacing of the samples
  // recorded so far. 0 = unknown (fewer than 2 samples, or same ms).
  if (g_scan.idx >= 2) {
    const uint32_t spanMs = g_scan.tLastMs - g_scan.tFirstMs;
    g_scan.periodUs = (spanMs == 0u)
                          ? 0u
                          : (spanMs * 1000u) / (uint32_t)(g_scan.idx - 1u);
  } else {
    g_scan.periodUs = 0u;
  }

  // Header v1 (16 B; see README "Scan mode" for the byte table).
  g_scanBuf[0] = RVR_SCAN_MSG;
  g_scanBuf[1] = (uint8_t)(RVR_SCAN_VERSION
      | (RVR_SCAN_OPT_SIGNAL ? RVR_SCAN_FLAG_HAS_SIGNAL : 0u)
      | (RVR_SCAN_OPT_AMBIENT ? RVR_SCAN_FLAG_HAS_AMBIENT : 0u)
      | (RVR_SCAN_OPT_T_US ? RVR_SCAN_FLAG_HAS_T_US : 0u)
      | (g_scan.partialFlush ? RVR_SCAN_FLAG_PARTIAL : 0u)
      | (g_scan.chunkTimeout ? RVR_SCAN_FLAG_TIMEOUTS : 0u));
  g_scanBuf[2] = (uint8_t)(g_scan.act.id & 0xFFu);
  g_scanBuf[3] = (uint8_t)(g_scan.act.id >> 8);
  g_scanBuf[4] = g_scan.chunkIdx;
  g_scanBuf[5] = g_scan.chunkCount;
  g_scanBuf[6] = g_scan.chunkFirstIdx;
  g_scanBuf[7] = n;
  rvrPutU32LE(&g_scanBuf[8], g_scan.tChunkFirstMs);
  rvrPutU32LE(&g_scanBuf[12], g_scan.periodUs);

  // Never send a truncated datagram (same discipline as telemetry). By
  // construction len <= RVR_SCAN_DATAGRAM_CAP <= sizeof(g_scanBuf); the
  // guard is defensive only.
  if (n > 0u && len <= RVR_SCAN_DATAGRAM_CAP && len <= sizeof(g_scanBuf) &&
      g_telemTargetValid) {
    g_udp.beginPacket(g_peerIp, g_peerPort);
    g_udp.write(g_scanBuf, len);
    g_udp.endPacket();
  }

  // Advance the chunk bookkeeping.
  g_scan.chunkN = 0;
  g_scan.chunkTimeout = false;
  g_scan.chunkFirstIdx = (uint8_t)g_scan.idx;
  g_scan.chunkIdx = (uint8_t)(g_scan.chunkIdx + 1u);

  if (g_scan.partialFlush || g_scan.finalFlush) {
    // Sweep over (completed or aborted): idle, unless a replacement start
    // is already waiting.
    g_scan.finalFlush = false;
    g_scan.partialFlush = false;
    if (g_scan.startPending) {
      rvrScanBeginAccepted();
    } else {
      g_scan.phase = RVR_SCAN_IDLE;
    }
  } else {
    g_scan.phase = RVR_SCAN_SCANNING;
  }
}

// Aborts the sweep (watchdog trip, action "stop", or a replacing start).
// No I2C, no servo write; at most one partial datagram, emitted by the
// FLUSHING phase on a later pass (one datagram per pass).
static void rvrScanAbort(uint32_t now) {
  if (g_scan.phase == RVR_SCAN_SCANNING && g_scan.idx < g_scan.n) {
    // Stamp the in-flight slot as aborted so the partial sweep shows where
    // it stopped, then flush whatever is staged.
    rvrScanRecordSample(now, RVR_SCAN_STATUS_ABORTED, 0, 0, 0, false);
  }
  g_scan.partialFlush = true;
  if (g_scan.chunkN > 0u) {
    g_scan.phase = RVR_SCAN_FLUSHING;
    return;
  }
  g_scan.partialFlush = false;
  if (g_scan.startPending) {
    rvrScanBeginAccepted();
  } else {
    g_scan.phase = RVR_SCAN_IDLE;
  }
}

// One bounded sample step: poll readiness (non-blocking), fetch one sample
// when ready, or record a timeout when the slot is overdue. Never loops.
static void rvrScanServiceSample(uint32_t now) {
  g_scan.tOpMs = now;

#if RVR_SCAN_HAS_SAMPLE_MIN
  // L0X fallback pacing (absent on the L1X path): never complete slots
  // faster than the timing budget, or the fallback would record the same
  // measurement over and over.
  if ((uint32_t)(now - g_scan.tSlotMs) < RVR_SCAN_SAMPLE_MIN_MS) return;
#endif

  if (rvrScanSensorReady()) {
    uint16_t mm = 0, signal = 0, ambient = 0;
    uint8_t status = 0;
    if (rvrScanSensorFetch(&mm, &status, &signal, &ambient)) {   // ONE burst
      if (g_scan.discardFirst) {
        // The first ready measurement may predate the servo's move to the
        // start angle; drop it and restart the slot clock so the first
        // *recorded* sample is a fresh one.
        g_scan.discardFirst = false;
        g_scan.tSlotMs = now;
        return;
      }
      rvrScanRecordSample(now, status, mm, signal, ambient, true);
    } else if ((uint32_t)(now - g_scan.tSlotMs) > RVR_SCAN_TIMEOUT_MS) {
      // The read failed and the slot is overdue: record the timeout.
      rvrScanRecordSample(now, RVR_SCAN_STATUS_TIMEOUT, 0, 0, 0, true);
    } else {
      return;   // read failed but there is budget left; try again next grid
    }
  } else if ((uint32_t)(now - g_scan.tSlotMs) > RVR_SCAN_TIMEOUT_MS) {
    // Not ready within 1.5x the timing budget: record status 0xFE and
    // advance. A wedged sensor degrades the sweep, never the loop.
    rvrScanRecordSample(now, RVR_SCAN_STATUS_TIMEOUT, 0, 0, 0, true);
  } else {
    return;   // still waiting for this slot
  }

  // Time to flush? Chunk full, or the sweep reached its last sample.
  if (g_scan.idx >= g_scan.n) {
    g_scan.finalFlush = true;
    g_scan.phase = RVR_SCAN_FLUSHING;
  } else if (g_scan.chunkN >= RVR_SCAN_CHUNK_MAX) {
    g_scan.phase = RVR_SCAN_FLUSHING;
  }
}

// The FSM itself. Called once per loop() pass; every path is bounded.
static void rvrScanStep(uint32_t now) {
  // ---- rule 7: abort when the watchdog is down ----
  // A FLUSHING partial chunk is allowed to drain (one datagram per pass).
  if (g_watchdog && g_scan.phase != RVR_SCAN_IDLE && g_scan.phase != RVR_SCAN_FLUSHING) {
    rvrScanAbort(now);
    return;
  }

  // ---- action "stop" (consumed even when it arrives too late) ----
  if (g_scan.stopPending) {
    g_scan.stopPending = false;
    if (g_scan.phase == RVR_SCAN_SCANNING || g_scan.phase == RVR_SCAN_ARMED) {
      rvrScanAbort(now);
      return;
    }
    if (g_scan.phase == RVR_SCAN_FLUSHING && !g_scan.finalFlush) {
      // A mid-sweep chunk is already going out (one datagram per pass), so
      // mark it aborted; the flush then ends the sweep instead of resuming.
      g_scan.partialFlush = true;
    }
  }

  // ---- a new start replaces a running sweep ----
  if (g_scan.startPending) {
    if (g_scan.phase == RVR_SCAN_SCANNING || g_scan.phase == RVR_SCAN_ARMED) {
      rvrScanAbort(now);   // finishes the old sweep, then starts the new one
      return;
    }
    if (g_scan.phase == RVR_SCAN_FLUSHING && !g_scan.finalFlush) {
      g_scan.partialFlush = true;   // the old sweep is being cut short
    }
  }

  // ---- rule 4: at most one scan operation every RVR_SCAN_GRID_MS ----
  if ((uint32_t)(now - g_scan.tOpMs) < RVR_SCAN_GRID_MS) {
    return;
  }

  switch (g_scan.phase) {
    case RVR_SCAN_ARMED:
      // Start: move the servo to the first angle (ONE write) and open the
      // first slot. No I2C here: the sensor is already free-running.
      g_scan.tOpMs = now;
      g_scan.tStartMs = now;
      g_scan.tSlotMs = now;
      rvrServoWriteAngle(g_scan.act.startDd, g_scan.act.startDd, g_scan.act.endDd);
      Serial.printf("[scan] sweep id=%u %d..%d deg step=%u rate=%u Hz (nominal), budget=%u ms\n",
                    (unsigned)g_scan.act.id, (int)g_scan.act.startDd,
                    (int)g_scan.act.endDd, (unsigned)g_scan.act.stepDd,
                    (unsigned)g_scan.act.rateHz, (unsigned)RVR_SCAN_BUDGET_MS);
      g_scan.phase = RVR_SCAN_SCANNING;
      break;

    case RVR_SCAN_SCANNING:
      rvrScanServiceSample(now);
      break;

    case RVR_SCAN_FLUSHING:
      rvrScanFlushChunk(now);
      break;

    case RVR_SCAN_IDLE:
    default:
      if (g_scan.startPending) {
        rvrScanBeginAccepted();
      }
      break;
  }
}

// =====================================================================
// 15. UDP
// =====================================================================

static void rvrPollUdp(uint32_t now) {
  for (uint8_t i = 0; i < RVR_MAX_PACKETS_PER_TICK; ++i) {
    const int packetSize = g_udp.parsePacket();
    if (packetSize <= 0) {
      break;
    }

    const int len = g_udp.read(g_rxBuf, sizeof(g_rxBuf) - 1);
    if (len <= 0) {
      continue;
    }
    g_rxBuf[len] = '\0';

    float t = 0.0f;
    float v = 0.0f;
    float w = 0.0f;
    int32_t seq = 0;
    int32_t ttlMs = 0;
    bool scanPresent = false;
    bool scanStop = false;
    int32_t scanId = 0;
    int32_t scanStartDd = 0;
    int32_t scanEndDd = 0;
    int32_t scanStepDd = 0;
    int32_t scanRateHz = 0;
    if (!rvrParseCommand(g_rxBuf, (size_t)len, &t, &seq, &v, &w, &ttlMs,
                         &scanPresent, &scanStop, &scanId, &scanStartDd,
                         &scanEndDd, &scanStepDd, &scanRateHz)) {
      if ((uint32_t)(now - g_lastRejectLogMs) > 1000u) {
        g_lastRejectLogMs = now;
        Serial.printf("[cmd] rejected datagram (%d bytes)\n", len);
      }
      continue;
    }

    // Accepted: this is now the command the safety layer acts on, and
    // this sender is now the telemetry destination.
    g_cmd.valid = true;
    g_cmd.lastMs = now;
    g_cmd.t = t;
    g_cmd.seq = seq;
    g_cmd.v = v;
    g_cmd.w = w;
    g_cmd.ttlMs = ttlMs;

    g_peerIp = g_udp.remoteIP();
    g_peerPort = g_udp.remotePort();
    g_telemTargetValid = true;

    if (g_watchdog) {
      Serial.printf("[cmd] command accepted after a watchdog stop: seq=%ld v=%.3f w=%.3f\n",
                    (long)seq, v, w);
      g_watchdog = false;
      g_loggedWatchdog = false;
    }

    // Scan request (v1). Latch only: no I/O, no watchdog write (rule 6);
    // the FSM in section 14 does the bounded work. A malformed scan object
    // never reaches this point -- rvrParseCommand() rejects the datagram.
    if (scanPresent) {
      rvrScanAcceptCommand(scanStop, (uint16_t)scanId, (int8_t)scanStartDd,
                           (int8_t)scanEndDd, (uint16_t)scanStepDd,
                           (uint8_t)scanRateHz);
    }
  }
}

// =====================================================================
// 16. CONTROL TICK  (watchdog -> reflex -> mixing -> motors)
// =====================================================================

static void rvrControlTick(uint32_t now) {
  // ---- sensor reads: a failed read is an INVALID reading ----
  g_frontValid = g_frontSensor.read(g_frontM);
#if RVR_ENABLE_REAR_SENSOR
  g_rearValid = g_rearSensor.read(g_rearM);
#else
  g_rearValid = false;
#endif

  // ---- 1. watchdog: the network cannot be trusted, so time is the judge ----
  if (!g_cmd.valid) {
    g_watchdog = true;                                  // never received a command
  } else {
    g_watchdog = (uint32_t)(now - g_cmd.lastMs) > RVR_WATCHDOG_MS;
  }

  float v = g_watchdog ? 0.0f : g_cmd.v;
  float w = g_watchdog ? 0.0f : g_cmd.w;

  // ---- 2. reflex: code-owned, evaluated after the watchdog ----
  bool reflexNow = false;
  bool forceStop = g_watchdog || !g_frontValid;

  if (!g_frontValid) {
    // No usable distance information at all: hold still in every axis.
    // Even rotation is refused because we cannot know which way is free.
    v = 0.0f;
    w = 0.0f;
    reflexNow = true;
  } else if (v > 0.0f) {
    if (g_frontM < RVR_REFLEX_STOP_M) {
      v = 0.0f;
      reflexNow = true;
    } else if (g_frontM < RVR_REFLEX_SLOW_M) {
      if (v > RVR_REFLEX_SLOW_V) {
        v = RVR_REFLEX_SLOW_V;
      }
      reflexNow = true;
    }
    // v < 0 (reversing away) is deliberately never blocked here.
  }

  if (reflexNow) {
    g_lastReflexMs = now;
  }
  g_reflexFlag = reflexNow ||
                 ((uint32_t)(now - g_lastReflexMs) < RVR_REFLEX_HOLD_MS);

  // ---- 3. actuation ----
  if (forceStop) {
    // Watchdog or dead sensor: cut the motors now, do not ramp.
    g_drive.stopImmediately();
  } else {
    // Everything else, including the proximity reflex, goes through the
    // mixer and the (asymmetric) slew limiter. A reflex-induced v=0
    // therefore decelerates at RVR_SLEW_DOWN_V_PER_TICK, which is
    // roughly 120 ms from full speed.
    g_drive.setTarget(v, w);
    g_drive.update();
  }

  // ---- 4. estimators (telemetry only; never part of the control path) ----
#if RVR_ENABLE_ENCODERS
  noInterrupts();
  const int32_t encL = g_encCount[0];
  const int32_t encR = g_encCount[1];
  interrupts();
  g_encL = encL;
  g_encR = encR;

  const float dt = (float)RVR_TICK_MS / 1000.0f;
  const float wheelCirc = PI * RVR_WHEEL_DIAM_M;
  const float distL = ((float)(g_encL - g_encLPrev) / RVR_ENCODER_CPR) * wheelCirc;
  const float distR = ((float)(g_encR - g_encRPrev) / RVR_ENCODER_CPR) * wheelCirc;
  const float vL = distL / dt;
  const float vR = distR / dt;
  g_vMeas = 0.5f * (vL + vR);
  g_wMeas = (vR - vL) / RVR_TRACK_M;
  g_encLPrev = g_encL;
  g_encRPrev = g_encR;
#else
  g_vMeas = 0.0f;
  g_wMeas = 0.0f;
#endif

#if RVR_ENABLE_BATTERY
  const float pinV = ((float)analogRead(RVR_BATT_ADC_PIN) / RVR_ADC_COUNTS) * RVR_ADC_REF_V;
  const float packV = pinV * RVR_BATT_DIVIDER * RVR_BATT_CAL;
  g_battV += (packV - g_battV) * RVR_BATT_ALPHA;
#endif

  // ---- 5. quiet transition logging ----
  if (g_watchdog != g_loggedWatchdog) {
    g_loggedWatchdog = g_watchdog;
    Serial.printf("[safety] watchdog %s\n", g_watchdog ? "TRIPPED (motors cut)" : "cleared");
  }
  if (g_reflexFlag != g_loggedReflex) {
    g_loggedReflex = g_reflexFlag;
    if (g_reflexFlag) {
      Serial.printf("[safety] reflex engaged (front=%s)\n", g_frontValid ? "valid" : "INVALID");
    } else {
      Serial.println("[safety] reflex cleared");
    }
  }
  if (!g_frontValid && (uint32_t)(now - g_lastSensorLogMs) > 2000u) {
    g_lastSensorLogMs = now;
    Serial.println("[safety] front sensor has no valid reading; car is held stopped");
  }
}

// =====================================================================
// 17. TELEMETRY
// =====================================================================

// Writes a JSON null. Assigning nullptr lands on ArduinoJson's null
// converter (and a null string also serialises as null), so this is
// portable across 6.x/7.x.
static void rvrSetNull(JsonObject obj, const char* key) {
  obj[key] = nullptr;
}

static void rvrSendTelemetry(uint32_t now) {
  if (!g_telemTargetValid) {
    return;
  }

  JsonDocument doc;
  JsonObject root = doc.to<JsonObject>();

  root["t"] = g_cmd.t;             // echo of the last accepted command's t
  root["seq"] = g_cmd.seq;         // echo, so the laptop can correlate

  // The protocol types dist_front_m as a plain float, so a failed read is
  // reported as 0.0 (which is also the conservative interpretation) plus
  // "reflex": true. dist_rear_m is explicitly nullable.
  root["dist_front_m"] = g_frontValid ? g_frontM : 0.0f;
  if (g_rearValid) {
    root["dist_rear_m"] = g_rearM;
  } else {
    rvrSetNull(root, "dist_rear_m");
  }

#if RVR_ENABLE_ENCODERS
  root["enc_l"] = g_encL;
  root["enc_r"] = g_encR;
#else
  rvrSetNull(root, "enc_l");
  rvrSetNull(root, "enc_r");
#endif

  root["v_meas"] = g_vMeas;
  root["w_meas"] = g_wMeas;
  root["batt_v"] = g_battV;
  root["reflex"] = g_reflexFlag;
  root["watchdog"] = g_watchdog;
  root["uptime_s"] = (float)((uint32_t)(now - g_bootMs)) / 1000.0f;

  // Scan (v1) status: the id currently active or most recently finished
  // (0 = none yet), and the phase. These two fields leave ~170 B of
  // headroom under RVR_TX_BUF (measured, see README).
  root["scan_seq"] = g_scan.lastId;
  root["scan_state"] = rvrScanStateName();

  char buf[RVR_TX_BUF];
  const size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf)) {
    // Never send a truncated datagram: better to skip a sample than to
    // hand the laptop malformed JSON.
    return;
  }

  g_udp.beginPacket(g_peerIp, g_peerPort);
  g_udp.write((const uint8_t*)buf, n);
  g_udp.endPacket();
}

// =====================================================================
// 18. SETUP / LOOP
// =====================================================================

static void rvrInitEncoderPins() {
#if RVR_ENABLE_ENCODERS
  pinMode(RVR_ENC_L_A, INPUT_PULLUP);
  pinMode(RVR_ENC_L_B, INPUT_PULLUP);
  pinMode(RVR_ENC_R_A, INPUT_PULLUP);
  pinMode(RVR_ENC_R_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(RVR_ENC_L_A), rvrEncLeftIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(RVR_ENC_R_A), rvrEncRightIsr, CHANGE);
#endif
}

void setup() {
  Serial.begin(RVR_SERIAL_BAUD);
  delay(200);

  g_bootMs = millis();
  g_lastTickMs = g_bootMs;
  g_lastTelemMs = g_bootMs;

  Serial.println();
  Serial.println("=== jev-rover reference firmware (ESP32) ===");

  // Motors first, before anything can ask them to move: the drive object
  // starts with all outputs at zero (RVR_MOTOR_STBY is raised inside
  // RvrDrive::begin()).
  g_drive.begin();

  // Distance sensors. The I2C bus is also needed when the scan sensor is
  // the VL53L1X, even if the front distance sensor is an HC-SR04.
#if (RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X) || \
    (RVR_SCAN_SENSOR == RVR_SCAN_SENSOR_VL53L1X)
  Wire.begin(RVR_I2C_SDA, RVR_I2C_SCL);
  Wire.setClock(400000);
#endif
  const bool frontOk = g_frontSensor.begin(RVR_HCSR04_TRIG_PIN, RVR_HCSR04_ECHO_PIN);
  if (frontOk) {
    Serial.println("[sensor] front distance sensor ready");
  } else {
    Serial.println("[sensor] FRONT SENSOR INIT FAILED - the rover will refuse to move (fail-safe)");
  }
#if RVR_ENABLE_REAR_SENSOR
  const bool rearOk = g_rearSensor.begin(RVR_HCSR04_REAR_TRIG_PIN, RVR_HCSR04_REAR_ECHO_PIN);
  Serial.printf("[sensor] rear distance sensor %s\n", rearOk ? "ready" : "FAILED");
#endif

  // Scan sensor + servo (HARDWARE-UNVERIFIED; see README "Scan mode").
  // The sensor init runs here and only here: it may block internally, and
  // it is unreachable from the scan FSM (interleave rule 3).
  const bool scanOk = rvrScanSensorBegin();
  if (scanOk) {
    Serial.println("[scan] scan sensor ready");
  } else {
    Serial.println("[scan] SCAN SENSOR INIT FAILED - sweeps will record timeouts only");
  }
  rvrServoBegin();
  Serial.printf("[scan] servo ch=%u pin=%u @ %u Hz; grid=%u ms budget=%u ms timeout=%u ms\n",
                (unsigned)RVR_SERVO_CH, (unsigned)RVR_SERVO_PIN,
                (unsigned)RVR_SERVO_HZ, (unsigned)RVR_SCAN_GRID_MS,
                (unsigned)RVR_SCAN_BUDGET_MS, (unsigned)RVR_SCAN_TIMEOUT_MS);

  rvrInitEncoderPins();

#if RVR_ENABLE_BATTERY
  g_battV = ((float)analogRead(RVR_BATT_ADC_PIN) / RVR_ADC_COUNTS) * RVR_ADC_REF_V
            * RVR_BATT_DIVIDER * RVR_BATT_CAL;
#else
  g_battV = 0.0f;
#endif

  // WiFi. Nothing here is safety-critical: if the link never comes up,
  // the watchdog keeps the motors cut, which is the correct resting state.
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);   // lower UDP latency; the watchdog is only 400 ms
#ifdef ROVER_STATIC_IP
  WiFi.config(IPAddress(ROVER_IP), IPAddress(ROVER_GATEWAY), IPAddress(ROVER_SUBNET));
#endif
  WiFi.begin(RVR_WIFI_SSID, RVR_WIFI_PASSWORD);

  Serial.printf("[wifi] connecting to \"%s\"", RVR_WIFI_SSID);
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED &&
         (uint32_t)(millis() - start) < RVR_WIFI_TIMEOUT_MS) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] connected, ip=%s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("[wifi] NOT connected yet - still trying in the background");
    Serial.println("[wifi] motors stay cut until a valid command arrives");
  }

  g_udp.begin(RVR_UDP_PORT);
  Serial.printf("[udp] listening on port %u\n", (unsigned)RVR_UDP_PORT);

#if RVR_OTA
  // Wireless updates. begin() starts the mDNS responder and the espota UDP
  // listener; handle() is serviced from loop(). Neither touches g_cmd, so
  // the watchdog and the reflex behave exactly as before during a flash.
  ArduinoOTA.setHostname(ROVER_HOSTNAME);
  if (strlen(ROVER_OTA_PASSWORD) > 0) {
    ArduinoOTA.setPassword(ROVER_OTA_PASSWORD);
  }
  ArduinoOTA.onStart([]() { Serial.println("[ota] update starting"); });
  ArduinoOTA.onEnd([]() { Serial.println("\n[ota] done; rebooting"); });
  ArduinoOTA.onError([](ota_error_t e) {
    Serial.printf("[ota] error %u\n", (unsigned)e);
  });
  ArduinoOTA.begin();
  Serial.printf("[ota] ready as %s.local (espota UDP 3232)\n", ROVER_HOSTNAME);
#endif
  Serial.printf("[proto] v <= %.2f m/s, w <= %.2f rad/s, watchdog %u ms\n",
                RVR_MAX_V, RVR_MAX_W, (unsigned)RVR_WATCHDOG_MS);
  Serial.println("[safety] watchdog armed, reflex armed. Wheels off the ground for first tests.");
}

void loop() {
  const uint32_t now = millis();

  // Incoming commands first: they are the only input the control loop needs.
  // (Interleave rule 1: this stays the first statement of every pass.)
  rvrPollUdp(now);

#if RVR_OTA
  // Non-blocking OTA service. Like rvrPollUdp it never blocks the loop and
  // never touches the watchdog: a flash without commands means stopped
  // motors (which is what we want while reflashing).
  ArduinoOTA.handle();
#endif

  // Servo ToF sweep: one bounded step per pass, never the whole sweep.
  rvrScanStep(now);

  if ((uint32_t)(now - g_lastTickMs) >= RVR_TICK_MS) {
    g_lastTickMs = now;
    rvrControlTick(now);
  }

  if ((uint32_t)(now - g_lastTelemMs) >= RVR_TELEM_MS) {
    g_lastTelemMs = now;
    rvrSendTelemetry(now);
  }
}
