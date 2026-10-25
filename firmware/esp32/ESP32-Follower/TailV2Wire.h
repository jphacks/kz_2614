#pragma once
#include "TailV2.h"
#include <cJSON.h>
#include <cmath>
#include <string>
#include <cstdio>

namespace tail_v2 {
inline bool hexString(const char* s, size_t size, bool lowerOnly) {
  if (!s || strlen(s) != size) return false;
  for (size_t i = 0; i < size; ++i)
    if (!(s[i] >= '0' && s[i] <= '9') && !(s[i] >= 'a' && s[i] <= 'f') &&
        !(!lowerOnly && s[i] >= 'A' && s[i] <= 'F')) return false;
  return true;
}
inline bool uuid(const char* s) {
  if (!s || strlen(s) != 36) return false;
  for (size_t i = 0; i < 36; ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (s[i] != '-') return false;
    } else if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f') ||
                 (s[i] >= 'A' && s[i] <= 'F'))) return false;
  }
  return true;
}
// cJSON stores doubles. Check the numeric tokens before conversion so fractional
// or exponent notation cannot round into an integer revision/duration.
inline bool integerTokens(const std::string& body) {
  bool inString = false, escaped = false, objectSeen = false;
  for (size_t i = 0; i < body.size(); ++i) {
    const unsigned char c = body[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (c == '\\') escaped = true;
      else if (c == '"') inString = false;
      if (c < 32) return false;
      continue;
    }
    if (c == '"') { inString = true; continue; }
    if (c < 32 && c != ' ' && c != '\t' && c != '\r' && c != '\n') return false;
    if (c == '-') return false;
    if (c == '[' || c == ']') return false;
    if (c == '{') { if (objectSeen) return false; objectSeen = true; }
    if (c >= '0' && c <= '9') {
      size_t end = i + 1;
      while (end < body.size() && body[end] >= '0' && body[end] <= '9') ++end;
      if (end - i > 1 && c == '0') return false;
      if (end < body.size() && body[end] != ',' && body[end] != '}' &&
          body[end] != ' ' && body[end] != '\t' && body[end] != '\n' && body[end] != '\r') return false;
      i = end - 1;
    }
  }
  return !inString;
}
inline bool parseRequest(Operation op, const std::string& body, Request& r) {
  if (body.empty() || body.size() > MAX_BODY || !integerTokens(body) || body.find('\0') != std::string::npos ||
      body.find("\\u0000") != std::string::npos) return false;
  cJSON* root = cJSON_ParseWithLengthOpts(body.c_str(), body.size() + 1, nullptr, true);
  if (!root) return false;
  bool valid = cJSON_IsObject(root);
  unsigned seen = 0;
  for (const cJSON* item = root->child; item && valid; item = item->next) {
    unsigned bit = 0;
    if (!item->string) { valid = false; break; }
    if (!strcmp(item->string, "device_epoch")) {
      bit = 1;
      valid = cJSON_IsString(item) && hexString(item->valuestring, 32, true);
      if (valid) memcpy(r.epoch, item->valuestring, 33);
    } else if (!strcmp(item->string, "expected_revision")) {
      bit = 2;
      valid = cJSON_IsNumber(item) && std::isfinite(item->valuedouble) &&
          item->valuedouble >= 0 && item->valuedouble <= MAX_REVISION &&
          floor(item->valuedouble) == item->valuedouble;
      if (valid) r.revision = static_cast<uint64_t>(item->valuedouble);
    } else if (!strcmp(item->string, "command_id")) {
      bit = 4;
      valid = cJSON_IsString(item) && uuid(item->valuestring);
      // Preserve command spelling for exact ACK comparison.
      if (valid) memcpy(r.command, item->valuestring, 37);
    } else if (!strcmp(item->string, "session_id") && op != Operation::Stop) {
      bit = 8;
      valid = cJSON_IsString(item) && uuid(item->valuestring);
      if (valid) memcpy(r.session, item->valuestring, 37);
    } else if (!strcmp(item->string, "duration_ms") && op == Operation::Tap) {
      bit = 16;
      valid = cJSON_IsNumber(item) && std::isfinite(item->valuedouble) &&
          item->valuedouble >= MIN_DURATION_MS && item->valuedouble <= MAX_DURATION_MS &&
          floor(item->valuedouble) == item->valuedouble;
      if (valid) r.duration = static_cast<uint32_t>(item->valuedouble);
    } else valid = false;
    if (seen & bit) valid = false;
    seen |= bit;
  }
  const unsigned required = op == Operation::Stop ? 7 : op == Operation::Open ? 15 : 31;
  cJSON_Delete(root);
  return valid && seen == required;
}
inline std::string stateJson(const State& s, const char* command = nullptr) {
  // All strings were validated as hex/UUID; no untrusted free text is formatted.
  char buffer[768];
  char session[40] = "null";
  if (s.session[0]) snprintf(session, sizeof(session), "\"%s\"", s.session);
  snprintf(buffer, sizeof(buffer),
      "{\"protocol_version\":2,\"capabilities\":[\"revision_fence\"],"
      "\"control_mode\":\"v2_exclusive\",\"device_epoch\":\"%s\",\"revision\":%llu,"
      "\"session_id\":%s,\"phase\":\"%s\",\"motion\":\"%s\","
      "\"start_enabled\":%s,\"calibrated\":%s,\"feedback\":\"command_only\","
      "\"limits\":{\"min_duration_ms\":500,\"max_duration_ms\":5000,"
      "\"rate_hz\":0.3,\"window_ms\":60000,\"budget_ms\":10000}%s%s%s}",
      s.epoch, static_cast<unsigned long long>(s.revision), session,
      s.open ? "open" : "closed", s.tapping ? "tapping" : "idle",
      s.enabled ? "true" : "false", s.calibrated ? "true" : "false",
      command ? ",\"command_id\":\"" : "", command ? command : "", command ? "\"" : "");
  return buffer;
}
inline std::string errorJson(const char* error) {
  return std::string("{\"error\":\"") + error + "\"}";
}
// One router used by the embedded HTTP adapter and the host fixture runner.
inline Result dispatch(Controller& controller, const std::string& method,
                       const std::string& path, const std::string& contentType,
                       const std::string& body, uint64_t now, std::string& json) {
  if (method == "GET" && path == "/api/v2/tail/state") {
    State s = controller.state(now);
    json = stateJson(s);
    return {200, nullptr, s};
  }
  if (method == "POST" && path == "/api/tail/stop") {
    State s = controller.localStop();
    json = stateJson(s);
    return {200, nullptr, s};
  }
  Operation op;
  bool v2 = true;
  if (path == "/api/v2/tail/open") op = Operation::Open;
  else if (path == "/api/v2/tail/tap") op = Operation::Tap;
  else if (path == "/api/v2/tail/stop") op = Operation::Stop;
  else { op = Operation::Stop; v2 = false; }
  if (!v2 || method != "POST") {
    const bool legacy = path == "/api/teleop" || path == "/api/angle" ||
        path == "/api/konkon" || path == "/api/mode" || path == "/api/tail/tap";
    const char* error = legacy ? "mode_not_ready" : "invalid_request";
    json = errorJson(error);
    return {legacy ? 409 : 400, error, {}};
  }
  Request request;
  if (contentType != "application/json" || !parseRequest(op, body, request)) {
    json = errorJson("invalid_request");
    return {400, "invalid_request", {}};
  }
  Result result = controller.apply(op, request, now);
  json = result.status == 200 ? stateJson(result.state, request.command) : errorJson(result.error);
  return result;
}
}  // namespace tail_v2
