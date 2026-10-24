#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "WiFiConfig.h"

// ESP32 DevKit / ESP32 Dev Module: GPIO34 is ADC1 and remains usable with Wi-Fi.
constexpr uint8_t POT_PIN = 34;
// Many ESP32 boards wire their user LED to GPIO2. Use an external LED if yours does not.
constexpr uint8_t ROLE_LED_PIN = 2;
constexpr bool ROLE_LED_ACTIVE_HIGH = true;
constexpr unsigned long ROLE_LED_CYCLE_MS = 1800;
constexpr int ADC_MAX_RAW = 4095;
constexpr int MIN_CALIBRATION_SPAN = 128;
constexpr unsigned long CALIBRATION_REPORT_MS = 500;
constexpr int MAX_ANGLE = 180;  // Full command span after calibrating the servo endpoints.
constexpr unsigned long SAMPLE_INTERVAL_MS = 30;
constexpr unsigned long SEND_INTERVAL_MS = 100;
constexpr unsigned long KEEPALIVE_MS = 1000;
constexpr unsigned long RESOLVE_INTERVAL_MS = 5000;

IPAddress receiverIp;
Preferences calibrationStore;
bool wifiReady = false;
bool mdnsStarted = false;
bool firstResolve = true;
bool apiModeBlocked = false;
bool teleopConfirmed = false;
bool calibrationStoreReady = false;
bool calibrating = false;
int calibrationMin = 0;
int calibrationMax = ADC_MAX_RAW;
int observedMin = ADC_MAX_RAW;
int observedMax = 0;
int filteredRaw = -1;
int lastSentAngle = -1;
unsigned long lastSampleMs = 0;
unsigned long lastSendMs = 0;
unsigned long lastResolveMs = 0;
unsigned long lastWifiStatusMs = 0;
unsigned long lastCalibrationReportMs = 0;

void onWifiDisconnected(WiFiEvent_t, WiFiEventInfo_t info) {
  Serial.print("Reader Wi-Fi disconnect reason: ");
  Serial.println(info.wifi_sta_disconnected.reason);
}

void updateRoleLed() {
  // One short blink per cycle identifies the Reader (sender).
  const bool on = millis() % ROLE_LED_CYCLE_MS < 180;
  static bool wasOn = false;
  if (on != wasOn) {
    digitalWrite(ROLE_LED_PIN, on == ROLE_LED_ACTIVE_HIGH ? HIGH : LOW);
    wasOn = on;
  }
}

int readPotentiometer() {
  int sum = 0;
  for (int i = 0; i < 8; ++i) {
    sum += analogRead(POT_PIN);
    delayMicroseconds(250);
  }
  return sum / 8;
}

bool validCalibration(int minimum, int maximum) {
  return minimum >= 0 && maximum <= ADC_MAX_RAW &&
      maximum - minimum >= MIN_CALIBRATION_SPAN;
}

int angleFromRaw(int raw) {
  const int bounded = constrain(raw, calibrationMin, calibrationMax);
  const int span = calibrationMax - calibrationMin;
  return ((bounded - calibrationMin) * MAX_ANGLE + span / 2) / span;
}

void printCalibration() {
  Serial.print("ADC raw: ");
  Serial.print(readPotentiometer());
  Serial.print("; active min/max: ");
  Serial.print(calibrationMin);
  Serial.print('/');
  Serial.println(calibrationMax);
  if (calibrating) {
    Serial.print("Observed min/max: ");
    Serial.print(observedMin);
    Serial.print('/');
    Serial.println(observedMax);
  }
}

void loadCalibration() {
  calibrationStoreReady = calibrationStore.begin("pot-range", false);
  if (!calibrationStoreReady) {
    Serial.println("Calibration storage unavailable; using 0-4095");
    return;
  }
  const uint32_t packed = calibrationStore.getUInt("range", 0x0FFF0000UL);
  const int minimum = packed & 0xFFFF;
  const int maximum = packed >> 16;
  if (validCalibration(minimum, maximum)) {
    calibrationMin = minimum;
    calibrationMax = maximum;
  } else {
    Serial.println("Saved calibration invalid; using 0-4095");
  }
  Serial.print("Active ADC min/max: ");
  Serial.print(calibrationMin);
  Serial.print('/');
  Serial.println(calibrationMax);
}

void handleSerialCommands() {
  while (Serial.available()) {
    const char command = static_cast<char>(Serial.read());
    if (command == 'c' || command == 'C') {
      calibrating = true;
      observedMin = ADC_MAX_RAW;
      observedMax = 0;
      lastCalibrationReportMs = 0;
      Serial.println("Calibration started. Turn potentiometer to both ends, then send s.");
    } else if (command == 's' || command == 'S') {
      if (!calibrating) {
        Serial.println("Send c before s.");
      } else if (!validCalibration(observedMin, observedMax)) {
        Serial.println("ADC span too small; keep turning the potentiometer to both ends.");
        printCalibration();
      } else {
        calibrationMin = observedMin;
        calibrationMax = observedMax;
        const uint32_t packed = (static_cast<uint32_t>(calibrationMax) << 16) |
            static_cast<uint32_t>(calibrationMin);
        const bool saved = calibrationStoreReady &&
            calibrationStore.putUInt("range", packed) == sizeof(uint32_t);
        calibrating = false;
        filteredRaw = -1;
        lastSentAngle = -1;
        Serial.print(saved ? "Calibration saved: " : "Calibration active but NOT saved: ");
        Serial.print(calibrationMin);
        Serial.print('/');
        Serial.println(calibrationMax);
      }
    } else if (command == 'x' || command == 'X') {
      calibrating = false;
      filteredRaw = -1;
      lastSentAngle = -1;
      Serial.println("Calibration cancelled; previous range kept.");
    } else if (command == 'r' || command == 'R') {
      if (calibrationStoreReady && calibrationStore.isKey("range") &&
          !calibrationStore.remove("range")) {
        Serial.println("Could not erase saved calibration.");
        continue;
      }
      calibrating = false;
      calibrationMin = 0;
      calibrationMax = ADC_MAX_RAW;
      filteredRaw = -1;
      lastSentAngle = -1;
      Serial.println("Calibration reset to 0-4095.");
    } else if (command == 'v' || command == 'V') {
      printCalibration();
    }
  }
}

