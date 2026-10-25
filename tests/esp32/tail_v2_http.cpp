// Compile the embedded HTTP adapter itself with fake IDF I/O, including timeouts.
#include "TailV2Http.h"
#include <cassert>
#include <iostream>
void epoch(void*, char* out) { strcpy(out, "7ef65580e6e346de8b503259d4e1f40c"); }
bool pwm(void*, int32_t) { return true; }
int main() {
  tail_v2::Controller c(epoch, pwm, nullptr, {true, true, true, 0, 180, 90000});
  tail_v2::HttpServer server(c);
  assert(server.begin());
  const std::string open = "{\"device_epoch\":\"7ef65580e6e346de8b503259d4e1f40c\",\"expected_revision\":0,"
      "\"command_id\":\"7ed373b7-b211-4f16-8cb5-0623fe19a3d2\",\"session_id\":\"4e933206-d53c-49b3-8f8a-e86603c88aba\"}";
  for (int scenario = 0; scenario < 7; ++scenario) {
    fakeNowUs = 0;
    httpd_req_t r;
    r.user_ctx = fakeRoute.user_ctx;
    r.body = open;
    r.content_len = r.body.size();
    if (scenario == 0) { r.body.resize(20); } // peer disconnect mid-body
    if (scenario == 1) { r.chunk = 1; r.recvDelay = 100000; } // slow drip, absolute deadline
    if (scenario == 2) r.content_len = 1025;
    if (scenario == 3) r.transferEncoding = "chunked";
    if (scenario == 4) r.contentType = "text/plain";
    if (scenario == 5) r.recvDelay = 600000; // last chunk also must meet deadline
    if (scenario == 6) r.chunk = 7; // valid fragmented request
    assert(fakeRoute.handler(&r) == ESP_FAIL); // close, never keep-alive
    assert(r.headers["Cache-Control"] == "no-store");
    assert(r.headers["Connection"] == "close");
    if (scenario < 6) {
      assert(r.status == "400 Bad Request");
      assert(c.state(0).revision == 0);
      assert(!c.state(0).open);
    } else {
      assert(r.status == "200 OK");
      assert(c.state(0).open && c.state(0).revision == 1);
    }
    assert(fakeNowUs <= 600000);
  }
  std::cout << "PASS: 7 embedded HTTP adapter cases (partial/slow/oversize/chunked/type/fragmented)\n";
}
