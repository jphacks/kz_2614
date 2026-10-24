"""Start only the native TCP voice entry, using this PC's CUDA library paths."""
import os
from pathlib import Path
import sys
import sysconfig

root = Path(__file__).resolve().parents[1]
packages = Path(sysconfig.get_path('purelib'))
libraries = sorted((packages / 'nvidia').glob('*/lib'))
if (packages / 'torch' / 'lib').is_dir():
    libraries.append(packages / 'torch' / 'lib')
previous = os.environ.get('LD_LIBRARY_PATH', '')
os.environ['LD_LIBRARY_PATH'] = ':'.join([*(str(p) for p in libraries), *([previous] if previous else [])])
os.chdir(root)
os.execvpe(sys.executable, [sys.executable, '-m', 'server.ai.voice.tcp_server', *sys.argv[1:]], os.environ)
