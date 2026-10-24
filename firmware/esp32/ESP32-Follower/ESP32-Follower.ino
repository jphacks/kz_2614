#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <driver/gpio.h>
#include <esp_arduino_version.h>
#include <cJSON.h>
#include <math.h>
#include "WiFiConfig.h"
#include "TailConfig.h"

// ESP32 DevKit / ESP32 Dev Module. Power all SG90s from a separate 4.8-6 V supply.
// The Reader's 0-180 target is divided equally over three serial joints.
constexpr uint8_t SERVO_PIN = 18;
constexpr uint8_t ADDITIONAL_SERVO_PINS[] = {19, 21};
constexpr size_t ADDITIONAL_SERVO_COUNT = sizeof(ADDITIONAL_SERVO_PINS) /
    sizeof(ADDITIONAL_SERVO_PINS[0]);
constexpr uint16_t NEUTRAL_PULSE_US = 1500;
constexpr int JOINT_COUNT = 3;
constexpr bool BOOT_IN_NEUTRAL_MODE = false;
// Many ESP32 boards wire their user LED to GPIO2. Use an external LED if yours does not.
constexpr uint8_t ROLE_LED_PIN = 2;
constexpr bool ROLE_LED_ACTIVE_HIGH = true;
constexpr unsigned long ROLE_LED_CYCLE_MS = 1800;
constexpr int MAX_ANGLE = 180;  // Maps the calibrated pulse endpoints to 0 and 180.
constexpr uint32_t PWM_HZ = 50;
constexpr uint8_t PWM_BITS = 14;
constexpr uint32_t PWM_PERIOD_US = 20000;
// Boot at the proven range; widen each end only while observing the real servo.
// Stop expanding if the servo buzzes or reaches a mechanical end stop.
constexpr uint16_t DEFAULT_MIN_PULSE_US = 1000;
constexpr uint16_t DEFAULT_MAX_PULSE_US = 2000;
// Calibration bounds only; stop before the individual servo's hard stops.
constexpr uint16_t MIN_ALLOWED_PULSE_US = 500;
constexpr uint16_t MAX_ALLOWED_PULSE_US = 2500;
constexpr uint16_t PULSE_STEP_US = 50;
constexpr uint16_t CURL_EXTRA_STEP_US = 50;
constexpr uint16_t CURL_EXTRA_MAX_US = 150;
// Konkon speed is the final link's commanded angle change per second.
constexpr int KONKON_MIN_SPEED_DPS = 1;
constexpr int KONKON_MAX_SPEED_DPS = 180;
constexpr unsigned long KONKON_UPDATE_MS = 20;
constexpr unsigned long KONKON_MAX_ELAPSED_MS = 50;
// One "tap" means arriving at one configured endpoint, not a whole round trip.
// Endpoint values come from the private TailConfig.h after physical calibration.
constexpr uint32_t TAIL_MIN_DURATION_MS = 500;
constexpr uint32_t TAIL_MAX_DURATION_MS = 5000;
constexpr double TAIL_MIN_RATE_HZ = 0.3;
constexpr double TAIL_PROTOCOL_MAX_RATE_HZ = 1.0;
constexpr uint32_t TAIL_BUDGET_WINDOW_MS = 60000;
constexpr uint32_t TAIL_BUDGET_MAX_MS = 10000;
constexpr size_t TAIL_MAX_JSON_BYTES = 256;
#if ESP_ARDUINO_VERSION_MAJOR < 3
constexpr uint8_t PWM_CHANNEL = 0;
#endif

WebServer server(80);
Preferences pulseStore;
bool networkReady = false;
bool serverStarted = false;
bool mdnsStarted = false;
bool servoReady = false;
bool additionalServoReady[ADDITIONAL_SERVO_COUNT] = {};
bool additionalPinInputReady[ADDITIONAL_SERVO_COUNT] = {};
bool pulseStoreReady = false;
bool servoPinInputReady = false;
bool apiMode = false;
bool neutralMode = BOOT_IN_NEUTRAL_MODE;
bool relaxMode = false;
bool konkonActive = false;
bool tailTapActive = false;
int currentAngle = 90;
uint16_t minPulseUs = DEFAULT_MIN_PULSE_US;
uint16_t maxPulseUs = DEFAULT_MAX_PULSE_US;
uint16_t currentPulseUs = 1500;
uint16_t curlExtraUs = 0;
int konkonStartAngle = 0;
int konkonEndAngle = 0;
int konkonSpeedDps = 0;
int konkonTargetAngle = 0;
int32_t konkonPositionMilliDeg = 90000;
unsigned long konkonLastUpdateMs = 0;
unsigned long tailTapStartedMs = 0;
uint32_t tailTapDurationMs = 0;
float tailTapRateHz = 0;
unsigned long tailBudgetWindowStartedMs = 0;
uint32_t tailBudgetReservedMs = 0;
bool tailBudgetStarted = false;
unsigned long lastWifiStatusMs = 0;

