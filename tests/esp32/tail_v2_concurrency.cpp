#include "TailV2.h"
#include <atomic>
#include <cassert>
#include <thread>
#include <condition_variable>
#include <iostream>
using namespace tail_v2;
struct Hardware {
  std::mutex mutex;
  std::condition_variable cv;
  bool entered = false, release = false;
};
void epoch(void*, char* out) { strcpy(out, "7ef65580e6e346de8b503259d4e1f40c"); }
bool blockedWrite(void* p, int32_t) {
  auto& h = *static_cast<Hardware*>(p);
  std::unique_lock<std::mutex> lock(h.mutex);
  h.entered = true; h.cv.notify_all();
  h.cv.wait(lock, [&] { return h.release; });
  return true;
}
bool noop(void*, int32_t) { return true; }
Request request(const State& s) {
  Request r;
  strcpy(r.epoch, s.epoch); r.revision = s.revision;
  strcpy(r.session, "4e933206-d53c-49b3-8f8a-e86603c88aba");
  strcpy(r.command, "7ed373b7-b211-4f16-8cb5-0623fe19a3d2");
  r.duration = 500;
  return r;
}
int main() {
  // Both serializations of stop racing open/tap must close and fence.
  for (int i = 0; i < 500; ++i) {
    Controller c(epoch, noop, nullptr, {true, true, true, 0, 180, 90000});
    if (i % 2) assert(c.apply(Operation::Open, request(c.state(0)), 0).status == 200);
    Request r = request(c.state(0));
    Result start{}, stop{};
    std::atomic<bool> go{false};
    std::thread a([&] { while (!go.load()) std::this_thread::yield();
      start = c.apply(i % 2 ? Operation::Tap : Operation::Open, r, 0); });
    std::thread b([&] { while (!go.load()) std::this_thread::yield();
      stop = c.apply(Operation::Stop, r, 0); });
    go = true; a.join(); b.join();
    assert((start.status == 200) != (stop.status == 200));
    if (stop.status == 409) assert(c.apply(Operation::Stop, request(c.state(0)), 0).status == 200);
    assert(!c.state(0).open && !c.state(0).tapping);
    assert(c.apply(i % 2 ? Operation::Tap : Operation::Open, r, 0).status == 409);
  }
  // Network preparation cannot overtake a concurrent permission-releasing stop.
  for (int i = 0; i < 500; ++i) {
    Controller prepared(epoch, noop, nullptr, {false, true, false, 0, 180, 90000, true});
    Request r = request(prepared.state(0));
    Result start{}, stop{};
    std::atomic<bool> go{false};
    std::thread a([&] { while (!go.load()) std::this_thread::yield();
      start = prepared.apply(Operation::Prepare, r, 0); });
    std::thread b([&] { while (!go.load()) std::this_thread::yield();
      stop = prepared.apply(Operation::Disarm, r, 0); });
    go = true; a.join(); b.join();
    assert((start.status == 200) != (stop.status == 200));
    if (stop.status == 409) assert(prepared.apply(Operation::Disarm, request(prepared.state(0)), 0).status == 200);
    assert(!prepared.state(0).enabled && !prepared.state(0).permissionSession[0]);
    assert(prepared.apply(Operation::Prepare, r, 0).status == 409);
  }
  // PWM write and stop use the same lock: ACK cannot precede an in-flight write.
  Hardware h;
  Controller c(epoch, blockedWrite, &h, {true, true, true, 0, 180, 90000});
  c.apply(Operation::Open, request(c.state(0)), 0);
  c.apply(Operation::Tap, request(c.state(0)), 0);
  Request stop = request(c.state(0));
  std::thread tick([&] { c.tick(20); });
  { std::unique_lock<std::mutex> lock(h.mutex); h.cv.wait(lock, [&] { return h.entered; }); }
  std::atomic<bool> attempted{false}, done{false};
  std::thread stopping([&] { attempted = true; assert(c.apply(Operation::Stop, stop, 20).status == 200); done = true; });
  while (!attempted.load()) std::this_thread::yield();
  assert(!done.load());
  { std::lock_guard<std::mutex> lock(h.mutex); h.release = true; h.cv.notify_all(); }
  tick.join(); stopping.join();
  assert(done.load() && !c.state(20).tapping);
  c.tick(40); // Would deadlock if a queued tick could write after stop.
  // Explicit local preparation also serializes its hardware effect with stop.
  Hardware local;
  Controller c2(epoch, noop, nullptr, {true, true, true, 0, 180, 90000});
  Request before = request(c2.state(0));
  std::thread prepare([&] { c2.localChange([&] {
    blockedWrite(&local, 90000);
    return Configuration{true, true, true, 0, 180, 90000};
  }); });
  { std::unique_lock<std::mutex> lock(local.mutex); local.cv.wait(lock, [&] { return local.entered; }); }
  std::atomic<bool> localAttempted{false}, localDone{false};
  Result localStop{};
  std::thread stopLocal([&] { localAttempted = true; localStop = c2.apply(Operation::Stop, before, 0); localDone = true; });
  while (!localAttempted.load()) std::this_thread::yield();
  assert(!localDone.load());
  { std::lock_guard<std::mutex> lock(local.mutex); local.release = true; local.cv.notify_all(); }
  prepare.join(); stopLocal.join();
  assert(localStop.status == 409); // The local change advanced the revision first.
  assert(c2.apply(Operation::Stop, request(c2.state(0)), 0).status == 200);
  assert(!c2.state(0).open);
  std::cout << "PASS: 1000 concurrent request races + PWM/local-preparation/stop serialization\n";
}
