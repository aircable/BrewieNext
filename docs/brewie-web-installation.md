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
/var/lib/brewie/programs/source          editable Git procedure checkout
/var/lib/brewie/recipes                  persistent user recipes
/var/lib/brewie/avr_state.json           persisted manual targets
/etc/brewie/machine.json                 machine-specific AVR calibration
/etc/brewie/github.json                  procedure repository configuration
/etc/brewie/github-token                 private GitHub access token
```

Before enabling hardware operation, create `/etc/brewie/machine.json` from the
machine's original `/usr/share/brewie/config.json`, or copy
`config/machine.example.json` and enter its measured calibration values. Never
reuse another machine's load-cell calibration. The required properties are
`toLiter`, `toLiterNull`, `mashTemperatureDelta`, and
`boilTemperatureDelta`; `boilingPoint` defaults to 100 °C.

The application release installer creates `/etc/brewie` when it is absent but
never creates or overwrites `machine.json`. Back up and restore that file when
reflashing the SD card.

The backend uses AVR mode `auto` on the appliance. At startup it sends `P999`,
waits for the following status record to confirm that physical valve movement
has finished, then sends `P80` with this calibration to power on and initialize
the AVR sensors. Without a valid calibration file, safe reset still runs but
the AVR is not reported ready for hardware commands. Restarting the backend
therefore stops an active brew and returns outputs to their safe state.
Hardware testing must be supervised.

## Saving procedures to GitHub

Procedure Studio can use a personal `BrewieNextProcedures` repository as the
machine's persistent workflow storage. **Load from GitHub once before editing.**
The application release contains a flat, immutable procedure bundle for safe
startup, not a Git working tree. The first Load clones the configured repository
into `/var/lib/brewie/programs/source`, validates it, and makes that checkout the
source for subsequent edits and brews. Application upgrades and reboots then
continue to use the checkout.

Edits made before the first Load belong to the installed fallback bundle. They
cannot be committed by **Save GitHub**, and the initial Load does not migrate
them. The intended appliance workflow is therefore:

1. Install BrewieNext and its pinned procedure bundle.
2. Configure GitHub access as described below.
3. Open Procedure Studio and select **Load** once.
4. Edit a workflow or procedure and use its editor **Save** button.
5. Select **Save GitHub**. `main · SAVED` confirms validation, commit, and push
   succeeded and the checkout has no remaining changes.

Load is disabled during an active brew. Save may run during a brew because the
execution engine uses the immutable workflow snapshot captured when that brew
started; saved changes apply only to a future session.

### Create a fine-grained token

In GitHub, open **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**. GitHub's current instructions are
in [Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Use the narrowest access that permits a Git push:

- Select the user or organization that owns the procedure repository as the
  resource owner.
- Limit repository access to the one `BrewieNextProcedures` repository.
- Set repository **Contents** permission to **Read and write**. Metadata read
  access is added by GitHub.
- Give the token an expiration date and replace it on the machine before it
  expires. Organization policy may require an owner to approve the token.

GitHub displays the token only once. Store it directly on the Brewie and do not
put it in `github.json`, a shell command, a Git repository, documentation, or a
support log:

```sh
mkdir -p /etc/brewie
umask 077
touch /etc/brewie/github-token
vi /etc/brewie/github-token
chmod 600 /etc/brewie/github-token
```

The file contains only the token followed by a newline. Its supported name is
`github-token` (hyphen), not `github_token`. The backend deliberately rejects a
token file accessible by group or other users.

Create `/etc/brewie/github.json` separately:

```json
{
  "repository": "https://github.com/OWNER/BrewieNextProcedures.git",
  "branch": "main",
  "username": "YOUR_GITHUB_LOGIN",
  "token_file": "/etc/brewie/github-token"
}
```

`repository` must use HTTPS and must not contain credentials. `token_file`
points to the secret; it does not contain the secret itself. Restart the backend
after changing this configuration:

```sh
/etc/init.d/S85brewie-backend restart
```

The Studio header should initially report `main · NOT LOADED`. After the first
successful Load it reports `main · SAVED`. If the remote repository changes
elsewhere, or local files have not been saved, synchronization stops rather than
silently overwriting either version.