void onWifiDisconnected(WiFiEvent_t, WiFiEventInfo_t info) {
  Serial.print("Servo Wi-Fi disconnect reason: ");
  Serial.println(info.wifi_sta_disconnected.reason);
}

void updateRoleLed() {
  // Two short blinks per cycle identify the Follower (receiver).
  const unsigned long phase = millis() % ROLE_LED_CYCLE_MS;
  const bool on = phase < 180 || (phase >= 360 && phase < 540);
  static bool wasOn = false;
  if (on != wasOn) {
    digitalWrite(ROLE_LED_PIN, on == ROLE_LED_ACTIVE_HIGH ? HIGH : LOW);
    wasOn = on;
  }
}

uint32_t dutyFromPulse(uint32_t pulseUs) {
  return (pulseUs * (1UL << PWM_BITS) + PWM_PERIOD_US / 2) /
      PWM_PERIOD_US;
}

bool allServosReady() {
  if (!servoReady) return false;
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    if (!additionalServoReady[i]) return false;
  }
  return true;
}

bool writeAllServosPulse(uint16_t pulseUs, int finalAngle) {
  if (!allServosReady()) return false;
  const uint32_t duty = dutyFromPulse(pulseUs);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  bool ok = ledcWrite(SERVO_PIN, duty);
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    ok = ledcWrite(ADDITIONAL_SERVO_PINS[i], duty) && ok;
  }
#else
  ledcWrite(PWM_CHANNEL, duty);
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    ledcWrite(PWM_CHANNEL + i + 1, duty);
  }
  const bool ok = true;
#endif
  if (ok) {
    currentAngle = finalAngle;
    currentPulseUs = pulseUs;
  }
  return ok;
}

bool writeServoAngle(int angle) {
  // Each joint moves one third of the full servo span around 1500 us.
  // 0 / 90 / 180 final degrees -> 1200 / 1500 / 1800 us with 600-2400 calibration.
  const int32_t offsetUs = (static_cast<int32_t>(angle - MAX_ANGLE / 2) *
      (maxPulseUs - minPulseUs)) / (MAX_ANGLE * JOINT_COUNT);
  int32_t pulseUs = NEUTRAL_PULSE_US + offsetUs;
  if (angle < MAX_ANGLE / 2) {
    pulseUs -= (static_cast<int32_t>(curlExtraUs) *
        (MAX_ANGLE / 2 - angle)) / (MAX_ANGLE / 2);
  }
  return writeAllServosPulse(static_cast<uint16_t>(pulseUs), angle);
}

bool centerAllServos() {
  return writeAllServosPulse(NEUTRAL_PULSE_US, MAX_ANGLE / 2);
}

bool stopAllServoPwm() {
  if (!allServosReady()) return false;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  bool ok = ledcWrite(SERVO_PIN, 0);
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    ok = ledcWrite(ADDITIONAL_SERVO_PINS[i], 0) && ok;
  }
#else
  ledcWrite(PWM_CHANNEL, 0);
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    ledcWrite(PWM_CHANNEL + i + 1, 0);
  }
  const bool ok = true;
#endif
  if (ok) {
    konkonActive = false;
    tailTapActive = false;
    apiMode = false;
    neutralMode = false;
    relaxMode = true;
  }
  return ok;
}

void attachAdditionalServos() {
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    const uint8_t pin = ADDITIONAL_SERVO_PINS[i];
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    additionalServoReady[i] = ledcAttach(pin, PWM_HZ, PWM_BITS);
    if (additionalServoReady[i]) {
      additionalServoReady[i] = ledcWrite(pin, dutyFromPulse(NEUTRAL_PULSE_US));
    }
#else
    const uint8_t channel = PWM_CHANNEL + i + 1;
    additionalServoReady[i] = ledcSetup(channel, PWM_HZ, PWM_BITS) != 0;
    if (additionalServoReady[i]) {
      ledcAttachPin(pin, channel);
      ledcWrite(channel, dutyFromPulse(NEUTRAL_PULSE_US));
    }
#endif
    if (additionalServoReady[i]) {
      additionalPinInputReady[i] =
          gpio_input_enable(static_cast<gpio_num_t>(pin)) == ESP_OK;
    }
    Serial.print("Servo GPIO");
    Serial.print(pin);
    Serial.println(additionalServoReady[i] ? ": ready at 1500 us" :
        ": PWM unavailable");
  }
}

