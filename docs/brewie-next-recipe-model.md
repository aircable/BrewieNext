# BrewieNext Recipe Model

## Purpose

Recipes contain brew-specific intent: ingredients, water volumes, two mash rests,
boil duration, hop-cage timing, cooling target, and sedimentation time. Hardware properties and
safety limits are machine configuration and must not be stored in recipes.

Fixture recipes live in `fixtures/recipes/` during development and are
validated by `services/runtime/recipe.schema.json`. On the embedded
system, bundled recipes ship with an application release while edited recipes
are stored persistently under `/var/lib/brewie/recipes`.

## Hop timing

`minutes_remaining` is the time remaining in the boil when a cage releases:

```text
release_after_min = boil.duration_min - minutes_remaining
```

For a 100-minute boil, cages configured for 60, 30, 10, and 5 minutes remaining
release after 40, 70, 90, and 95 elapsed minutes. Cages 1–4 are defined exactly
once and their times descend in physical cage order. Every cage is released;
`hop` may be blank and `amount_g` may be zero when a cage is intentionally empty.

The workflow divides the boil into `boiling` (heat-up and the initially
unhopped interval) and `hopping` (the final 60 minutes by default). Hopping
uses four AVR-managed `P103` stages. Released cages stay open cumulatively while
the boil pump circulates through them: `[1]`, `[1,2]`, `[1,2,3]`, then all four.
The final stage runs for five minutes, then enters cooling directly. There is
no whirlpool phase. After cooling, `sedimentation.duration_min` keeps the wort
undisturbed before transfer; the development default is 20 minutes.

## Runtime globals

Selecting **Use for brew** validates the recipe and creates an immutable
session snapshot. The runtime projects recipe fields into semantic globals;
for example, `recipe.water.mash_volume_L` becomes `mash_water_volume_L` and
`recipe.water.sparge_volume_L` becomes `sparge_water_volume_L`. Stored-recipe
changes must not alter an already-started brew.

Procedures reference these names directly. They do not copy recipe defaults
or receive mutable node-specific aliases such as `target_volume`:

```yaml
transition:
  - weight_boil_tank >= (mash_water_volume_L - 0.5): wait_for_user_to_continue
```

Recipe globals, live sensors, user inputs, machine configuration, and engine
variables are separate namespaces. Recipe globals are read-only during a brew.

The mash schedule contains exactly two ordered rests for modern malts. Each
rest defines its own target temperature and duration. Runtime projection adds
`mash_rest_1_temperature_C`, `mash_rest_1_duration_min`,
`mash_rest_2_temperature_C`, `mash_rest_2_duration_min`, and
`mash_total_duration_min` while preserving the complete `mash_steps` list.

The workflow begins with `prepare_brew`. It presents the recipe summary, grain
bill, hop-cage loading plan, and machine checklist before `fill_mash_water`.

## Editor and API

The browser **Recipes** page edits water, fermentables, the two mash rests,
sparge temperature and cycle count, boil timing, four hop cages, cooling
temperature, and sedimentation time. The hop timeline displays minutes
remaining and computed elapsed release time.

- `GET /api/recipes`
- `GET /api/recipes/<id>`
- `POST /api/recipes/<id>/validate`
- `PUT /api/recipes/<id>`
- `GET /api/graphs/beer_brewing/resolve?recipe=<id>`

Run `npm run test` from the repository root to exercise recipe validation and
the complete workflow against the development fixture.
