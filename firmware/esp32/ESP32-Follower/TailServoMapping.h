#pragma once
#include <stdint.h>

namespace tail_servo {
// Expand around neutral, keeping the original direction and curl correction.
// The calibrated pulse limits remain authoritative at every gain setting.
inline uint16_t pulseForMilliAngle(int32_t angle, uint16_t minimum, uint16_t maximum,
                                   uint16_t curl, int amplitudePercent) {
  angle = angle < 0 ? 0 : angle > 180000 ? 180000 : angle;
  amplitudePercent = amplitudePercent < 100 ? 100 : amplitudePercent > 150 ? 150 : amplitudePercent;
  int32_t offset = (static_cast<int64_t>(angle - 90000) * (maximum - minimum)) / (180000 * 3);
  if (angle < 90000) offset -= (static_cast<int64_t>(curl) * (90000 - angle)) / 90000;
  int32_t pulse = 1500 + offset * amplitudePercent / 100;
  pulse = pulse < minimum ? minimum : pulse > maximum ? maximum : pulse;
  return static_cast<uint16_t>(pulse);
}
inline uint16_t pulseForAngle(int angle, uint16_t minimum, uint16_t maximum,
                              uint16_t curl, int amplitudePercent) {
  angle = angle < 0 ? 0 : angle > 180 ? 180 : angle;
  return pulseForMilliAngle(angle * 1000, minimum, maximum, curl, amplitudePercent);
}
}  // namespace tail_servo