void printAdditionalServos() {
  for (size_t i = 0; i < ADDITIONAL_SERVO_COUNT; ++i) {
    const uint8_t pin = ADDITIONAL_SERVO_PINS[i];
    Serial.print("GPIO");
    Serial.print(pin);
    Serial.print(" HIGH time: ");
    if (!additionalServoReady[i]) {
      Serial.println("PWM unavailable");
      continue;
    }
    if (additionalPinInputReady[i]) {
      const unsigned long observedHighUs = pulseIn(pin, HIGH, 30000UL);
      if (observedHighUs != 0) {
        Serial.print(observedHighUs);
        Serial.println(" us HIGH");
        continue;
      }
      if (relaxMode) {
        Serial.println("timeout (no HIGH pulse)");
        continue;
      }
    }
    if (relaxMode) Serial.println("PWM stopped; GPIO readback unavailable");
    else {
      Serial.print(currentPulseUs);
      Serial.println(" us commanded; GPIO readback unavailable");
    }
  }
}

bool validPulseRange(int minimum, int maximum) {
  return minimum >= MIN_ALLOWED_PULSE_US && minimum <= DEFAULT_MIN_PULSE_US &&
      maximum >= DEFAULT_MAX_PULSE_US && maximum <= MAX_ALLOWED_PULSE_US;
}

void printPulseRange() {
  Serial.print("Servo pulse min/max: ");
  Serial.print(minPulseUs);
  Serial.print('/');
  Serial.print(maxPulseUs);
  Serial.print(" us; last final target/each pulse: ");
  Serial.print(currentAngle);
  Serial.print('/');
  Serial.print(currentPulseUs);
  Serial.print(" us; low-side curl extra: ");
  Serial.print(curlExtraUs);
  Serial.println(" us");
  if (relaxMode) Serial.println("Relax mode: PWM stopped on all three servo pins");
  if (!servoReady) {
    Serial.println("LEDC readback: PWM unavailable");
    return;
  }
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const uint32_t actualHz = ledcReadFreq(SERVO_PIN);
  const uint32_t actualDuty = ledcRead(SERVO_PIN);
#else
  const uint32_t actualHz = ledcReadFreq(PWM_CHANNEL);
  const uint32_t actualDuty = ledcRead(PWM_CHANNEL);
#endif
  Serial.print("LEDC readback Hz/duty: ");
  Serial.print(actualHz);
  Serial.print('/');
  Serial.print(actualDuty);
  if (actualHz != 0) {
    const uint64_t numerator = static_cast<uint64_t>(actualDuty) * 1000000ULL;
    const uint32_t readbackPulseUs = (numerator +
        static_cast<uint64_t>(actualHz) * (1ULL << PWM_BITS) / 2) /
        (static_cast<uint64_t>(actualHz) * (1ULL << PWM_BITS));
    Serial.print("; calculated pulse: ");
    Serial.print(readbackPulseUs);
    Serial.print(" us");
  }
  Serial.println();
  if (servoPinInputReady) {
    // Read the GPIO pad itself; this does not verify the remote servo connector.
    const unsigned long observedHighUs = pulseIn(SERVO_PIN, HIGH, 30000UL);
    Serial.print("GPIO18 HIGH time: ");
    if (observedHighUs == 0) Serial.println("timeout");
    else {
      Serial.print(observedHighUs);
      Serial.println(" us");
    }
  }
}

void loadPulseRange() {
  pulseStoreReady = pulseStore.begin("servo-pwm", false);
  if (!pulseStoreReady) {
    Serial.println("Servo pulse storage unavailable; using 1000/2000 us");
    return;
  }
  const uint32_t fallback = (static_cast<uint32_t>(DEFAULT_MAX_PULSE_US) << 16) |
      DEFAULT_MIN_PULSE_US;
  const uint32_t packed = pulseStore.getUInt("range", fallback);
  const int minimum = packed & 0xFFFF;
  const int maximum = packed >> 16;
  if (validPulseRange(minimum, maximum)) {
    minPulseUs = minimum;
    maxPulseUs = maximum;
  } else {
    Serial.println("Saved servo pulse range invalid; using 1000/2000 us");
  }
  const uint32_t savedCurlExtraUs = pulseStore.getUInt("curl", 0);
  if (savedCurlExtraUs <= CURL_EXTRA_MAX_US &&
      savedCurlExtraUs % CURL_EXTRA_STEP_US == 0) {
    curlExtraUs = savedCurlExtraUs;
  } else {
    Serial.println("Saved curl adjustment invalid; using 0 us");
  }
  printPulseRange();
}

