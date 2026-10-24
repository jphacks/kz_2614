#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SIDE_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
REQUIREMENTS_FILE="${SERVER_SIDE_DIR}/requirements.txt"
VENV_DIR="${SERVER_SIDE_DIR}/.venv"

if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
    echo "ServerSide requirements file was not found: ${REQUIREMENTS_FILE}" >&2
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    echo "sudo is required to install Git and Python packages." >&2
    exit 1
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y git python3 python3-venv

python_version_supported() {
    local version major minor patch
    version="$("$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" || return 1
    IFS=. read -r major minor patch <<< "${version}"
    (( major == 3 && minor >= 11 && minor < 14 ))
}

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_version_supported "${candidate}"; then
        PYTHON_BIN="$(command -v "${candidate}")"
        break
    fi
done

if [[ -z "${PYTHON_BIN}" ]] && apt-cache show python3.11 >/dev/null 2>&1; then
    "${SUDO[@]}" apt-get install -y python3.11 python3.11-venv
    PYTHON_BIN="$(command -v python3.11)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
    echo "Python 3.11 through 3.13 is required. This Ubuntu release's configured repositories do not provide a supported version; install Python 3.11-3.13 from an approved source, then rerun this script." >&2
    exit 1
fi

PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"${SUDO[@]}" apt-get install -y "python${PYTHON_VERSION}-venv"

if [[ -x "${VENV_DIR}/bin/python" ]]; then
    if ! python_version_supported "${VENV_DIR}/bin/python"; then
        echo "The existing virtual environment uses Python outside the supported 3.11-3.13 range: ${VENV_DIR}. Keep it intact and recreate it manually before rerunning this script." >&2
        exit 1
    fi
else
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install --requirement "${REQUIREMENTS_FILE}"

git --version
"${VENV_DIR}/bin/python" --version
echo "Setup complete. Virtual environment: ${VENV_DIR}"
