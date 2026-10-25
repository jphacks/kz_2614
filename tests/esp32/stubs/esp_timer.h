#pragma once
#include <stdint.h>
inline int64_t fakeNowUs = 0;
inline int64_t esp_timer_get_time() { return fakeNowUs; }
