# BrewieNext Web Architecture

## Repository layout

`services/runtime/` contains the Flask backend, runner, schemas, and validator.
`apps/web/` is the Svelte application. The
two are intentionally separate so the browser UI can evolve without putting
frontend code in the procedure toolchain.

## Frontend layers

- `src/lib/model.ts`: frontend-facing workflow projection, procedure, session, and screen
  types plus fixture data.
- `src/lib/api.ts`: REST loading and draft/validation operations.
- `src/lib/store.ts`: application state and selection logic.
- `src/components/GraphCanvas.svelte`: derived top-to-bottom workflow display.
- `src/components/StateInspector.svelte`: structured state editing.
- `src/components/BrewieScreen.svelte`: shared active-state screen.
- `src/App.svelte`: responsive browser/touch presentation.
- `services/runtime/runtime_engine.py`: authoritative workflow/procedure
  runner plus simulation and AVR hardware adapters.

The workflow canvas uses Svelte Flow only as a renderer. Nodes, fork/join
connectors, and positions are derived from the ordered YAML; they are not
authored as graph data. The fixture fallback
keeps the UI usable when the Flask service is not running.

## API boundary

The frontend expects these design-time operations:

- `GET /api/procedures`
- `GET /api/procedures/:name`
- `POST /api/procedures/:name/validate` with a draft document
- `PUT /api/procedures/:name` for an explicitly saved validated document
- `GET /api/schema`

Runtime uses polling REST endpoints:

- `POST /api/runtime/sessions` starts `simulation` or confirmed `hardware` mode;
- `GET /api/runtime/session` returns the complete UI snapshot;
- `POST /api/runtime/session/control` pauses, resumes, aborts, or changes speed;
- `POST /api/runtime/session/input` supplies the current YAML user input;
- `POST /api/runtime/session/navigate` moves between workflow steps;
- `/api/machine/*` exposes status and whitelisted Brewmaster interventions.

`WorkflowSession` owns sequencing and parallel joins. `ProcedureExecution`
owns ordered transitions, entry actions, timeouts, input, and `on_exit`. Both
modes use these classes; only `SimulatedHAL` versus `AvrHAL` changes. A future
event stream may replace polling without changing semantics.

## Safety boundary

The browser and touchscreen display snapshots and submit intent. They do not
read GPIO, interpret procedure YAML, or execute actions. Hardware mode requires
same-host requests and explicit confirmation, begins with `P999`, and closes
all outputs on completion, failure, or abort.
