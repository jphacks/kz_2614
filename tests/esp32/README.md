# Follower v2 native verification

These tests compile the actual `TailV2.h`, `TailV2Wire.h` and `TailV2Http.h` used by
`ESP32-Follower.ino`. PWM and ESP-IDF I/O are simulated; no ESP32 is contacted.
See [the repair report](../../docs/esp32/mvp-v2-repair.md) for acceptance boundaries.

Use Linux with C/C++17 compilers, pthreads, AddressSanitizer/UndefinedBehaviorSanitizer,
and Python 3. Download the official **cJSON v1.7.19** `cJSON.c` and `cJSON.h` to a
scratch directory, outside this repository:

- https://raw.githubusercontent.com/DaveGamble/cJSON/v1.7.19/cJSON.c
- https://raw.githubusercontent.com/DaveGamble/cJSON/v1.7.19/cJSON.h

The script verifies pinned SHA-256 hashes before compilation. cJSON is already
provided by ESP-IDF on device; it is not vendored or newly licensed into this repo.

```sh
python3 scripts/esp32/test_native.py --cjson-dir /tmp/cjson-1.7.19
```

`CC` and `CXX` may name local compiler executables. Tests require an environment
where sanitizer thread/leak inspection is permitted; a ptrace-restricted sandbox
can fail LeakSanitizer independently of the firmware. Do not report that as PASS.

For the PC-side compatibility test, use a Python environment with `httpx` and
supply a checkout containing `server/ai/llm/tail_v2.py`:

```sh
python3 scripts/esp32/test_native.py --cjson-dir /tmp/cjson-1.7.19 \
  --pc-worktree /path/to/pc-checkout
```

Without the PC checkout, that one optional test is explicitly skipped. The other
tests never depend on PC source. `tests/fixtures/tail_v2.json` is the same fixture
as RyuuzinnRaika's PC tests. The prepared fixture has permission enabled; actual
firmware boot is separately tested as closed/idle, revision 0, permission off.

- Python drives compiled firmware over stdin/stdout: JSON schema, normal flow,
  delayed/duplicate/reordered requests, epoch changes, budget, bounds, PWM hold,
  narrow-range speed, expiry, local/legacy stop, and disabled/uncalibrated modes.
- 500 concurrent C++ races cover both open/stop and tap/stop. Blocked PWM/local
  preparation callbacks prove that stop cannot ACK before the write completes.
- Servo mapping tests cover supported calibration/curl combinations, neutral,
  monotonic direction, unchanged 100% output, expanded 100–150% amplitude,
  calibrated pulse bounds, and every milli-degree at the widest synthetic range.
- The real HTTP adapter compiled with IDF I/O stubs rejects partial/slow/oversized/
  chunked/wrong-type bodies, including a late final chunk, and accepts fragments.
  It checks no-store and connection closure. Actual IDF socket scheduling and
  hardware latency still require the physical test plan.

Firmware-only template build (public configuration, **never upload this output**):

```sh
python3 scripts/esp32/build_template.py --cli /path/to/arduino-cli
```

Use Arduino-ESP32 **3.3.12**, FQBN `esp32:esp32:esp32`. The helper stages this
checkout's sources with public templates in a new temporary directory, records
input hashes and `upload_eligible=false`, and never copies real Wi-Fi settings.
It has no upload command. Reader source is unchanged; `--role Reader` is available
for a separate build if needed.
