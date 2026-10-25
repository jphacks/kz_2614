#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <driver/gpio.h>
#include <esp_arduino_version.h>
#include <esp_random.h>
#include <atomic>
#include "TailV2Http.h"
#include <math.h>
#include "WiFiConfig.h"
#include "TailConfig.h"
#include "TailServoMapping.h"

// Existing private configurations retain their original motion width.
#ifndef TAIL_MOTION_AMPLITUDE_PERCENT
#define TAIL_MOTION_AMPLITUDE_PERCENT 100
#endif
static_assert(TAIL_MOTION_AMPLITUDE_PERCENT >= 100 && TAIL_MOTION_AMPLITUDE_PERCENT <= 150,
              "Tail motion amplitude must be between 100 and 150 percent");

// ESP32 DevKit / ESP32 Dev Module. Power all SG90s from a separate 4.8-6 V supply.
// The Reader's 0-180 target is divided equally over three serial joints.
constexpr uint8_t SERVO_PIN = 18;
constexpr uint8_t ADDITIONAL_SERVO_PINS[] = {19, 21};
constexpr size_t ADDITIONAL_SERVO_COUNT = sizeof(ADDITIONAL_SERVO_PINS) /
    sizeof(ADDITIONAL_SERVO_PINS[0]);
constexpr uint16_t NEUTRAL_PULSE_US = 1500;
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
// One "tap" means arriving at one configured endpoint, not a whole round trip.
// Endpoint values come from the private TailConfig.h after physical calibration.
#if ESP_ARDUINO_VERSION_MAJOR < 3
constexpr uint8_t PWM_CHANNEL = 0;
#endif

tail_v2::Controller* tailController = nullptr;
tail_v2::HttpServer* tailHttp = nullptr;
bool savedCalibrationValid = false;
Preferences pulseStore;
std::atomic<bool> wifiDisconnectObserved{false};
bool networkReady = false;
bool serverStarted = false;
bool mdnsStarted = false;
bool servoReady = false;
bool additionalServoReady[ADDITIONAL_SERVO_COUNT] = {};
bool additionalPinInputReady[ADDITIONAL_SERVO_COUNT] = {};
bool pulseStoreReady = false;
bool servoPinInputReady = false;
bool neutralMode = false;
bool relaxMode = true;
#ifndef TAIL_SESSION_PREPARE_ENABLED
#define TAIL_SESSION_PREPARE_ENABLED false
#endif

int currentAngle = 90;
uint16_t minPulseUs = DEFAULT_MIN_PULSE_US;
uint16_t maxPulseUs = DEFAULT_MAX_PULSE_US;
uint16_t currentPulseUs = 1500;
uint16_t curlExtraUs = 0;

void onWifiDisconnected(WiFiEvent_t, WiFiEventInfo_t info) {
  wifiDisconnectObserved.store(true);
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
  const uint16_t pulseUs = tail_servo::pulseForAngle(
      angle, minPulseUs, maxPulseUs, curlExtraUs, TAIL_MOTION_AMPLITUDE_PERCENT);
  return writeAllServosPulse(pulseUs, angle);
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
      additionalServoReady[i] = ledcWrite(pin, 0);
    }
#else
    const uint8_t channel = PWM_CHANNEL + i + 1;
    additionalServoReady[i] = ledcSetup(channel, PWM_HZ, PWM_BITS) != 0;
    if (additionalServoReady[i]) {
      ledcAttachPin(pin, channel);
      ledcWrite(channel, 0);
    }
