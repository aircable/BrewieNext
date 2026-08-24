# Brewie B20 Touchscreen PoC

## Status

The first hardware touchscreen test is working on the ReLinux Brewie B20
image. The display, backlight, FT5x06 input driver, Qt evdev input plugin,
portrait layout, calibrated touch coordinates, and touch controls have all
been verified on the real machine.

The PoC is intentionally local and safe: it has no procedure engine, MQTT,
HAL, GPIO, serial, or actuator connection. The controls only change local
test-screen state.

## Hardware and ReLinux configuration

The relevant project is `../ReLinux`:

- Board: Allwinner A13 / ARMv7 / 512 MB
- LCD: 480×272 RGB framebuffer at `/dev/fb0`
- Physical mounting: portrait
- Touch controller: FT5x06 at I²C2 address `0x38`
- Interrupt: PG11
- Reset: PC03
- Input device: `/dev/input/event0`
- Input name: `generic ft5x06 (79)`

The board DTS is `../ReLinux/board/brewie/sun5i-brewie.dts`. The touchscreen
node must include:

```dts
touchscreen-size-x = <480>;
touchscreen-size-y = <272>;
```

Without those properties, Qt reported an input range of `0..65535` on both
axes. The properties are only effective after a clean Linux/Buildroot rebuild
and deployment of the resulting boot image/DTB. Verify the live DTB rather
than only the source file:

```sh
find /sys/firmware/devicetree/base -type f | grep touchscreen-size
```

## Fonts and backlight

Qt does not ship fonts. Enabling DejaVu in Buildroot did not place fonts in
the directory searched by this Qt image, so the reliable solution was to put
the `.ttf` files in the ReLinux root overlay:

```text
board/brewie/B20/rootfs-overlay/usr/lib/fonts/
```

The PoC launcher enables the backlight before starting QML:

```sh
echo 5 > /sys/class/backlight/backlight/brightness
```

## PoC deployment and launch

The PoC files are in `touchscreen-poc/`:

```sh
scp touchscreen-poc/brewie-touch-poc.qml root@BREWIE_IP:/usr/share/brewie/
scp touchscreen-poc/run-touch-poc.sh root@BREWIE_IP:/usr/bin/
ssh root@BREWIE_IP chmod +x /usr/bin/run-touch-poc.sh
```

The launcher uses the existing Qt framebuffer path and explicitly binds the
touch plugin:

```sh
QT_QPA_PLATFORM="linuxfb:fb=/dev/fb0"
QT_QPA_GENERIC_PLUGINS="evdevtouch:/dev/input/event0"
```

Run it with:

```sh
/usr/bin/run-touch-poc.sh
```

For raw and QML diagnostics:

```sh
RAW_TOUCH_DEBUG=1 /usr/bin/run-touch-poc.sh
```

This runs `od` against `/dev/input/event0` beside QML and prints
`QML_TOUCH_*` messages.

## Portrait touch calibration

The framebuffer remains landscape while the physical display is portrait.
The UI is authored in `272×480` portrait coordinates and rotated into the
`480×272` framebuffer.

The touchscreen active area was measured as:

```text
Portrait top-left     → framebuffer (28,241)
Portrait bottom-right  → framebuffer (456,27)
```

The PoC applies an affine mapping using those two corners, including axis
rotation, scaling, and margins. Button hit testing is handled at the root
framebuffer level because transformed child QML items did not receive touch
events reliably on this Qt/linuxfb stack.

## Verified results

- Backlight can be enabled from the launcher.
- DejaVu fonts render correctly from `/usr/lib/fonts`.
- Qt loads `evdevtouch` and opens `/dev/input/event0`.
- Raw FT5x06 events appear while QML is running.
- QML receives touch events.
- Portrait touch coordinates map correctly after calibration.
- PAUSE/RESUME, ABORT, and RESET TEST respond and show pressed feedback.

## Web-rendering result

The HTTP renderer test is now working on the same hardware. It uses BusyBox
`httpd` plus the Qt WebKit kiosk and verifies HTML/CSS/JavaScript/SVG,
portrait rendering, calibrated touch coordinates, and button activation.
The implementation details and limitations are recorded in
`brewie-a13-web-kiosk-guideline.md`.

This remains a hardware renderer baseline rather than the production
frontend. Before adopting it for the Svelte application, measure startup
time, RAM, CPU, timer stability, touch lifecycle behavior, and recovery when
the kiosk or HTTP server restarts.
