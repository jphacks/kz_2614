#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${project_dir}/server/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
    echo "先に ServerSide の Python 環境をセットアップしてください。" >&2
    exit 1
fi

exec "${python_bin}" "${project_dir}/scripts/start_local.py" "$@"
