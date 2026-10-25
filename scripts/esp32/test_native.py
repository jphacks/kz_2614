#!/usr/bin/env python3
"""Compile the firmware core/wire code and run deterministic host tests (no devices)."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import sys

REPO = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cjson-dir', type=Path, required=True, help='Official cJSON 1.7.19 source directory')
p.add_argument('--pc-worktree', type=Path, help='Optional RyuuzinnRaika checkout for validator compatibility')
a = p.parse_args()
expected = {'cJSON.c': '298581a04a36c0165da4b0aade235c23088cb2faa58651d720ea2f3706ed0b0d',
            'cJSON.h': '25b0145150d500498e4d209cec69c18c42cf818bffcc54690be3b895a2a16dee'}
for name, sha in expected.items():
    if hashlib.sha256((a.cjson_dir / name).read_bytes()).hexdigest() != sha:
        raise SystemExit(f'Expected official cJSON v1.7.19: {name} checksum mismatch')
build = Path(tempfile.mkdtemp(prefix='esp32-v2-native-'))
flags = ['-g', '-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie']
subprocess.run([os.environ.get('CC', 'cc'), *flags, '-c', str(a.cjson_dir / 'cJSON.c'), '-o', str(build / 'cjson.o')], check=True)
include = REPO / 'firmware/esp32/ESP32-Follower'
for name in ['tail_v2_runner', 'tail_v2_concurrency', 'tail_v2_http', 'tail_servo_mapping']:
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags, '-no-pie',
                    '-I', str(include), '-I', str(a.cjson_dir), '-I', str(REPO / 'tests/esp32/stubs'), str(REPO / f'tests/esp32/{name}.cpp'),
                    str(build / 'cjson.o'), '-o', str(build / name)], check=True)
subprocess.run([str(build / 'tail_v2_concurrency')], check=True)
subprocess.run([str(build / 'tail_v2_http')], check=True)
subprocess.run([str(build / 'tail_servo_mapping')], check=True)
env = {**os.environ, 'TAIL_V2_RUNNER': str(build / 'tail_v2_runner')}
if a.pc_worktree:
    env['TAIL_V2_PC_WORKTREE'] = str(a.pc_worktree.resolve())
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/esp32', '-p', 'test_*.py', '-v'],
               cwd=REPO, env=env, check=True)
print(f'Native firmware binaries: {build}')