void adjustPulseRange(int minDelta, int maxDelta) {
  const int nextMin = static_cast<int>(minPulseUs) + minDelta;
  const int nextMax = static_cast<int>(maxPulseUs) + maxDelta;
  if (!validPulseRange(nextMin, nextMax)) {
    Serial.println("Pulse limit reached; range unchanged");
    return;
  }
  const uint16_t previousMin = minPulseUs;
  const uint16_t previousMax = maxPulseUs;
  minPulseUs = nextMin;
  maxPulseUs = nextMax;
  if (!neutralMode && !relaxMode && !writeServoAngle(currentAngle)) {
    minPulseUs = previousMin;
    maxPulseUs = previousMax;
    Serial.println("Servo PWM write failed; range unchanged");
    return;
  }
  printPulseRange();
  Serial.println("Temporary range. Send s to save after checking both ends.");
}

void adjustCurlExtra(int deltaUs) {
  const int next = static_cast<int>(curlExtraUs) + deltaUs;
  if (next < 0 || next > CURL_EXTRA_MAX_US) {
    Serial.println("Curl adjustment limit reached");
    return;
  }
  const uint16_t previous = curlExtraUs;
  curlExtraUs = next;
  if (!neutralMode && !relaxMode && !writeServoAngle(currentAngle)) {
    curlExtraUs = previous;
    Serial.println("Servo PWM write failed; curl adjustment unchanged");
    return;
  }
  printPulseRange();
  Serial.println("Temporary curl adjustment. Send s to save after checking movement.");
}

void handleSerialCommands() {
  while (Serial.available()) {
    switch (Serial.read()) {
      case '1': adjustPulseRange(-PULSE_STEP_US, 0); break; // Widen low end.
      case '2': adjustPulseRange(PULSE_STEP_US, 0); break;  // Undo low-end step.
      case '3': adjustPulseRange(0, PULSE_STEP_US); break;  // Widen high end.
      case '4': adjustPulseRange(0, -PULSE_STEP_US); break; // Undo high-end step.
      case '5': adjustCurlExtra(CURL_EXTRA_STEP_US); break;  // Curl more at 0.
      case '6': adjustCurlExtra(-CURL_EXTRA_STEP_US); break; // Undo curl step.
      case 'v': printPulseRange(); printAdditionalServos(); break;
      case 'd': {
        const bool stopped = stopAllServoPwm();
        Serial.println(stopped ? "Relax mode: GPIO18/19/21 PWM stopped" :
            "Relax mode failed: servo PWM unavailable");
        break;
      }
      case 'n': {
        const bool centered = centerAllServos();
        if (centered) {
          relaxMode = false;
          neutralMode = true;
          apiMode = false;
          konkonActive = false;
          tailTapActive = false;
        }
        Serial.println(centered ? "Neutral mode: GPIO18/19/21 at 1500 us" :
            "Neutral mode failed: servo PWM unavailable");
        break;
      }
      case 't':
        if (relaxMode && !centerAllServos()) {
          Serial.println("Teleop mode failed: servo PWM unavailable");
          break;
        }
        relaxMode = false;
        neutralMode = false;
        apiMode = false;
        konkonActive = false;
        tailTapActive = false;
        Serial.println("Teleop mode enabled for all three servos");
        break;
      case 's': {
        const uint32_t packed = (static_cast<uint32_t>(maxPulseUs) << 16) |
            minPulseUs;
        const bool savedRange = pulseStoreReady &&
            pulseStore.putUInt("range", packed) == sizeof(uint32_t);
        const bool savedCurl = pulseStoreReady &&
            pulseStore.putUInt("curl", curlExtraUs) == sizeof(uint32_t);
        Serial.println(savedRange && savedCurl ? "Servo settings saved" :
            "Servo settings NOT fully saved");
        break;
      }
      case 'r': {
        if (pulseStoreReady && pulseStore.isKey("range") &&
            !pulseStore.remove("range")) {
          Serial.println("Could not erase saved servo pulse range");
          break;
        }
        if (pulseStoreReady && pulseStore.isKey("curl") &&
            !pulseStore.remove("curl")) {
          Serial.println("Could not erase saved curl adjustment");
          break;
        }
        minPulseUs = DEFAULT_MIN_PULSE_US;
        maxPulseUs = DEFAULT_MAX_PULSE_US;
        curlExtraUs = 0;
        if (!neutralMode && !relaxMode) writeServoAngle(currentAngle);
        Serial.println("Servo settings reset to 1000/2000 us and 0 curl extra");
        printPulseRange();
        break;
      }
    }
  }
}

