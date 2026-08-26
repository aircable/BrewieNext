# BrewieNext Web Use Guide

## Start the foundation manually

On the Brewie target, start the static server first:

```sh
/usr/bin/run-brewie-web.sh
```

From another shell, verify the assets:

```sh
wget -qO- http://127.0.0.1:8080/current/ | head
```

Then start the physical touchscreen kiosk:

```sh
/usr/bin/run-brewie-web-kiosk.sh
```

The launcher uses the tested settings:

```text
QT_QPA_PLATFORM=linuxfb:fb=/dev/fb0
QT_QPA_GENERIC_PLUGINS=evdevtouch:/dev/input/event0
```

The backlight is enabled by the HTTP server launcher. If the panel is dark,
check it directly:

```sh
cat /sys/class/backlight/backlight/brightness
echo 5 > /sys/class/backlight/backlight/brightness
```

## Use from a browser

Open:

```text
http://BREWIE_IP:8080/current/
```

The browser starts at the `beer_brewing` ordered workflow. Select a node to
open its procedure and select a state to edit its description, timeout,
actions, or transitions. Use **Validate draft** before **Save procedure**.
Saved procedures are backed up by the Flask backend under `.backups`.

Use **+ Procedure at end** to create or reuse a procedure and append it to the
workflow. From an open procedure, **+ Procedure after** inserts one after that
workflow step. **Remove from workflow** removes the selected step but preserves
its YAML procedure definition. Parallel branches must be edited as a group.

Open **Recipes** to load or create a recipe, edit water volumes, grain,
ordered mash steps, sparge settings, boil duration, all four hop cages,
cooling, and sedimentation time. Hop-cage times are minutes remaining in the
boil; every cage releases even when its contents are blank. The timeline shows
the calculated elapsed release time. Use **Validate**, then **Save recipe**.
**Use for brew** resolves the recipe values into the orchestration graph and
selects `prepare_brew` as the entry node.

On the target, edited recipes are stored under `/var/lib/brewie/recipes` so
application upgrades do not overwrite them.

Use **Live brew** to operate the authoritative procedure runner. **Start
simulation** uses modeled tanks and devices; **Start hardware** requires an
explicit confirmation and executes the same YAML transitions against the AVR.
Select **60×** to compress an approximately five-hour simulated brew into about
five minutes. Hardware mode is always fixed at 1×. Start, pause, resume, abort,
or navigate while the active states, workflow nodes, readouts, valves, pumps,
and heaters update from backend snapshots.

Procedure actions run once when a state is entered. Conditions are evaluated
in their YAML order and `on_exit` runs before every normal, error, navigation,
or abort departure. Therefore a Brewmaster valve/pump/heater intervention does
not pause the runner and is not overwritten by pause/resume; a later state may
still intentionally command that device. Waiting freezes accelerated simulated
time, while hardware timeouts continue in real time.

The Brewmaster panel below the local-screen preview is a second 272×480 Brewie
screen. When the backend reports a live AVR connection, valve and pump buttons
send their registered direct commands, while **Close all** sends `P999`.
Selecting either heater opens a numeric target keypad; enter `0` to disable it.
An energized heater is red, while an enabled heater currently deferred by AVR
power arbitration is yellow. In simulation mode the same controls modify the
simulation HAL. On the physical touchscreen, use **Machine status** to open
the overview and **Back to main screen** to return to the active instruction.

Manual Brewmaster controls bypass procedure sequencing. Use them only while
observing tank levels and hose/valve routing. The backend accepts semantic,
same-host browser commands and does not expose arbitrary AVR command strings.
When the backend starts, it sends `P999`, waits for its following completion
status, and then sends the calibrated `P80` power-on command. Initialization
must complete before the UI reports the AVR ready. Check
`initializationConfigured` and `initializationComplete` in `/api/health` when
diagnosing startup. Starting a hardware runner issues another `P999` safe
baseline. Runtime failure, completion, and abort also close all outputs.
Restarting the backend therefore stops active heating, pumping, valve activity,
and any AVR-managed brew step.

## Fixture mode versus API mode

The header shows `LOCAL FIXTURE` when the API is unavailable. In this mode the
page remains useful for layout and navigation testing, but edits are not
persisted. When the API is reachable on port 8081, procedure data is loaded
from the target and save/validation operations use the Flask backend.

## Stop and recover

Stop each foreground process with `Ctrl-C`. If the kiosk is stopped while the
server remains active, restart only:

```sh
/usr/bin/run-brewie-web-kiosk.sh
```

If the server is stopped, restart it before the kiosk. A blank kiosk screen
usually means the HTTP server is not running or the URL was not passed with
`-u`. Inspect the page independently with `wget` before debugging Qt.
