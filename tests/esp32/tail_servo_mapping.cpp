#include "TailServoMapping.h"
#include <cassert>
#include <cstdio>

int main() {
  using tail_servo::pulseForAngle;
  // Synthetic configurations cover supported calibration limits and curl.
  for (int low = 500; low <= 1000; low += 50) {
    for (int high = 2000; high <= 2500; high += 50) {
      for (int curl = 0; curl <= 150; curl += 50) {
        for (int gain = 100; gain <= 150; gain += 10) {
          int previous = low;
          for (int angle = 0; angle <= 180; ++angle) {
            const int pulse = pulseForAngle(angle, low, high, curl, gain);
            assert(pulse >= low && pulse <= high && pulse >= previous);
            assert(angle < 90 ? pulse <= 1500 : pulse >= 1500);
            if (angle == 90) assert(pulse == 1500);
            const int old = 1500 + (angle - 90) * (high - low) / 540
                - (angle < 90 ? curl * (90 - angle) / 90 : 0);
            if (gain == 100) assert(pulse == old);
            if (angle < 90) assert(pulse <= old);
            if (angle > 90) assert(pulse >= old);
            previous = pulse;
          }
        }
      }
    }
  }
  assert(pulseForAngle(0, 1000, 2000, 0, 150) == 1251);
  assert(pulseForAngle(180, 1000, 2000, 0, 150) == 1749);
  assert(pulseForAngle(-1, 1000, 2000, 150, 150) == pulseForAngle(0, 1000, 2000, 150, 150));
  assert(pulseForAngle(181, 1000, 2000, 150, 150) == pulseForAngle(180, 1000, 2000, 150, 150));
  for (int angle = 0; angle <= 180000; ++angle) {
    const int old = 1500 + static_cast<int64_t>(angle - 90000) * 2000 / 540000
        - (angle < 90000 ? static_cast<int64_t>(150) * (90000 - angle) / 90000 : 0);
    assert(tail_servo::pulseForMilliAngle(angle, 500, 2500, 150, 100) == old);
    const int expanded = tail_servo::pulseForMilliAngle(angle, 500, 2500, 150, 150);
    assert(expanded >= 500 && expanded <= 2500);
    assert(angle < 90000 ? expanded <= old : expanded >= old);
  }
  puts("PASS: servo mapping preserves neutral/direction, expands width, and stays within calibration");
}
