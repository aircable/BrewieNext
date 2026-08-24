# Repository and Release Model

BrewieNext is split at stable ownership boundaries:

- `aircable/BrewieNext` develops the editor, runner, simulator, touchscreen UI,
  hardware adapter, and appliance packaging.
- `aircable/BrewieNextProcedures` develops the executable YAML workflow,
  procedures, schemas, and machine/recipe contracts.
- `BrewieAppAtlas` preserves source evidence, reverse-engineering notes, and
  historical design material.
- `ReLinux` owns the kernel, device tree, Buildroot configuration, and generic
  startup services.

Application releases pin one published procedure release and SHA-256 in
`programs.lock.json`. A clean development checkout and a newly installed Brewie
therefore begin with identical machine programs. Development uses an editable
copy; released bundles remain immutable. Program changes are reviewed, tagged,
and released in their own repository before updating the application lock.

The simulator is not a second execution implementation. `npm run dev` starts
the same Python workflow runner used on the appliance, substituting only the
simulated hardware adapter. This keeps timing, user input, transitions, and
machine-state behavior reviewable without enabling physical outputs.
