#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <mutex>

// No network, NVS or clock dependency. HTTP, expiry and local stops share this
// mutex, including the PWM write: a queued tick cannot write after a stop ACK.
namespace tail_v2 {
constexpr uint64_t MAX_REVISION = 9007199254740991ULL;
constexpr uint32_t WINDOW_MS = 60000;
constexpr uint32_t BUDGET_MS = 10000;
constexpr uint32_t MIN_DURATION_MS = 500;
constexpr uint32_t MAX_DURATION_MS = 5000;
constexpr size_t MAX_BODY = 1024;

enum class Operation { Open, Tap, Stop };
struct Request {
  char epoch[33] = {};
  uint64_t revision = 0;
  char command[37] = {};
  char session[37] = {};
  uint32_t duration = 0;
};
struct State {
  char epoch[33] = {};
  uint64_t revision = 0;
  char session[37] = {};
  bool open = false;
  bool tapping = false;
  bool enabled = false;
  bool calibrated = false;
  bool ready = false;
};
struct Result {
  int status;
  const char* error;
  State state;
};
struct Configuration {
  bool enabled = false, calibrated = false, ready = false;
  int low = 90, high = 90;
  int32_t position = 90000;
};
using EpochSource = void (*)(void*, char*);
using PwmWriter = bool (*)(void*, int32_t);

class Controller {
 public:
  Controller(EpochSource random, PwmWriter write, void* context, Configuration config = {})
      : random_(random), write_(write), context_(context) {
    random_(context_, state_.epoch);
    setConfiguration(config);
  }
  State state(uint64_t now) {
    std::lock_guard<std::mutex> lock(mutex_);
    expire(now);
    return state_;
  }
  Result apply(Operation op, const Request& r, uint64_t now) {
    std::lock_guard<std::mutex> lock(mutex_);
    expire(now);
    if (state_.revision >= MAX_REVISION - 1) rotateEpoch();
    if (strcmp(r.epoch, state_.epoch)) return result(409, "stale_epoch");
    if (r.revision != state_.revision) return result(409, "stale_revision");
    if (op == Operation::Stop) {
      close();
      advance();
      return result(200, nullptr);
    }
    if (!state_.enabled) return result(403, "start_disabled");
    if (!state_.calibrated) return result(503, "not_calibrated");
    if (!state_.ready) return result(409, "mode_not_ready");
    if (op == Operation::Open) {
      if (state_.open || state_.tapping) return result(409, "invalid_phase");
      state_.open = true;
      memcpy(state_.session, r.session, sizeof(state_.session));
    } else {
      if (!state_.open || state_.tapping) return result(409, "invalid_phase");
      if (strcmp(r.session, state_.session)) return result(409, "session_mismatch");
      if (r.duration < MIN_DURATION_MS || r.duration > MAX_DURATION_MS)
        return result(400, "invalid_request");
      uint32_t used = 0;
      for (const auto& entry : budget_)
        if (entry.duration && now - entry.at < WINDOW_MS) used += entry.duration;
      if (used + r.duration > BUDGET_MS) return result(429, "budget_exceeded");
      // At most 20 accepted minimum-duration requests can be live in the window.
      for (auto& entry : budget_) {
        if (!entry.duration || now - entry.at >= WINDOW_MS) {
          entry = {now, r.duration};
          break;
        }
      }
      state_.tapping = true;
      deadline_ = now + r.duration;
      lastTick_ = now;
      const int32_t low = low_ * 1000, high = high_ * 1000;
      target_ = position_ - low <= high - position_ ? low : high;
      if (position_ == target_) target_ = target_ == low ? high : low;
    }
    advance();
    return result(200, nullptr);
  }
  // Only the explicit local preparation path may change permission/mode.
  // Neither HTTP open nor stop arms PWM or releases neutral/relax.
  void configure(bool enabled, bool calibrated, bool ready, int low, int high,
                 int32_t position) {
    std::lock_guard<std::mutex> lock(mutex_);
    close();
    setConfiguration({enabled, calibrated, ready, low, high, position});
    advance();
  }
  // The complete explicit local action (including hardware writes) is serialized
  // with HTTP stop and timer writes. A stop ACK cannot be overtaken by preparation.
  template <typename Action> void localChange(Action action) {
    std::lock_guard<std::mutex> lock(mutex_);
    close();
    state_.enabled = false;
    state_.ready = false;
    setConfiguration(action());
    positionFineValid_ = false;
    advance();
  }
  State localStop(bool disarm = false) {
    std::lock_guard<std::mutex> lock(mutex_);
    close();
    if (disarm) { state_.enabled = false; state_.ready = false; }
    advance();
    return state_;
  }
  void tick(uint64_t now) {
    std::lock_guard<std::mutex> lock(mutex_);
    expire(now);
    if (!state_.tapping || now - lastTick_ < 20) return;
    uint64_t elapsed = now - lastTick_;
    lastTick_ = now;
    if (elapsed > 50) elapsed = 50;
    // Exact 0.3 endpoint arrivals/sec, even for narrow calibrated ranges.
    // Unit is 1/10000 degree (position_ is millidegrees). No integer deg/s round-up.
    const int64_t step = static_cast<int64_t>(high_ - low_) * 3 * elapsed;
    int64_t next = positionFineValid_ ? positionFine_ : position_ * 10LL;
    const int64_t target = target_ * 10LL;
    next = next < target ? (next + step < target ? next + step : target)
                        : (next - step > target ? next - step : target);
    const int32_t milli = static_cast<int32_t>(next / 10);
    if (milli != position_ && !write_(context_, milli)) {
      close();
      state_.enabled = false;
      state_.ready = false;
      advance();
      return;
    }
    position_ = milli;
    positionFine_ = next;
    positionFineValid_ = true;
    if (next == target) target_ = target_ == low_ * 1000 ? high_ * 1000 : low_ * 1000;
  }
 private:
  friend struct ControllerTestAccess; // Host boundary test injects near-overflow revision.
  void setConfiguration(Configuration config) {
    state_.enabled = config.enabled;
    state_.calibrated = config.calibrated && config.low >= 0 && config.high <= 180 && config.low < config.high;
    state_.ready = config.ready && config.position >= config.low * 1000 && config.position <= config.high * 1000;
    low_ = config.low; high_ = config.high; position_ = config.position;
  }
  struct Reservation { uint64_t at = 0; uint32_t duration = 0; };
  std::mutex mutex_;
  State state_;
  Reservation budget_[BUDGET_MS / MIN_DURATION_MS] = {};
  EpochSource random_;
  PwmWriter write_;
  void* context_;
  uint64_t deadline_ = 0, lastTick_ = 0;
  int low_ = 90, high_ = 90;
  int32_t position_ = 90000, target_ = 90000;
  int64_t positionFine_ = 900000;
  bool positionFineValid_ = false;
  Result result(int status, const char* error) { return {status, error, state_}; }
  void close() {
    state_.open = false;
    state_.tapping = false;
    state_.session[0] = '\0';
    positionFineValid_ = false;
  }
  void rotateEpoch() {
    close();
    state_.enabled = false;
    state_.ready = false;
    char old[33];
    memcpy(old, state_.epoch, sizeof(old));
    do { random_(context_, state_.epoch); } while (!strcmp(old, state_.epoch));
    state_.revision = 0;
    // Reservations deliberately survive revision rollover (unlike a real boot).
  }
  void advance() {
    if (state_.revision >= MAX_REVISION - 1) rotateEpoch();
    else ++state_.revision;
  }
  void expire(uint64_t now) {
    if (state_.tapping && now >= deadline_) {
      state_.tapping = false;
      positionFineValid_ = false;
      advance();
    }
  }
};
}  // namespace tail_v2
