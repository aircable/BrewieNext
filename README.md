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
cd BrewieNext
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. The command creates `.dev/`, downloads and
verifies the pinned procedure release, starts the real Python runner with AVR
access disabled, and starts the web UI. Procedure edits are kept in
`.dev/programs/workspace`; the downloaded release remains unchanged.

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
