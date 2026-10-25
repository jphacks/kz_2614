#!/usr/bin/env python3
"""Build this checkout's public templates in a new directory. Never uploads."""
import argparse
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cli', default='arduino-cli')
p.add_argument('--role', choices=['Follower', 'Reader'], default='Follower')
a = p.parse_args()
stage = Path(tempfile.mkdtemp(prefix='esp32-v2-template-'))
name = f'ESP32-{a.role}'
source = REPO / 'firmware/esp32' / name
sketch = stage / name
sketch.mkdir()
for f in source.iterdir():
    if f.suffix in {'.ino', '.cpp', '.h'} and f.name not in {'WiFiConfig.h', 'TailConfig.h'} and not f.name.endswith('.example.h'):
        shutil.copy2(f, sketch / f.name)
for config in ['WiFiConfig'] + (['TailConfig'] if a.role == 'Follower' else []):
    shutil.copy2(source / f'{config}.example.h', sketch / f'{config}.h')
(stage / 'BUILD_ONLY_DO_NOT_FLASH.json').write_text(json.dumps({
    'upload_eligible': False, 'source': str(source),
    'sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sketch.iterdir()}
}, indent=2) + '\n')
print(f'Build-only templates, no private config: {stage}', flush=True)
subprocess.run([a.cli, 'compile', '--fqbn', 'esp32:esp32:esp32', '--jobs', '2',
                '--warnings', 'all', '--build-path', str(stage / 'output'), str(sketch)], check=True)
