# Development Platform Brew and UX Test Plan

This is a living acceptance plan for BrewieNext on a Brewie development
machine. Run one scenario at a time, record the observation, and fix confirmed
UX or execution problems before moving to the next risk level.

## Test roles and URLs

- **T** — physical Brewie touchscreen, the permanent local controller.
- **B1** — normal desktop browser at
  `http://brewienext.local:8080/current/?view=runtime`.
- **B2** — a private/incognito browser window. It must receive a different
  client identity and remain view-only when B1 is permitted.
- **API** — read-only diagnostics at
  `http://brewienext.local:8081/api/health` and
  `http://brewienext.local:8081/api/runtime/session`.

The expected screen convergence time is one second or less. Polling currently
runs every 500 ms.

## Safety boundary for this platform

The platform has no usable water inlet or heater. Treat an unfilled pump as
unsafe unless it is physically disconnected or the brewer explicitly confirms
that brief dry operation cannot damage it.

- Always begin and end a hardware scenario with **Close all** (`P999`).
- Keep a hand on power during every first actuator transition.
- Do not leave a heat, transfer, mash, sparge, hop, cooling, or fermenter
  procedure running merely to inspect its screen.
- **Next procedure** runs `on_exit` cleanup, but then immediately executes the
  next procedure's entry actions. It is not an actuator-inhibit mode.
- Use authoritative simulation for the complete brew walkthrough. Use hardware
  mode separately for approved valves and session/lock/recovery behavior.

If a full hardware-shaped dry run is still desirable after these scenarios,
add a commissioning profile with an explicit actuator allowlist and simulated
sensor overrides. Do not hide this behavior behind ordinary simulation or
silently ignore hardware actions.

## Current platform baseline

Verified after the ReLinux flash on 2026-09-11:

- Linux 6.6.156 and Buildroot 2026.02 boot normally.
- Root filesystem is 13.9 GiB with approximately 13.5 GiB free.
- `MemAvailable` is approximately 423 MiB after the UI starts.
- CMA is 16 MiB, with approximately 15.6 MiB free.
- `kernel.panic_on_oops=1` and `kernel.panic=10` are active.
- pstore is mounted and the boot-time archiver has captured a ramoops console.
- `/var/lib/brewie/diagnostics/memory.log` is receiving five-minute samples.
- BrewieNext 0.6.9 and procedure bundle 0.2.0 are installed.
- Static server, backend, memory monitor, and Qt kiosk are running.
- No new kernel, MMC, ext4, or OOM errors were found.

Open item: `/etc/brewie/machine.json` is missing. The backend therefore performs
the safe AVR reset but intentionally refuses hardware initialization. Restore
this machine's own calibration before Scenario 3. The four required values are
`toLiter`, `toLiterNull`, `mashTemperatureDelta`, and
`boilTemperatureDelta`.

## Scenario 0 — preflight and calibration

1. Restore `/etc/brewie/machine.json` and restart `S85brewie-backend`.
2. Confirm `/api/health` reports `connected`, `initializationConfigured`, and
   `initializationComplete` as true with no AVR error.
3. On T, open **Machine status**, issue **Close all**, and confirm the physical
   valves reach their safe positions.
4. Identify which valves may move and whether both pumps and both heater
   outputs are physically disconnected or otherwise safe.
5. Open B1 and B2. Confirm T shows **BrewieNext ready** with access to Programs
   and Machine status, and that merely opening either browser creates no
   runtime session.

Acceptance: the machine is safe, calibration is machine-specific, and all
clients show the same idle machine state without taking ownership.

## Scenario 1 — program catalog and editing

1. From **BrewieNext ready**, open Programs on T and compare its catalog with
   the browser catalog preview.
2. Confirm these programs appear in the right categories:
   **All-grain brewing**, **LME brewing**, and **Short cleaning**.
3. Open each program in B1. All three must open their workflow editor.
   On T, selecting All-grain brewing must return to a ready screen that names
   the selected program and offers START and Machine status.