#endif
    if (additionalServoReady[i]) {
      additionalPinInputReady[i] =
          gpio_input_enable(static_cast<gpio_num_t>(pin)) == ESP_OK;
    }
    Serial.print("Servo GPIO");
    Serial.print(pin);
    Serial.println(additionalServoReady[i] ? ": ready; PWM off" :
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
  Serial.print("Motion amplitude percent: ");
  Serial.println(TAIL_MOTION_AMPLITUDE_PERCENT);
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
  savedCalibrationValid = pulseStore.isKey("range") && validPulseRange(minimum, maximum) &&
      savedCurlExtraUs <= CURL_EXTRA_MAX_US && savedCurlExtraUs % CURL_EXTRA_STEP_US == 0;
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


bool v2Calibrated() {
  return savedCalibrationValid && TAIL_TAP_START_ANGLE >= 0 &&
      TAIL_TAP_END_ANGLE <= MAX_ANGLE && TAIL_TAP_START_ANGLE < TAIL_TAP_END_ANGLE &&
      isfinite(TAIL_TESTED_MAX_RATE_HZ) && TAIL_TESTED_MAX_RATE_HZ >= 0.3;
}

uint64_t nowMs() { return esp_timer_get_time() / 1000; }

void makeEpoch(void*, char* output) {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));  // Wi-Fi radio is initialized before construction.
  const char* hex = "0123456789abcdef";
  for (size_t i = 0; i < sizeof(bytes); ++i) {
    output[2 * i] = hex[bytes[i] >> 4];
    output[2 * i + 1] = hex[bytes[i] & 15];
  }
  output[32] = '\0';
}

bool writeV2Angle(void*, int32_t milliAngle) {
  // Preserve the existing individual pulse calibration, with sub-degree motion.
  const uint16_t pulse = tail_servo::pulseForMilliAngle(
      milliAngle, minPulseUs, maxPulseUs, curlExtraUs, TAIL_MOTION_AMPLITUDE_PERCENT);
  return writeAllServosPulse(pulse, (milliAngle + 500) / 1000);
}

tail_v2::Configuration localConfiguration(bool enabled, bool ready) {
  return {enabled, v2Calibrated(), ready,
      TAIL_TAP_START_ANGLE, TAIL_TAP_END_ANGLE, currentAngle * 1000,
      TAIL_SESSION_PREPARE_ENABLED && TAIL_MCP_API_ENABLED && allServosReady()};
}

void handleSerialCommands() {
  // Limit work per loop. Slow diagnostics run only while disarmed.
  if (!Serial.available()) return;
  const int command = Serial.read();
  if (command == '\r' || command == '\n') return;
  if (command == 'v') {
    const auto state = tailController->state(nowMs());
    Serial.println(tail_v2::stateJson(state).c_str());
    if (!state.enabled) { printPulseRange(); printAdditionalServos(); }
    return;
  }
  if (command == 't') {
    Serial.println("Reader/teleop is unavailable in v2_exclusive");
    return;
  }
  if (command == 'x') {
    tailController->localStop(); // Hold PWM and fence, just like HTTP stop.
    Serial.println("Stopped: closed/idle; holding last PWM");
    return;
  }
  const bool tuning = command >= '1' && command <= '6';
  if (!(tuning || command == 'a' || command == 'd' || command == 'n' ||
        command == 's' || command == 'r')) return;
  tailController->localChange([&]() -> tail_v2::Configuration {
    // Hold the controller mutex through the entire local hardware transition.
    if (command == 'a') {
      if (!TAIL_MCP_API_ENABLED || !v2Calibrated() || !allServosReady() ||
          WiFi.status() != WL_CONNECTED) {
        Serial.println("Preparation refused: check permission/calibration/PWM/Wi-Fi");
        return localConfiguration(false, false);
      }
      const int prepareAngle = constrain(90, TAIL_TAP_START_ANGLE, TAIL_TAP_END_ANGLE);
      if (!writeServoAngle(prepareAngle)) {
        Serial.println("Preparation refused: PWM write failed");
        return localConfiguration(false, false);
      }
      neutralMode = relaxMode = false;
      Serial.println("Operator prepared v2: closed/idle; Reader remains blocked");
      return localConfiguration(true, true);
    }
    if (command == 'd') stopAllServoPwm();
    else if (command == 'n') {
      if (centerAllServos()) { neutralMode = true; relaxMode = false; }
    } else {
      // Calibration changes never move the servos. Explicit 'a' is needed later.
      if (!stopAllServoPwm()) {
        Serial.println("Calibration refused: could not disable PWM");
        return localConfiguration(false, false);
      }
      if (tuning) {
        savedCalibrationValid = false;
        switch (command) {
          case '1': adjustPulseRange(-PULSE_STEP_US, 0); break;
          case '2': adjustPulseRange(PULSE_STEP_US, 0); break;
          case '3': adjustPulseRange(0, PULSE_STEP_US); break;
          case '4': adjustPulseRange(0, -PULSE_STEP_US); break;
          case '5': adjustCurlExtra(CURL_EXTRA_STEP_US); break;
          case '6': adjustCurlExtra(-CURL_EXTRA_STEP_US); break;
        }
      } else if (command == 's') {
        const uint32_t packed = (static_cast<uint32_t>(maxPulseUs) << 16) | minPulseUs;
        const bool rangeSaved = pulseStoreReady &&
            pulseStore.putUInt("range", packed) == sizeof(uint32_t);
        const bool curlSaved = pulseStoreReady &&
            pulseStore.putUInt("curl", curlExtraUs) == sizeof(uint32_t);
        savedCalibrationValid = rangeSaved && curlSaved;
        Serial.println(savedCalibrationValid ? "Calibration saved" : "Calibration save failed");
      } else if (command == 'r') {
        // Explicit local erase only; firmware update/boot never invokes this.
        savedCalibrationValid = false;
        if (pulseStoreReady) { pulseStore.remove("range"); pulseStore.remove("curl"); }
        minPulseUs = DEFAULT_MIN_PULSE_US;
        maxPulseUs = DEFAULT_MAX_PULSE_US;
        curlExtraUs = 0;
      }
    }
    return localConfiguration(TAIL_MCP_API_ENABLED, false);
  });
}

