#pragma once
#include <string>
#include <map>
#include <cstring>
#include <algorithm>
#include <new>
#include "esp_timer.h"
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_FAIL = -1;
constexpr int HTTPD_SOCK_ERR_TIMEOUT = -3, HTTPD_SOCK_ERR_FAIL = -1;
using httpd_handle_t = void*;
enum httpd_method_t { HTTP_GET, HTTP_POST };
struct httpd_req_t {
  size_t content_len = 0;
  const char* uri = "/api/v2/tail/open";
  httpd_method_t method = HTTP_POST;
  void* user_ctx = nullptr;
  std::string body, contentType = "application/json", transferEncoding, output, status;
  std::map<std::string, std::string> headers;
  size_t received = 0, chunk = 1024;
  int64_t recvDelay = 0;
};
struct httpd_config_t {
  int stack_size, max_open_sockets, max_uri_handlers;
  bool lru_purge_enable;
  int recv_wait_timeout, send_wait_timeout;
  esp_err_t (*open_fn)(httpd_handle_t, int);
  bool (*uri_match_fn)(const char*, const char*, size_t);
};
#define HTTPD_DEFAULT_CONFIG() httpd_config_t{}
struct httpd_uri_t {
  const char* uri;
  httpd_method_t method;
  esp_err_t (*handler)(httpd_req_t*);
  void* user_ctx;
};
inline httpd_uri_t fakeRoute;
inline bool httpd_uri_match_wildcard(const char*, const char*, size_t) { return true; }
inline int httpd_start(httpd_handle_t* h, httpd_config_t*) { *h = reinterpret_cast<void*>(1); return 0; }
inline int httpd_stop(httpd_handle_t) { return 0; }
inline int httpd_register_uri_handler(httpd_handle_t, httpd_uri_t* r) { fakeRoute = *r; return 0; }
inline void httpd_sess_set_ctx(httpd_handle_t, int, void*, void (*)(void*)) {}
inline void* httpd_sess_get_ctx(httpd_handle_t, int) { return nullptr; }
inline int httpd_sess_set_recv_override(httpd_handle_t, int,
    int (*)(httpd_handle_t, int, char*, size_t, int)) { return 0; }
inline void httpd_resp_set_status(httpd_req_t* r, const char* s) { r->status = s; }
inline void httpd_resp_set_type(httpd_req_t*, const char*) {}
inline void httpd_resp_set_hdr(httpd_req_t* r, const char* key, const char* value) { r->headers[key] = value; }
inline int httpd_resp_send(httpd_req_t* r, const char* s, size_t n) { r->output.assign(s, n); return 0; }
inline size_t httpd_req_get_hdr_value_len(httpd_req_t* r, const char* key) {
  return !strcmp(key, "Transfer-Encoding") ? r->transferEncoding.size() : r->contentType.size();
}
inline int httpd_req_get_hdr_value_str(httpd_req_t* r, const char*, char* b, size_t n) {
  if (r->contentType.size() >= n) return -1;
  strcpy(b, r->contentType.c_str()); return 0;
}
inline int httpd_req_recv(httpd_req_t* r, char* buffer, size_t n) {
  fakeNowUs += r->recvDelay;
  size_t count = std::min({n, r->chunk, r->body.size() - r->received});
  memcpy(buffer, r->body.data() + r->received, count);
  r->received += count;
  return count;
}