bool parseIntegerArg(const char *name, int minimum, int maximum, int &result) {
  if (!server.hasArg(name)) return false;
  String value = server.arg(name);
  if (value.length() == 0 || value.length() > 3) return false;
  for (size_t i = 0; i < value.length(); ++i) {
    if (value[i] < '0' || value[i] > '9') return false;
  }
  result = value.toInt();
  return result >= minimum && result <= maximum;
}

void sendStatus() {
  const uint32_t tailRemainingMs = tailTapActive ?
      tailTapDurationMs - min(static_cast<uint32_t>(millis() - tailTapStartedMs),
          tailTapDurationMs) : 0;
  String body = "{\"angle\":" + String(currentAngle) +
      ",\"mode\":\"" + (relaxMode ? "relax" : (neutralMode ? "neutral" :
          (konkonActive ? "konkon" : (apiMode ? "api" : "teleop")))) +
      "\",\"pwm_enabled\":" + (relaxMode ? "false" : "true") +
      ",\"pulse_us\":" + (relaxMode ? "null" : String(currentPulseUs)) +
      ",\"joint_angle_deg\":" + (relaxMode ? "null" : String(90.0f +
          (static_cast<int32_t>(currentPulseUs) - NEUTRAL_PULSE_US) *
          180.0f / (maxPulseUs - minPulseUs), 1)) +
      ",\"curl_extra_us\":" + String(curlExtraUs) +
      ",\"pulse_min_us\":" + String(minPulseUs) +
      ",\"pulse_max_us\":" + String(maxPulseUs) +
      ",\"neutral_pulse_us\":" + String(NEUTRAL_PULSE_US) +
      ",\"joint_count\":" + String(JOINT_COUNT) +
      ",\"konkon_start_angle\":" + (konkonActive ? String(konkonStartAngle) : "null") +
      ",\"konkon_end_angle\":" + (konkonActive ? String(konkonEndAngle) : "null") +
      ",\"konkon_speed_dps\":" + (konkonActive ? String(konkonSpeedDps) : "null") +
      ",\"konkon_target\":" + (konkonActive ? String(konkonTargetAngle) : "null") +
      ",\"tail_tap_active\":" + (tailTapActive ? "true" : "false") +
      ",\"tail_remaining_ms\":" + (tailTapActive ? String(tailRemainingMs) : "null") +
      ",\"servo_pins\":[18,19,21]}";
  server.send(200, "application/json; charset=utf-8", body);
}

void setAngle(bool fromApi) {
  int angle;
  if (!parseIntegerArg("angle", 0, MAX_ANGLE, angle)) {
    server.send(400, "text/plain", "angle must be an integer from 0 to 180");
    return;
  }
  if (neutralMode || relaxMode) {
    server.send(409, "text/plain", relaxMode ?
        "Relax mode is active" : "Neutral mode is active");
    return;
  }
  if (!fromApi && apiMode) {
    server.send(409, "text/plain", "API mode is active");
    return;
  }
  if (!writeServoAngle(angle)) {
    server.send(503, "text/plain", "Servo PWM unavailable");
    return;
  }
  if (fromApi) {
    konkonActive = false;
    tailTapActive = false;
    apiMode = true;
  }
  sendStatus();
}

void beginKonkon(int startAngle, int endAngle, int speed) {
  konkonStartAngle = startAngle;
  konkonEndAngle = endAngle;
  konkonSpeedDps = speed;
  konkonPositionMilliDeg = static_cast<int32_t>(currentAngle) * 1000;
  konkonTargetAngle = currentAngle == konkonStartAngle ?
      konkonEndAngle : konkonStartAngle;
  konkonLastUpdateMs = millis();
  apiMode = true;
  konkonActive = true;
  tailTapActive = false;
}

