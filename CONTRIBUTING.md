# Contributing

Start with `npm install` and `npm run dev`, then run `npm run verify` before
opening a pull request. Keep frontend and runtime behavior together when an API
contract changes, and include screenshots for visible UI changes.

Changes to the editor, simulator, runner, hardware adapter, or packaging belong
in this repository. Changes to brewing behavior, state transitions, timing, or
recipe variables belong in
[BrewieNextProcedures](https://github.com/aircable/BrewieNextProcedures).
Update `programs.lock.json` only to a published release and record its SHA-256.

Use short imperative commit subjects. Pull requests should explain the user or
machine behavior affected, list verification commands, and distinguish tested
hardware behavior from simulation or inference. Never enable AVR output in an
automated test or development default.
