#!/usr/bin/env bash
set -euo pipefail

SERVER_SIDE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SERVER_SIDE_DIR}/../.." && pwd)"
PYTHON="${SERVER_SIDE_DIR}/.venv/bin/python"
SERVER_MODULE="${SERVER_SIDE_DIR}/dashboard/server.py"

if (( $# > 2 )); then
    echo "Usage: $0 [bind-address] [port]" >&2
    exit 2
fi

BIND_ADDRESS="${1:-127.0.0.1}"
PORT="${2:-8765}"

if [[ ! "${PORT}" =~ ^[0-9]{1,5}$ ]]; then
    echo "Port must be an integer between 1 and 65535: ${PORT}" >&2
    exit 2
fi
PORT_NUMBER=$((10#${PORT}))
if (( PORT_NUMBER < 1 || PORT_NUMBER > 65535 )); then
    echo "Port must be an integer between 1 and 65535: ${PORT}" >&2
    exit 2
fi

if [[ ! -x "${PYTHON}" ]]; then
    echo "ServerSide virtual environment was not found. Run Programs/ServerSide/STTLLMTTS/third_party/setup_ubuntu.sh first." >&2
    exit 1
fi
if [[ ! -f "${SERVER_MODULE}" ]]; then
    echo "ServerSide HTTP entry point was not found: ${SERVER_MODULE}" >&2
    exit 1
fi
if [[ ! -f "${SERVER_SIDE_DIR}/.env" ]]; then
    echo "Warning: Programs/ServerSide/.env was not found. The server will use its defaults; copy .env.example if you need custom settings." >&2
fi

echo "Starting the ServerSide web application on ${BIND_ADDRESS}:${PORT_NUMBER}. Press Ctrl+C to stop."
if [[ "${BIND_ADDRESS}" != "127.0.0.1" && "${BIND_ADDRESS}" != "::1" && "${BIND_ADDRESS,,}" != "localhost" ]]; then
    echo "Non-loopback binding requires NENGITSUNE_DASHBOARD_TOKEN (32+ characters) and a restricted private network."
fi

cd -- "${REPO_ROOT}"
exec "${PYTHON}" -m Programs.ServerSide.dashboard.server --host "${BIND_ADDRESS}" --port "${PORT_NUMBER}"