void startKonkon() {
  int startAngle;
  int endAngle;
  int speed;
  if (!parseIntegerArg("start_angle", 0, MAX_ANGLE, startAngle) ||
      !parseIntegerArg("end_angle", 0, MAX_ANGLE, endAngle) ||
      startAngle == endAngle ||
      !parseIntegerArg("speed", KONKON_MIN_SPEED_DPS,
          KONKON_MAX_SPEED_DPS, speed)) {
    server.send(400, "text/plain", "start_angle and end_angle must differ within 0..180; speed must be 1..180 deg/s");
    return;
  }
  if (neutralMode || relaxMode) {
    server.send(409, "text/plain", relaxMode ?
        "Relax mode is active" : "Neutral mode is active");
    return;
  }
  if (!allServosReady()) {
    server.send(503, "text/plain", "Servo PWM unavailable");
    return;
  }
  beginKonkon(startAngle, endAngle, speed);
  sendStatus();
}

void updateKonkon() {
  if (!konkonActive || neutralMode || relaxMode) return;
  if (WiFi.status() != WL_CONNECTED) {
    konkonActive = false;
    tailTapActive = false;
    Serial.println("Konkon stopped: Wi-Fi disconnected; holding last angle");
    return;
  }
  const unsigned long now = millis();
  const unsigned long elapsed = now - konkonLastUpdateMs;
  if (elapsed < KONKON_UPDATE_MS) return;
  konkonLastUpdateMs = now;
  const int32_t stepMilliDeg = konkonSpeedDps *
      static_cast<int32_t>(min(elapsed, KONKON_MAX_ELAPSED_MS));
  const int32_t targetMilliDeg = konkonTargetAngle * 1000;
  if (konkonPositionMilliDeg < targetMilliDeg) {
    konkonPositionMilliDeg = min(konkonPositionMilliDeg + stepMilliDeg,
        targetMilliDeg);
  } else {
    konkonPositionMilliDeg = max(konkonPositionMilliDeg - stepMilliDeg,
        targetMilliDeg);
  }
  const int nextAngle = (konkonPositionMilliDeg + 500) / 1000;
  if (nextAngle != currentAngle && !writeServoAngle(nextAngle)) {
    konkonActive = false;
    tailTapActive = false;
    Serial.println("Konkon stopped: servo PWM write failed");
    return;
  }
  if (konkonPositionMilliDeg == targetMilliDeg) {
    konkonTargetAngle = konkonTargetAngle == konkonStartAngle ?
        konkonEndAngle : konkonStartAngle;
  }
}

void updateTailTap() {
  if (!tailTapActive) return;
  if (static_cast<uint32_t>(millis() - tailTapStartedMs) <
      tailTapDurationMs) return;
  tailTapActive = false;
  konkonActive = false;
  // Keep the last command; do not abruptly move the linked servos to center.
  apiMode = true;
  Serial.println("Tail tap duration elapsed; holding last angle");
}

void sendTailError(int code, const char *errorCode) {
  server.send(code, "application/json; charset=utf-8",
      String("{\"ok\":false,\"error\":{\"code\":\"") +
      errorCode + "\"}}");
}

void sendTailState() {
  const uint32_t remainingMs = tailTapActive ?
      tailTapDurationMs - min(static_cast<uint32_t>(millis() - tailTapStartedMs),
          tailTapDurationMs) : 0;
  const char *state = tailTapActive ? "tapping" :
      (konkonActive ? "moving" : "idle");
  const String body = String("{\"ok\":true,\"state\":\"") + state +
      "\",\"enabled\":" + (TAIL_MCP_API_ENABLED ? "true" : "false") +
      ",\"feedback\":\"command_only\"" +
      ",\"angle\":" + String(currentAngle) +
      ",\"remaining_ms\":" + (tailTapActive ? String(remainingMs) : "null") +
      ",\"rate_hz\":" + (tailTapActive ? String(tailTapRateHz, 3) : "null") +
      ",\"max_rate_hz\":" + String(TAIL_TESTED_MAX_RATE_HZ, 3) +
      ",\"start_angle\":" + String(TAIL_TAP_START_ANGLE) +
      ",\"end_angle\":" + String(TAIL_TAP_END_ANGLE) + "}";
  server.send(200, "application/json; charset=utf-8", body);
}

