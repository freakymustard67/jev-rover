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
 * THIS IS REFERENCE FIRMWARE
 * ---------------------------------------------------------------------
 * Every pin and tuning constant in section 2 below is an EXAMPLE VALUE
 * chosen to be plausible, not to match your board. Adapt them to your
 * wiring before flashing. The protocol is the only hard contract.
 * See firmware/README.md for wiring, flashing, and behaviour notes.
 *
 * Libraries: WiFi.h / WiFiUdp.h (ESP32 core), ArduinoJson v7,
 *            Adafruit_VL53L0X (+ Adafruit BusIO) when using the default
 *            distance sensor. No other dependencies.
 * ===================================================================== */

// =====================================================================
//  1. HARDWARE SELECTION  (must be defined before the #includes)
// =====================================================================

#define RVR_SENSOR_VL53L0X 1
#define RVR_SENSOR_HCSR04  2

// EXAMPLE default: a VL53L0X time-of-flight sensor on I2C.
// Switch to RVR_SENSOR_HCSR04 for the HC-SR04 (pulseIn) fallback.
#define RVR_DISTANCE_SENSOR RVR_SENSOR_VL53L0X

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

#if RVR_ENABLE_REAR_SENSOR && (RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X)
#error "RVR_ENABLE_REAR_SENSOR=1 needs the HC-SR04 driver: two VL53L0X sensors share I2C address 0x29 and re-addressing via XSHUT is not implemented. Set RVR_ENABLE_REAR_SENSOR to 0, or set RVR_DISTANCE_SENSOR to RVR_SENSOR_HCSR04."
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

#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
  #include <Adafruit_VL53L0X.h>
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
//  9. GLOBAL STATE
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

// =====================================================================
// 10. ENCODER INTERRUPTS  (must stay tiny and never block)
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
// 11. COMMAND PARSING  (all outputs are primitives so that this file
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
// telemetry destination.
static bool rvrParseCommand(const char* json, size_t len,
                           float* outT, int32_t* outSeq,
                           float* outV, float* outW, int32_t* outTtlMs) {
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

  *outT = t;
  *outSeq = (int32_t)rvrClamp(seqRounded, -2147483647.0f, 2147483647.0f);
  *outV = v;
  *outW = w;
  *outTtlMs = ttlMs;
  return true;
}

// =====================================================================
// 12. UDP
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
    if (!rvrParseCommand(g_rxBuf, (size_t)len, &t, &seq, &v, &w, &ttlMs)) {
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
  }
}

// =====================================================================
// 13. CONTROL TICK  (watchdog -> reflex -> mixing -> motors)
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
// 14. TELEMETRY
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
// 15. SETUP / LOOP
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

  // Distance sensors.
#if RVR_DISTANCE_SENSOR == RVR_SENSOR_VL53L0X
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
  Serial.printf("[proto] v <= %.2f m/s, w <= %.2f rad/s, watchdog %u ms\n",
                RVR_MAX_V, RVR_MAX_W, (unsigned)RVR_WATCHDOG_MS);
  Serial.println("[safety] watchdog armed, reflex armed. Wheels off the ground for first tests.");
}

void loop() {
  const uint32_t now = millis();

  // Incoming commands first: they are the only input the control loop needs.
  rvrPollUdp(now);

  if ((uint32_t)(now - g_lastTickMs) >= RVR_TICK_MS) {
    g_lastTickMs = now;
    rvrControlTick(now);
  }

  if ((uint32_t)(now - g_lastTelemMs) >= RVR_TELEM_MS) {
    g_lastTelemMs = now;
    rvrSendTelemetry(now);
  }
}
