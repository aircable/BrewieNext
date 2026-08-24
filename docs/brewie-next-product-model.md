# BrewieNext Product Model

## Purpose

BrewieNext has one brewing model and two presentations. The full browser is
the design and supervision surface; the Brewie touchscreen is the compact
execution surface. Neither surface executes hardware actions directly.

## Browser experience

The browser opens at the highest level with the `beer_brewing` orchestration
graph. Each node represents a procedure and may expose parameters and named
conditional alternatives. Selecting a node opens its subprocedure state graph.
Selecting a state opens a structured inspector for its description, actions,
timeouts, cleanup, and transitions.

During execution, the active orchestration node is highlighted and a popup
renders the same `BrewieScreen` model shown on the physical display.

## Touchscreen experience

The touchscreen never attempts to show the full graph editor. It provides:

- procedure selection and start/resume navigation;
- named alternatives and parameter choices;
- the active BrewieScreen;
- continue/input, pause/resume, and abort controls.

The touchscreen and browser receive the same runtime state. Layout, not
business meaning, differs between the two surfaces.

## Runtime boundary

The frontend sends user intent to a validated API. The procedure engine owns
state transitions and actuation. The safety supervisor can override it. The
frontend is never authoritative for hardware state.