4. Confirm LME and Short cleaning are visibly marked as design programs and
   cannot be started.
5. Open an all-grain subprocedure and edit, save, reload, and restore a selected
   workflow node label and description.
6. Check the 480×272 local preview after each navigation and judge label
   wrapping, touch target size, and whether the route back to Programs is clear.

Acceptance: catalog selection has immediate feedback; draft status is clear;
editing is possible without implying a draft program is executable.

## Scenario 2 — complete all-grain simulation and shared display

Use the `development_test` recipe and 120× speed. T and both browsers should
attach to the one backend session; opening or refreshing a client must never
restart it.

1. Start simulation from B1 and record the session ID and elapsed time.
2. Refresh B1, then open B2. Confirm the same ID, state, elapsed time, active
   nodes, and completed nodes remain visible.
3. Alternate user choices between B1 and T. A choice made on either surface
   must disappear from all surfaces after one poll interval and must execute
   only once.
4. Pause on T and resume on B1. Verify timers and graph progress stop and resume
   consistently.
5. At the parallel mash/sparge-heat step, confirm both active nodes are green,
   both procedure statuses are understandable, and the primary/join behavior
   is visible enough for a brewer.
6. Complete every workflow step below.

| Step | Interaction and UX checks |
|---|---|
| Prepare brew | Review recipe, grain loading, all four hop cages, and final readiness prompts. Check that quantities and vessel instructions are readable. |
| Fill mash water | Select manual fill, verify the 15 L target and live weight display, then continue using the modeled sensor. |
| Heat mash water | Observe target/current temperature and device status; no unexplained idle period. |
| Transfer to mash | Observe transfer, settling, and remaining-volume feedback. |
| Fill sparge water | Select manual fill and verify the separate 10 L target. |
| Mash + heat sparge water | Verify two simultaneous green nodes, mash rest names/timers, sparge target, and a comprehensible join. |
| Sparging | Verify cycle number, transfer direction, tank readings, and five-cycle progress. |
| Boiling | Verify heat-to-boil versus initial unhopped-boil phases and time remaining. |
| Hopping | Verify cages 1–4 release in recipe order and the AVR-session concept is translated into brewer language. |
| Cooling | Verify target/current temperature, cooling-path status, and progress. |
| Sedimentation | Verify the deliberate no-motion wait and remaining time. |
| Transfer to fermenter | Verify hose confirmation, cage-emptying instruction, transfer state, and safe completion. |

7. At completion, confirm all clients show 100%, the same completed graph, and
   a useful next action rather than an ambiguous **Reset**.
8. Return to Programs and confirm this does not silently erase or replace the
   completed session.

Acceptance: one run, one clock, one set of inputs, and one final result across
all surfaces. No screen should require knowledge of YAML or AVR terminology.

## Scenario 3 — hardware session ownership and remote permission

Keep the run in **Prepare brew** until remote ownership is proven; this avoids
process actuator entry actions beyond the initial safe reset and calibration.

1. Start **All-grain brewing** in hardware mode from T and accept the in-app
   rotated confirmation dialog.
2. Confirm B1 and B2 automatically switch to the same live session and graph.
3. Attempt an input or pause from B1. It must remain view-only and explain how
   to request control.
4. Request control from B1. T must show one clearly identified request without
   losing the current brewing instruction.
5. Deny the first request. B1 remains view-only and can make a new request.
6. Request again and allow it on T. Verify the dialog orientation, touch hit
   location, confirmation feedback, and the 15-minute scope.
7. Submit the next preparation choice from B1. T and B2 must update, but B2 must
   remain view-only.
8. Try simultaneous commands from T and B1. One result may win, but the other
   must receive a clear stale-state message and must not execute twice.
9. Refresh B1. Its client identity and lease should survive. Open a fresh
   private B2 window and confirm the lease does not transfer.
10. Abort from B1, confirm on B1, and physically verify `P999` cleanup. T must
    show the same terminal result.

Acceptance: T always retains local authority; permission is client-specific;
remote commands are idempotent; remote monitoring never creates a second brew.

