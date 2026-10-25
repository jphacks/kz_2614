#!/bin/sh
set -eu
target=${VOICE_API_UPSTREAM:-}
config=${VOICE_PROXY_CONFIG_FILE:-/etc/nginx/voice-api.inc}
if [ -z "$target" ]; then
    cat > "$config" <<'EOF'
default_type application/json;
add_header Cache-Control "no-store" always;
return 503 '{"error":"voice_upstream_unconfigured","message":"音声サーバーへの接続が設定されていません。"}';
EOF
    exit 0
fi
case "$target" in
    *[!A-Za-z0-9.:/-]*) echo "Invalid characters in VOICE_API_UPSTREAM" >&2; exit 1 ;;
esac
if ! printf '%s' "$target" | LC_ALL=C grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?$'; then
    echo "VOICE_API_UPSTREAM must be an http(s) origin without path or credentials" >&2
    exit 1
fi
printf 'proxy_pass %s;\n' "$target" > "$config"
cat >> "$config" <<'EOF'
proxy_http_version 1.1;
proxy_intercept_errors off;
error_page 502 = @voice_unreachable;
error_page 504 = @voice_timeout;
proxy_set_header Host $proxy_host;
proxy_set_header Connection "";
proxy_ssl_server_name on;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
proxy_buffering off;
proxy_request_buffering off;
proxy_cache off;
proxy_connect_timeout 5s;
proxy_read_timeout 180s;
proxy_send_timeout 30s;
client_max_body_size 25m;
add_header Cache-Control "no-store" always;
EOF