void maintainWifi() {
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (wifiDisconnectObserved.exchange(false) || (!connected && networkReady)) {
    tailController->localStop(true);
    networkReady = false;
    if (mdnsStarted) { MDNS.end(); mdnsStarted = false; }
    Serial.println("Wi-Fi lost: closed/idle and disarmed; no automatic rearm");
  }
  if (connected && !networkReady) {
    networkReady = true;
    Serial.print("Servo ESP32 IP: ");
    Serial.println(WiFi.localIP());
    mdnsStarted = MDNS.begin("esp32-servo");
    if (mdnsStarted) MDNS.addService("http", "tcp", 80);
  }
  if (connected && !serverStarted) serverStarted = tailHttp->begin();
}

void setup() {
  Serial.begin(115200);
  pinMode(ROLE_LED_PIN, OUTPUT);
  digitalWrite(ROLE_LED_PIN, ROLE_LED_ACTIVE_HIGH ? LOW : HIGH);
  Serial.println("Role: Follower; v2_exclusive; boot closed/idle, PWM off");
  loadPulseRange();
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  servoReady = ledcAttach(SERVO_PIN, PWM_HZ, PWM_BITS);
  if (servoReady) servoReady = ledcWrite(SERVO_PIN, 0);
#else
  servoReady = ledcSetup(PWM_CHANNEL, PWM_HZ, PWM_BITS) != 0;
  if (servoReady) { ledcAttachPin(SERVO_PIN, PWM_CHANNEL); ledcWrite(PWM_CHANNEL, 0); }
#endif
  if (servoReady) servoPinInputReady = gpio_input_enable(GPIO_NUM_18) == ESP_OK;
  attachAdditionalServos();
  // Enable the RF entropy source even when a Wi-Fi configuration is missing.
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("RF entropy source unavailable; controller stays disabled");
    return;
  }
  tailController = new tail_v2::Controller(makeEpoch, writeV2Angle, nullptr,
      {false, v2Calibrated(), false, TAIL_TAP_START_ANGLE, TAIL_TAP_END_ANGLE, 90000,
       TAIL_SESSION_PREPARE_ENABLED && TAIL_MCP_API_ENABLED && allServosReady()});
  // Revision 0, disabled: only explicit local or opt-in conversation preparation.
  tailHttp = new tail_v2::HttpServer(*tailController);
  Serial.println("v=status, a=explicit prepare (moves), x=hold/stop, n=neutral, d=relax; t=blocked");
  Serial.println("1..6=tune (disarms), s=save calibration, r=erase calibration");
  if (WIFI_SSID[0] == '\0') {
    Serial.println("Set private WiFiConfig.h; start remains disabled");
    return;
  }
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(onWifiDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  if (!tailController) { delay(10); return; }
  // Independent of the HTTP server task, including stalled body/response I/O.
  maintainWifi();
  tailController->tick(nowMs());
  handleSerialCommands();
  updateRoleLed();
  delay(2);
}