bool parseTailTapRequest(uint32_t &durationMs, float &rateHz) {
  if (!server.hasArg("plain")) return false;
  const String body = server.arg("plain");
  if (body.isEmpty() || body.length() > TAIL_MAX_JSON_BYTES) return false;
  cJSON *root = cJSON_ParseWithLength(body.c_str(), body.length());
  if (!root) return false;
  const cJSON *duration = cJSON_GetObjectItemCaseSensitive(root, "duration_ms");
  const cJSON *rate = cJSON_GetObjectItemCaseSensitive(root, "rate_hz");
  const bool valid = cJSON_IsObject(root) && cJSON_GetArraySize(root) == 2 &&
      cJSON_IsNumber(duration) && cJSON_IsNumber(rate) &&
      isfinite(duration->valuedouble) && isfinite(rate->valuedouble) &&
      duration->valuedouble >= TAIL_MIN_DURATION_MS &&
      duration->valuedouble <= TAIL_MAX_DURATION_MS &&
      floor(duration->valuedouble) == duration->valuedouble &&
      rate->valuedouble >= TAIL_MIN_RATE_HZ &&
      rate->valuedouble <= TAIL_PROTOCOL_MAX_RATE_HZ &&
      rate->valuedouble <= TAIL_TESTED_MAX_RATE_HZ;
  if (valid) {
    durationMs = static_cast<uint32_t>(duration->valuedouble);
    rateHz = static_cast<float>(rate->valuedouble);
  }
  cJSON_Delete(root);
  return valid;
}

void handleTailTap() {
  if (!TAIL_MCP_API_ENABLED) {
    sendTailError(403, "TAIL_CONTROL_DISABLED");
    return;
  }
  if (TAIL_TAP_START_ANGLE < 0 || TAIL_TAP_END_ANGLE > MAX_ANGLE ||
      TAIL_TAP_START_ANGLE >= TAIL_TAP_END_ANGLE ||
      TAIL_TESTED_MAX_RATE_HZ < TAIL_MIN_RATE_HZ ||
      TAIL_TESTED_MAX_RATE_HZ > TAIL_PROTOCOL_MAX_RATE_HZ) {
    sendTailError(503, "TAIL_RANGE_NOT_CALIBRATED");
    return;
  }
  uint32_t durationMs;
  float rateHz;
  if (!parseTailTapRequest(durationMs, rateHz)) {
    sendTailError(400, "INVALID_TAP_REQUEST");
    return;
  }
  if (neutralMode || relaxMode || konkonActive) {
    sendTailError(409, "TAIL_NOT_READY");
    return;
  }
  if (!allServosReady()) {
    sendTailError(503, "SERVO_PWM_UNAVAILABLE");
    return;
  }
  const unsigned long now = millis();
  if (!tailBudgetStarted ||
      static_cast<uint32_t>(now - tailBudgetWindowStartedMs) >=
          TAIL_BUDGET_WINDOW_MS) {
    tailBudgetStarted = true;
    tailBudgetWindowStartedMs = now;
    tailBudgetReservedMs = 0;
  }
  if (tailBudgetReservedMs + durationMs > TAIL_BUDGET_MAX_MS) {
    sendTailError(429, "TAIL_BUDGET_EXCEEDED");
    return;
  }
  // Each endpoint arrival counts as one tap: configured span / rate interval.
  const int speedDps = max(KONKON_MIN_SPEED_DPS, static_cast<int>(rateHz *
      (TAIL_TAP_END_ANGLE - TAIL_TAP_START_ANGLE) + 0.5f));
  // Visit the nearer endpoint first so a short request can cover both ends.
  const int firstAngle = abs(currentAngle - TAIL_TAP_START_ANGLE) <=
      abs(currentAngle - TAIL_TAP_END_ANGLE) ?
      TAIL_TAP_START_ANGLE : TAIL_TAP_END_ANGLE;
  const int secondAngle = firstAngle == TAIL_TAP_START_ANGLE ?
      TAIL_TAP_END_ANGLE : TAIL_TAP_START_ANGLE;
  beginKonkon(firstAngle, secondAngle, speedDps);
  tailBudgetReservedMs += durationMs;
  tailTapDurationMs = durationMs;
  tailTapRateHz = rateHz;
  tailTapStartedMs = now;
  tailTapActive = true;
  sendTailState();
}

void handleTailStop() {
  // Holding the last pulse stops movement without a sudden return to center.
  tailTapActive = false;
  konkonActive = false;
  if (!neutralMode && !relaxMode) apiMode = true;
  sendTailState();
}

void handleTailStatus() {
  sendTailState();
}

