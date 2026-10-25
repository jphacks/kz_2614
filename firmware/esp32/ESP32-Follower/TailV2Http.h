#pragma once
#include "TailV2Wire.h"
#include <esp_http_server.h>
#include <esp_timer.h>
#include <sys/socket.h>
#include <errno.h>

namespace tail_v2 {
// The HTTP task never owns the control mutex while receiving/sending. The main
// task continues to expire and tick motion even when a peer stalls mid-request.
class HttpServer {
 public:
  explicit HttpServer(Controller& controller) : controller_(controller) {}
  bool begin() {
    if (server_) return true;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.max_open_sockets = 4;
    config.max_uri_handlers = 2;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 1;
    config.send_wait_timeout = 1;
    config.open_fn = opened;
    config.uri_match_fn = httpd_uri_match_wildcard;
    if (httpd_start(&server_, &config) != ESP_OK) return false;
    for (httpd_method_t method : {HTTP_GET, HTTP_POST}) {
      httpd_uri_t route = {};
      route.uri = "/*";
      route.method = method;
      route.handler = handle;
      route.user_ctx = this;
      if (httpd_register_uri_handler(server_, &route) != ESP_OK) {
        httpd_stop(server_);
        server_ = nullptr;
        return false;
      }
    }
    return true;
  }
 private:
  struct ReceiveDeadline { int64_t at; };
  Controller& controller_;
  httpd_handle_t server_ = nullptr;
  static void freeDeadline(void* p) { delete static_cast<ReceiveDeadline*>(p); }
  static esp_err_t opened(httpd_handle_t server, int fd) {
    auto* deadline = new (std::nothrow) ReceiveDeadline{esp_timer_get_time() + 1000000};
    if (!deadline) return ESP_FAIL;
    httpd_sess_set_ctx(server, fd, deadline, freeDeadline);
    timeval timeout{0, 100000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    return httpd_sess_set_recv_override(server, fd, receive);
  }
  static int receive(httpd_handle_t server, int fd, char* buffer, size_t length, int flags) {
    auto* deadline = static_cast<ReceiveDeadline*>(httpd_sess_get_ctx(server, fd));
    if (!deadline || esp_timer_get_time() >= deadline->at) return HTTPD_SOCK_ERR_TIMEOUT;
    int count = recv(fd, buffer, length, flags);
    if (esp_timer_get_time() >= deadline->at) return HTTPD_SOCK_ERR_TIMEOUT;
    if (count < 0) return errno == EAGAIN || errno == EWOULDBLOCK ?
        HTTPD_SOCK_ERR_TIMEOUT : HTTPD_SOCK_ERR_FAIL;
    return count;
  }
  static esp_err_t send(httpd_req_t* req, int code, const std::string& body) {
    const char* status = code == 200 ? "200 OK" : code == 403 ? "403 Forbidden" :
        code == 409 ? "409 Conflict" : code == 429 ? "429 Too Many Requests" :
        code == 503 ? "503 Service Unavailable" : "400 Bad Request";
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send(req, body.c_str(), body.size());
    // Also close at the server: no keep-alive or pipelined second command.
    return ESP_FAIL;
  }
  static esp_err_t handle(httpd_req_t* req) {
    auto* self = static_cast<HttpServer*>(req->user_ctx);
    if (req->content_len > MAX_BODY ||
        httpd_req_get_hdr_value_len(req, "Transfer-Encoding") != 0)
      return send(req, 400, errorJson("invalid_request"));
    char contentType[64] = {};
    httpd_req_get_hdr_value_str(req, "Content-Type", contentType, sizeof(contentType));
    std::string body(req->content_len, '\0');
    size_t received = 0;
    const int64_t deadline = esp_timer_get_time() + 500000;
    while (received < body.size()) {
      if (esp_timer_get_time() >= deadline)
        return send(req, 400, errorJson("invalid_request"));
      int count = httpd_req_recv(req, &body[received], body.size() - received);
      if (count <= 0 || esp_timer_get_time() >= deadline) return send(req, 400, errorJson("invalid_request"));
      received += count;
    }
    std::string json;
    Result result = dispatch(self->controller_, req->method == HTTP_GET ? "GET" : "POST",
        req->uri, contentType, body, esp_timer_get_time() / 1000, json);
    return send(req, result.status, json);
  }
};
}  // namespace tail_v2
