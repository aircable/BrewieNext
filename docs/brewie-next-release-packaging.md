# BrewieNext Release Packaging

The application, ReLinux platform, and machine programs have independent
release cycles. ReLinux supplies generic services and launchers. This
repository supplies the web UI and runtime. BrewieNextProcedures supplies the
versioned YAML machine program selected by `programs.lock.json`.

## Build an application release

From the repository root:

```sh
npm install
npm run verify
npm run package -- 0.5.0
```

The output is `releases/brewienext-0.5.0.tar.gz` plus its SHA-256 file. The
archive contains the built web application, Python runtime, bundled recipes,
manifest, lock file, and checksum-verified procedure release archive.

## Install on ReLinux

```sh
BREWIE_HOST=root@brewienext.local \
  sh packaging/scripts/install-release-to-target.sh \
  releases/brewienext-0.5.0.tar.gz
```

It installs application releases under `/usr/share/brewie/releases/` and
switches `/usr/share/brewie/current`. On first installation it also installs
the pinned machine program under `/var/lib/brewie/programs/releases/` and
creates `/var/lib/brewie/programs/current`. Later application upgrades preserve
an already active program version.

The installer stages and validates both archives before activation. Existing
release directories are never overwritten. Saved recipes remain under
`/var/lib/brewie/recipes` across upgrades.

## Rollback

Switch the relevant `current` symlink to an existing release, then restart the
backend and kiosk. Application and machine-program rollback are deliberately
separate operations.
