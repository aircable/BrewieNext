#!/bin/sh
set -eu

BACKLIGHT=/sys/class/backlight/backlight/brightness
if [ -w "$BACKLIGHT" ]; then
    echo "${BREWIE_BACKLIGHT:-5}" > "$BACKLIGHT"
fi

ROOT=${BREWIE_WEB_ROOT:-/usr/share/brewie}
PORT=${BREWIE_WEB_PORT:-8080}

if [ ! -f "$ROOT/startup.html" ] && [ ! -f "$ROOT/current/index.html" ]; then
    echo "BrewieNext web assets not found in $ROOT" >&2
    exit 1
fi

exec busybox httpd -f -p "$PORT" -h "$ROOT"
