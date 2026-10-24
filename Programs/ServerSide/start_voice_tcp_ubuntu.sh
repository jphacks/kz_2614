#!/usr/bin/env bash
set -euo pipefail
server_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${server_dir}/.venv/bin/python" "${server_dir}/../../scripts/start_voice_tcp.py" "$@"
