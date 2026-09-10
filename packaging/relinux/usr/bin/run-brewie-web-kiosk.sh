#!/bin/sh
set -eu

URL="${BREWIE_WEB_URL:-http://127.0.0.1:8080/?kiosk=1&debug=1}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-linuxfb:fb=/dev/fb0}"
export QT_QPA_GENERIC_PLUGINS="${QT_QPA_GENERIC_PLUGINS:-evdevtouch:/dev/input/event0}"
# BrewieNext maps raw FT5x06 coordinates into its rotated CSS viewport. Do not
# inherit the legacy profile transforms when this service is restarted over SSH.
unset QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS
unset QT_QPA_EVDEV_MOUSE_PARAMETERS

exec qt-webkit-kiosk -u "$URL"