## Scenario 4 — procedure navigation and safe side entry

Run this only for entry actions approved in Scenario 0.

1. Start hardware mode and use **Next procedure** once.
2. Verify the old procedure receives its exit cleanup before the new procedure
   becomes active.
3. Verify graph highlighting, title, instruction, readouts, and machine status
   change together on T, B1, and B2.
4. Use **Previous procedure** and confirm that it restarts that procedure from
   its first state rather than pretending to reconstruct a mid-state.
5. Navigate to the parallel mash/heat step only if its pumps and heater outputs
   are physically safe. Verify both branches start and skipping away cleans up
   both branches.
6. Abort and issue **Close all**.

Acceptance: side entry is explicit procedure-level restart with visible safety
cleanup. If the immediate next-entry actuation feels unsafe or surprising,
design a two-stage **Preview destination → Start procedure** interaction before
using navigation as recovery tooling.

## Scenario 5 — backend restart and power-loss recovery

1. Start a hardware session and stop at a user prompt in a safe procedure.
2. Restart the backend. It must send `P999`; no actuator may resume
   automatically.
3. Confirm T, B1, and B2 show **Brew interrupted**, the saved procedure, and the
   same last useful readouts.
4. Confirm B1/B2 cannot recover without a newly approved control lease.
5. On T choose **Restart procedure**. Confirm the procedure starts at its first
   state, not the interrupted state, and calibration is reapplied.
6. Repeat and choose **Discard**. Confirm the checkpoint is removed and the
   Programs screen returns.
7. After backend-restart recovery passes, repeat once with a real machine power
   cycle while the process is in a safe user-input state.

Acceptance: restoration never implies that physical state was recovered; the
machine becomes safe first and clearly explains the restart boundary.

## Scenario 6 — custom dialogs, touch mapping, and screen endurance

1. Exercise start, abort, replace-session, remote-control approval, recovery,
   and discard dialogs.
2. Touch the center and four corners of each button. The green marker and
   activated control must agree.
3. Cancel every dialog once and verify no command was sent.
4. Leave the session visible on all clients for at least 30 minutes. Confirm
   memory diagnostics continue, kiosk RSS does not grow without bound, and no
   new kernel/pstore crash appears.

Acceptance: every safety confirmation is portrait-oriented inside the app,
fully touchable, cancellable, and unambiguous.

## Scenario 7 — supervised valve-only hardware smoke test

Use **Machine status**, not workflow navigation, so each action is deliberate.

1. Begin with **Close all** and photograph or note each valve position.
2. Toggle one approved valve at a time, confirm physical direction and UI
   state, then return it to safe state.
3. Compare the semantic UI name with the physical flow path. Record any name a
   brewer would misunderstand.
4. Do not toggle a pump without liquid or explicit dry-run approval. Do not set
   a heater target on this platform.
5. Finish with **Close all** and confirm every valve.

Acceptance: physical movement, UI state, semantic name, and safe reset agree.

## Observation record

For every issue, record:

| Field | Value |
|---|---|
| Scenario / step | |
| Acting surface | T / B1 / B2 |
| Expected | |
| Observed | |
| Physical actuator result | None / exact movement |
| Confusing wording or extra taps | |
| Screenshot or API state | |
| Severity | Stop test / major UX / minor UX |
| Proposed change | |
| Retest result | |

Stop-test issues are fixed before continuing. Major UX issues are fixed before
the next complete brew. Minor UX issues may be grouped, but every change is
retested on both the browser and physical 480×272 screen.

## Recommended execution order

1. Scenario 0 preflight.
2. Scenario 1 catalog/editor check.
3. Scenario 2 complete simulation.
4. Scenario 3 shared hardware session and permit flow.
5. Scenario 5 recovery.
6. Scenario 4 navigation only after actuator entry actions are approved.
7. Scenarios 6 and 7.

This order gives us the complete brewer experience first, then increases
hardware risk only after the shared-session interaction has proven reliable.
