#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
service_name="nen-gitsune-dashboard.service"
action="${1:-start}"
if [[ $# -gt 0 ]]; then shift; fi

case "${action}" in
  start)
    if systemctl --user is-active --quiet "${service_name}"; then
      echo '会話サーバーは起動済みです: http://127.0.0.1:8765/'
      exit 0
    fi
    systemd-run --user --unit="${service_name}" --collect \
      --description='NenGitsune local voice dashboard' \
      --property=Restart=on-failure --property=RestartSec=3 \
      --property=TimeoutStopSec=15 \
      --working-directory="${project_dir}" \
      "${project_dir}/start-local.sh" "$@"
    ;;
  stop)
    systemctl --user stop "${service_name}"
    echo '会話サーバーを停止しました。'
    ;;
  restart)
    systemctl --user restart "${service_name}"
    ;;
  status)
    systemctl --user status "${service_name}" --no-pager
    ;;
  *)
    echo '使い方: service-local.sh [start|stop|restart|status]' >&2
    exit 2
    ;;
esac
