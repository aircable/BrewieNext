#!/bin/sh

ROOT=${BREWIE_WEB_ROOT:-/usr/share/brewie}
OUT="$ROOT/system-info.json"
TMP="${OUT}.tmp"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/[[:cntrl:]]/ /g'
}

kernel=$(uname -a 2>/dev/null || echo unknown)
release=$(cat "$ROOT/current/RELEASE" 2>/dev/null || echo unknown)
backlight=$(cat /sys/class/backlight/backlight/brightness 2>/dev/null || echo unknown)
touch_device=$(cat /sys/class/input/event0/device/name 2>/dev/null || echo unknown)

mkdir -p "$ROOT" 2>/dev/null || exit 0
{
  printf '{"kernel":"%s","release":"%s","backlight":"%s","touch_device":"%s"}\n' \
    "$(json_escape "$kernel")" "$(json_escape "$release")" \
    "$(json_escape "$backlight")" "$(json_escape "$touch_device")"
} > "$TMP" 2>/dev/null && mv "$TMP" "$OUT" 2>/dev/null || rm -f "$TMP"

exit 0
