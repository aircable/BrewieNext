# Brewie A13 Web Rendering Guideline

This guideline records the working web-rendering path validated on the real
Brewie B20 hardware running ReLinux. It is an implementation baseline for the
future Brewie web frontend, not yet the production launcher.

For the actual frontend deployment workflow, see
[brewie-web-installation.md](brewie-web-installation.md) and
[brewie-web-use.md](brewie-web-use.md).

## Hardware assumptions

- Allwinner A13, ARMv7, 512 MB RAM
- LCD framebuffer `/dev/fb0`, reported as `480×272`
- Physical panel mounted portrait (`272×480` UI coordinates)
- FT5x06 touchscreen at `/dev/input/event0`
- Qt 5 using the `linuxfb` platform plugin

The live device tree must define the touchscreen dimensions:

```dts
touchscreen-size-x = <480>;
touchscreen-size-y = <272>;
```

After changing the DTS, perform a clean Linux/Buildroot rebuild and boot the
new DTB. Verify the running system under
`/sys/firmware/devicetree/base`, not only the source file.

## Buildroot and image requirements

The verified renderer uses Qt WebKit and the `qt-webkit-kiosk` executable.
Enable Qt WebKit, WebChannel, and WebSockets as required by the page. The
image also needs:

- Qt `linuxfb` platform support
- Qt font support and deployed DejaVu `.ttf` files
- BusyBox `httpd`
- a startup or launcher step that enables the backlight

Fonts are deployed through the ReLinux root overlay, for example
`board/brewie/B20/rootfs-overlay/usr/lib/fonts/`. The kiosk does not provide
fonts itself. Enable the panel before rendering:

```sh
echo 5 > /sys/class/backlight/backlight/brightness
```

## HTTP server and kiosk launch

Serve the static frontend locally with BusyBox:

```sh
/usr/bin/run-http-poc.sh
```

Start the kiosk with an explicit URL option and framebuffer:

```sh
QT_QPA_PLATFORM="linuxfb:fb=/dev/fb0" \
qt-webkit-kiosk -u http://127.0.0.1:8080/
```

The server must start before the kiosk. `No signal BREAK defined` is a
non-fatal kiosk message. `wget -qO- http://127.0.0.1:8080/` verifies serving,
but does not verify rendering.

## Rotation and touch rules

The Qt LinuxFB plugin in the tested image ignored `rotation=90`. The working
fallback is CSS rotation on the root screen:

```css
.screen {
  width: 272px;
  height: 480px;
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%) rotate(90deg);
}
```

CSS rotation fixes pixels but confuses old WebKit DOM hit-testing. Therefore:

1. Convert viewport touch coordinates back to portrait coordinates. For the
   clockwise CSS rotation, the inverse mapping is
   `portrait_x = viewport_y` and `portrait_y = 480 - viewport_x`.
2. Do not use `elementFromPoint()` for controls.
3. Hit-test controls using their untransformed `offsetLeft`, `offsetTop`,
   `offsetWidth`, and `offsetHeight` values.
4. On this image, activate controls on `touchstart`; `touchend` and
   `touchmove` were not reliably delivered by Qt WebKit.

The resulting page coordinate range is `x=0..272`, `y=0..480`.

## Verified acceptance results

- HTML, CSS, JavaScript, SVG, and local assets render on the physical panel.
- The page aligns with the top-left display corner after CSS rotation.
- Touch coordinates follow the finger correctly.
- PAUSE, ABORT, and RESET TEST activate their intended controls.
- The local timer and WebSocket probe load without external dependencies.

Before production, replace the touch-start compatibility path with a renderer
that provides a complete touch lifecycle, then retest press, move, release,
cancel, multi-touch rejection, startup time, memory, CPU, and recovery after
the HTTP server or kiosk restarts.
