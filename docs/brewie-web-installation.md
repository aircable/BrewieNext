# BrewieNext Installation Guide

## ReLinux prerequisites

The image provides `/dev/fb0`, Qt WebKit kiosk support, BusyBox `httpd`, Python
3 with the runtime dependencies, DejaVu fonts in `/usr/lib/fonts`, the backlight
driver, and the Brewie AVR serial device. Copy `packaging/relinux/` into the
ReLinux root overlay when building the image. Its `S80`, `S85`, and `S90`
services remain harmless when no application is installed.

## Development installation

```sh
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. The bootstrap downloads the program release pinned
by `programs.lock.json`, verifies its SHA-256, and creates an editable copy in
`.dev/programs/workspace`. The authoritative Python runner starts in simulation
mode with `BREWIE_AVR_ENABLED=0`.

## Appliance installation

Build and transfer a release as described in
`docs/brewie-next-release-packaging.md`. Runtime paths are:

```text
/usr/share/brewie/current                 active application
/var/lib/brewie/programs/current         active machine programs
/var/lib/brewie/recipes                  persistent user recipes
/var/lib/brewie/avr_state.json           persisted manual targets
```

The backend uses AVR mode `auto` on the appliance and sends acknowledged `P999`
at startup when safe start is enabled. Restarting the backend therefore stops
an active brew and returns outputs to their safe state. Hardware testing must
be supervised.
