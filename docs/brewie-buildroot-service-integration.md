# BrewieNext Buildroot Service Integration

This is the handoff specification for the next ReLinux Buildroot image. The
service hooks are image-level integration; application releases only replace
`/usr/share/brewie/releases/<version>` and the `current` symlink.

## Overlay files

Copy these files into the Buildroot root overlay:

```text
packaging/relinux/etc/init.d/S80brewie-web
packaging/relinux/etc/init.d/S85brewie-backend
packaging/relinux/etc/init.d/S90brewie-kiosk
packaging/relinux/usr/bin/brewie-system-info.sh
packaging/relinux/usr/bin/run-brewie-web.sh
packaging/relinux/usr/bin/run-brewie-web-kiosk.sh
packaging/relinux/usr/share/brewie/startup.html
```

The resulting target paths must be:

```text
/etc/init.d/S80brewie-web
/etc/init.d/S85brewie-backend
/etc/init.d/S90brewie-kiosk
/usr/bin/brewie-system-info.sh
/usr/bin/run-brewie-web.sh
/usr/bin/run-brewie-web-kiosk.sh
/usr/share/brewie/startup.html
```

Ensure the scripts are executable. Buildroot starts `S80`, `S85`, and `S90` during
boot and stops them in reverse order during shutdown.

## Runtime contract

The scripts expect these application-owned files:

```text
/usr/share/brewie/current/index.html
/usr/bin/run-brewie-web.sh
/usr/bin/run-brewie-web-kiosk.sh
```

The HTTP root for this service should be `/usr/share/brewie`, so the
application release is available as `/current/`. `S90brewie-kiosk` first opens
`/startup.html`. That page displays runtime information from
`/system-info.json` and redirects to `/current/?kiosk=1` after three seconds when an
application release is installed. Without a release, it remains on the
startup page. The release also contains `backend/`, bundled `recipes/`, and a
pinned procedure archive; `S85` starts the Flask API on port 8081 with the
active program under `/var/lib/brewie/programs/current` plus persistent recipe
overrides in `/var/lib/brewie/recipes`. Buildroot
must provide Python 3 and Flask, Flask-CORS, PyYAML, and jsonschema.

If `current/index.html` or a launcher is absent, the corresponding service
exits successfully without starting. This allows a base image to include the
hooks even before an application release is installed.

Both services use BusyBox `start-stop-daemon` and PID files under
`/var/run`. They support `start`, `stop`, `restart`, and `reload`:

```sh
/etc/init.d/S80brewie-web restart
/etc/init.d/S85brewie-backend restart
/etc/init.d/S90brewie-kiosk restart
```

The web server must be started before the kiosk. The kiosk launcher uses the
local HTTP URL and enables the backlight through the existing launcher.

## Buildroot acceptance checks

After boot:

```sh
ps | grep '[h]ttpd'
ps | grep '[q]t-webkit-kiosk'
wget -qO- http://127.0.0.1:8080/current/ | head
```

For an image without `/usr/share/brewie/current/index.html`, both init hooks
must return success and neither process should be present.
