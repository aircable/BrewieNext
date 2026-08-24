# Repository Guidelines

## Structure

`apps/web/` contains the Svelte UI. `services/runtime/` contains the Python API,
runner, validation, and hardware adapter. `fixtures/` contains simulation data,
`packaging/` contains ReLinux integration, and `docs/` contains design and use
guides. Brewing programs are released by the separate BrewieNextProcedures
repository and pinned in `programs.lock.json`.

## Commands

Run `npm install` once, `npm run dev` for the complete local simulator,
`npm run verify` before committing, and `npm run package -- VERSION` to build an
embedded release. Development must keep `BREWIE_AVR_ENABLED=0`.

## Style and tests

Use two-space indentation in TypeScript, Svelte, JSON, and YAML; use four spaces
in Python. Keep identifiers `snake_case` in Python/YAML and `camelCase` in
TypeScript. Add Python tests beside runtime modules as `test_*.py`. Exercise UI
changes in both desktop and 480×272 kiosk layouts.

## Safety and scope

Do not silently change machine behavior in application code when it belongs in
a procedure. Preserve the distinction between simulation, inferred behavior,
and hardware-confirmed behavior. Hardware commands, heaters, pumps, and valves
require deliberate supervised testing; never make them a development default.
