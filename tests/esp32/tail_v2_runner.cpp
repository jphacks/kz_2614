// Host adapter for the same Controller + wire router compiled into the Follower.
#include "TailV2Wire.h"
#include <iostream>
#include <memory>
#include <vector>

namespace tail_v2 {
struct ControllerTestAccess {
  static void revision(Controller& c, uint64_t value) { c.state_.revision = value; }
};
}
struct Hardware {
  unsigned boots = 0;
  bool fail = false;
  std::vector<int32_t> writes;
};
void epoch(void* context, char* out) {
  auto& h = *static_cast<Hardware*>(context);
  if (h.boots++ == 0) strcpy(out, "7ef65580e6e346de8b503259d4e1f40c");
  else snprintf(out, 33, "%032x", h.boots);
}
bool writePwm(void* context, int32_t angle) {
  auto& h = *static_cast<Hardware*>(context);
  if (h.fail) return false;
  h.writes.push_back(angle);
  return true;
}
const cJSON* field(cJSON* root, const char* key) { return cJSON_GetObjectItemCaseSensitive(root, key); }
std::string stringField(cJSON* root, const char* key, const char* fallback = "") {
  const auto* f = field(root, key);
  return cJSON_IsString(f) ? f->valuestring : fallback;
}
int main() {
  Hardware h;
  auto c = std::unique_ptr<tail_v2::Controller>(new tail_v2::Controller(epoch, writePwm, &h));
  uint64_t now = 0;
  for (std::string line; std::getline(std::cin, line);) {
    cJSON* input = cJSON_Parse(line.c_str());
    if (!input) return 2;
    std::string action = stringField(input, "action");
    if (cJSON_IsNumber(field(input, "now_ms"))) now = field(input, "now_ms")->valuedouble;
    int status = 200;
    std::string json;
    if (action == "reboot" || action == "prepared_boot" || action == "session_boot") {
      tail_v2::Configuration cfg;
      if (action == "prepared_boot" || action == "session_boot") { h.boots = 0; cfg = {true, true, true, 0, 180, 90000}; }
      if (action == "session_boot") cfg = {false, true, false, 0, 180, 90000, true};
      c.reset(new tail_v2::Controller(epoch, writePwm, &h, cfg));
    } else if (action == "configure") {
      int low = cJSON_IsNumber(field(input, "low")) ? field(input, "low")->valueint : 0;
      int high = cJSON_IsNumber(field(input, "high")) ? field(input, "high")->valueint : 180;
      int position = cJSON_IsNumber(field(input, "position")) ? field(input, "position")->valueint : 90000;
      c->configure(cJSON_IsTrue(field(input, "enabled")), cJSON_IsTrue(field(input, "calibrated")),
          cJSON_IsTrue(field(input, "ready")), low, high, position);
    } else if (action == "tick") c->tick(now);
    else if (action == "local_stop") c->localStop(cJSON_IsTrue(field(input, "disarm")));
    else if (action == "pwm_fail") h.fail = true;
    else if (action == "near_overflow") tail_v2::ControllerTestAccess::revision(*c, tail_v2::MAX_REVISION - 1);
    else if (action == "request") {
      auto r = tail_v2::dispatch(*c, stringField(input, "method", "POST"),
          stringField(input, "path"), stringField(input, "content_type", "application/json"),
          stringField(input, "body"), now, json);
      status = r.status;
    }
    if (json.empty()) json = tail_v2::stateJson(c->state(now));
    std::cout << "{\"status\":" << status << ",\"body\":" << json
              << ",\"writes\":" << h.writes.size() << ",\"last_millidegrees\":"
              << (h.writes.empty() ? 90000 : h.writes.back()) << "}" << std::endl;
    cJSON_Delete(input);
  }
}