void registerRoutes() {
  server.on("/api/status", HTTP_GET, sendStatus);
  server.on("/api/teleop", HTTP_POST, []() { setAngle(false); });
  server.on("/api/angle", HTTP_POST, []() { setAngle(true); });
  server.on("/api/konkon", HTTP_POST, startKonkon);
  server.on("/api/tail/tap", HTTP_POST, handleTailTap);
  server.on("/api/tail/stop", HTTP_POST, handleTailStop);
  server.on("/api/tail/status", HTTP_GET, handleTailStatus);
  server.on("/api/mode", HTTP_POST, []() {
    if (!server.hasArg("mode")) {
      server.send(400, "text/plain", "mode must be neutral, teleop or relax");
      return;
    }
    if (server.arg("mode") == "relax") {
      if (!stopAllServoPwm()) {
        server.send(503, "text/plain", "Servo PWM unavailable");
        return;
      }
      sendStatus();
      return;
    }
    if (server.arg("mode") == "neutral") {
      if (!centerAllServos()) {
        server.send(503, "text/plain", "Servo PWM unavailable");
        return;
      }
      relaxMode = false;
      neutralMode = true;
      apiMode = false;
      konkonActive = false;
      tailTapActive = false;
      sendStatus();
      return;
    }
    if (server.arg("mode") != "teleop") {
      server.send(400, "text/plain", "mode must be neutral, teleop or relax");
      return;
    }
    if (relaxMode && !centerAllServos()) {
      server.send(503, "text/plain", "Servo PWM unavailable");
      return;
    }
    relaxMode = false;
    neutralMode = false;
    apiMode = false;
    konkonActive = false;
    tailTapActive = false;
    sendStatus();
  });
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
}

void maintainWifi() {
  if (WIFI_SSID[0] == '\0') return;
  if (WiFi.status() == WL_CONNECTED) {
    if (!networkReady) {
      networkReady = true;
      Serial.print("Servo ESP32 IP: ");
      Serial.println(WiFi.localIP());
      mdnsStarted = MDNS.begin("esp32-servo");
      if (mdnsStarted) MDNS.addService("http", "tcp", 80);
      else Serial.println("mDNS unavailable; use the IP address");
      // begin() can be called again after a Wi-Fi reconnect.
      server.begin();
      serverStarted = true;
      Serial.println("HTTP server ready on port 80");
    }
    return;
  }

  if (networkReady) {
    networkReady = false;
    if (mdnsStarted) {
      MDNS.end();
      mdnsStarted = false;
    }
  }
  if (millis() - lastWifiStatusMs >= 15000) {
    lastWifiStatusMs = millis();
    Serial.print("Servo Wi-Fi status: ");
    Serial.println(static_cast<int>(WiFi.status()));
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(ROLE_LED_PIN, OUTPUT);
  digitalWrite(ROLE_LED_PIN, ROLE_LED_ACTIVE_HIGH ? LOW : HIGH);
  Serial.println("Role: Follower (receiver); LED: two blinks");
  loadPulseRange();
  Serial.println("Pulse tuning: 1=low -50, 2=low +50, 3=high +50, 4=high -50, 5=more curl, 6=less curl, v=view, s=save, r=reset");
  Serial.print("Mode: d=relax (PWM off), n=all neutral, t=three-servo teleop; boot mode=");
  Serial.println(BOOT_IN_NEUTRAL_MODE ? "neutral" : "teleop");
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  servoReady = ledcAttach(SERVO_PIN, PWM_HZ, PWM_BITS);
#else
  servoReady = ledcSetup(PWM_CHANNEL, PWM_HZ, PWM_BITS) != 0;
  if (servoReady) ledcAttachPin(SERVO_PIN, PWM_CHANNEL);
#endif
  if (servoReady) {
    servoPinInputReady = gpio_input_enable(GPIO_NUM_18) == ESP_OK;
    if (!servoPinInputReady) Serial.println("GPIO18 input readback unavailable");
  } else Serial.println("Servo PWM initialization failed");
  attachAdditionalServos();
  if (!centerAllServos()) Serial.println("Three-servo centering failed");
  printPulseRange();
  printAdditionalServos();

  registerRoutes();
  if (WIFI_SSID[0] == '\0') {
    Serial.println("Set WIFI_SSID and WIFI_PASSWORD in WiFiConfig.h");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(onWifiDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  updateRoleLed();
  handleSerialCommands();
  maintainWifi();
  if (networkReady && serverStarted) server.handleClient();
  updateTailTap();
  updateKonkon();
  delay(2);
}
