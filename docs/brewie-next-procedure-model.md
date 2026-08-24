# BrewieNext Procedure Model

## Canonical Layers

There are two canonical YAML layers:

1. `beer_brewing.yml` is an ordered workflow with an explicit parallel block.
2. Each referenced procedure is an independently testable state machine.

The workflow is not an authored node-edge graph. The browser derives visual
nodes, vertical positions, fork connectors, and join connectors from the
ordered source.

## Ordered Workflow

Ordinary procedures appear in execution order. Parallel work is nested at the
point where it starts:

```yaml
steps:
  - id: fill_sparge_water
    procedure: fill_sparge_water
  - parallel:
      id: mash_and_sparge_heat
      primary: mashing
      branches:
        - id: mashing
          procedure: two_rest_mashing
        - id: heat_sparge_water
          procedure: heat_sparge_water
      join:
        deadline: mashing
        require:
          heat_sparge_water: completed
        on_failure: sparge_heating_failed
  - id: sparging
    procedure: sparging
```

The primary branch determines the lifetime of the group. When two-rest mashing
completes, the executor publishes `parallel_primary_complete` to the sparge
heating branch. That branch then runs its `on_exit` cleanup and completes before
the group joins. A heating-branch failure is an error; sparge temperature is
regulated by the AVR and is not evaluated by this procedure.

The current foundation validates and visualizes this contract. Starting two
procedure runners, publishing primary-branch completion, and enforcing the join
policy remain execution-engine work; the editor does not yet run the branches
in parallel.

## Procedure State Machine

Canonical procedures use `name`, `start_state`, `states`, and `error_handler`.
Each state has `description`, optional `action`, `timeout_s`, `on_exit`, and
ordered transitions. State `on_exit` owns cleanup; procedure-level `finalize`
is accepted only for legacy files.

`next_phase` is a reserved successful-completion target. Other reserved targets
include `error_handler` and `loops`.

## Shared Runtime Screen

The engine-facing screen is derived from the active state, notifications,
readouts, user-input actions, and runtime status. This lets the browser show an
active-node popup while the embedded display renders only the Brewie screen.
