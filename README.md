# BrewieNext

BrewieNext is the open development environment for the next-generation Brewie
B20 application. It contains the procedure editor, accelerated simulator,
runtime engine, Brewie touchscreen UI, and ReLinux deployment tooling.

The machine programs are versioned separately in
[BrewieNextProcedures](https://github.com/aircable/BrewieNextProcedures). This
repository pins an exact released program bundle in `programs.lock.json`, so
contributors run the same procedures in development and on the appliance.

## Try the simulator

Requirements: Node.js 20+, npm 10+, Python 3.10+, and Python's `venv` module.

```sh
git clone https://github.com/aircable/BrewieNext.git
git clone https://github.com/aircable/BrewieNextProcedures.git
cd BrewieNext
npm install
npm run dev
```

Keep the two repositories beside each other as shown. Procedure-editor saves
then belong to the `BrewieNextProcedures` Git checkout, where they can be
validated, reviewed, committed, and released independently.

Open <http://127.0.0.1:5173>. The command creates `.dev/`, downloads and
verifies the pinned procedure release, starts the real Python runner with AVR
access disabled, and starts the web UI. When a sibling `BrewieNextProcedures`
checkout is present, development reads and writes that Git working tree
directly. Otherwise, procedure edits are kept in `.dev/programs/workspace`;
the downloaded release remains unchanged.

## Starting the backend

`npm run dev` starts both required processes and is the normal command. It
waits for the backend at <http://127.0.0.1:8081/api/health> before starting the
frontend at port 5173. Stop both with `Ctrl-C`.

To run them separately for backend or API development, use two terminals:

```sh
# Terminal 1: bootstrap and start the Flask API/runner safely
npm run backend

# Terminal 2: start only the Svelte frontend
npm run dev:web
```

The development backend always sets `BREWIE_AVR_ENABLED=0`; it cannot actuate
hardware. Verify it with:

```sh
curl http://127.0.0.1:8081/api/health
```

On ReLinux, `/etc/init.d/S85brewie-backend` starts the packaged backend on port
8081. `S90brewie-kiosk` starts it automatically before launching the kiosk.
See [the development guide](docs/development.md) for paths and troubleshooting.

## Repository layout

- `apps/web/` — Svelte editor, live-brew view, and 480×272 kiosk UI.
- `services/runtime/` — Flask API, runner, AVR adapter, validation, and tests.
- `fixtures/recipes/` — safe recipes for development and simulation.
- `packaging/` — ReLinux init scripts, installer, and release builder.
- `docs/` — architecture, procedure-language, kiosk, and deployment guides.
- `scripts/` — development bootstrap and test orchestration.

## Verification

```sh
npm run verify
npm run package -- 0.5.0
```

`npm run verify` checks the Svelte application, runs the backend tests, and
builds the production UI. Brewie hardware output is never enabled by the
development command; real-machine testing must be deliberate and supervised.

This project is under active development. Treat the simulator and procedure
editor as review tools until a hardware release is explicitly identified.

Versioned application packages are published on the GitHub Releases page.