void maintainWifi() {
  if (WIFI_SSID[0] == '\0') return;

  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiReady) {
      wifiReady = true;
      mdnsStarted = MDNS.begin("esp32-reader");
      Serial.print("Reader IP: ");
      Serial.println(WiFi.localIP());
      if (!mdnsStarted) Serial.println("Reader mDNS start failed");
    }
    return;
  }

  if (wifiReady) {
    wifiReady = false;
    if (mdnsStarted) {
      MDNS.end();
      mdnsStarted = false;
    }
    receiverIp = IPAddress();
    firstResolve = true;
    teleopConfirmed = false;
  }
  if (millis() - lastWifiStatusMs >= 15000) {
    lastWifiStatusMs = millis();
    Serial.print("Reader Wi-Fi status: ");
    Serial.println(static_cast<int>(WiFi.status()));
  }
}

void resolveReceiver() {
  if (receiverIp != IPAddress()) return;
  if (!firstResolve && millis() - lastResolveMs < RESOLVE_INTERVAL_MS) return;
  firstResolve = false;
  lastResolveMs = millis();

  if (RECEIVER_IP[0] != '\0') {
    if (!receiverIp.fromString(RECEIVER_IP)) {
      Serial.println("Invalid RECEIVER_IP in WiFiConfig.h");
    }
  } else if (mdnsStarted) {
    receiverIp = MDNS.queryHost("esp32-servo", 1500);
    if (receiverIp == IPAddress()) {
      Serial.println("Waiting for esp32-servo.local (or set RECEIVER_IP)");
    }
  }

  if (receiverIp != IPAddress()) {
    Serial.print("Servo ESP32: ");
    Serial.println(receiverIp);
  }
}

void sendAngle(int angle) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(700);
  http.setTimeout(700);
  const String url = "http://" + receiverIp.toString() + "/api/teleop";
  if (!http.begin(client, url)) {
    receiverIp = IPAddress();
    return;
  }
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  const int status = http.POST("angle=" + String(angle));
  http.end();

  if (status == 200) {
    apiModeBlocked = false;
    lastSentAngle = angle;
    if (!teleopConfirmed) {
      Serial.print("Teleop HTTP 200; angle: ");
      Serial.println(angle);
      teleopConfirmed = true;
    }
  } else if (status == 409) {
    // API mode is active. Retry only on the one-second keepalive.
    apiModeBlocked = true;
    lastSentAngle = angle;
  } else {
    Serial.print("Teleop HTTP error: ");
    Serial.println(status);
    if (RECEIVER_IP[0] == '\0') receiverIp = IPAddress();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(ROLE_LED_PIN, OUTPUT);
  digitalWrite(ROLE_LED_PIN, ROLE_LED_ACTIVE_HIGH ? LOW : HIGH);
  Serial.println("Role: Reader (sender); LED: one blink");
  analogReadResolution(12);
  analogSetPinAttenuation(POT_PIN, ADC_11db);
  loadCalibration();
  Serial.println("Calibration: c=start, s=save, x=cancel, v=view, r=reset");

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
  const unsigned long now = millis();
  if (now - lastSampleMs < SAMPLE_INTERVAL_MS) return;
  lastSampleMs = now;

  const int raw = readPotentiometer();
  if (calibrating) {
    observedMin = min(observedMin, raw);
    observedMax = max(observedMax, raw);
    if (now - lastCalibrationReportMs >= CALIBRATION_REPORT_MS) {
      lastCalibrationReportMs = now;
      Serial.print("ADC raw/min/max: ");
      Serial.print(raw);
      Serial.print('/');
      Serial.print(observedMin);
      Serial.print('/');
      Serial.println(observedMax);
    }
    return;
  }

  filteredRaw = filteredRaw < 0 ? raw : (filteredRaw * 3 + raw) / 4;
  if (WiFi.status() != WL_CONNECTED) {
    delay(30);
    return;
  }
  resolveReceiver();
  if (receiverIp == IPAddress()) {
    delay(30);
    return;
  }

  const int angle = angleFromRaw(filteredRaw);
  const bool changed = lastSentAngle < 0 || abs(angle - lastSentAngle) >= 2;
  if (apiModeBlocked && now - lastSendMs < KEEPALIVE_MS) return;
  if ((changed && now - lastSendMs >= SEND_INTERVAL_MS) ||
      now - lastSendMs >= KEEPALIVE_MS) {
    lastSendMs = now;
    sendAngle(angle);
  }
}
