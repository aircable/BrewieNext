"""Authoritative Brewie workflow runtime with simulation and AVR HAL modes."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import deepcopy

from brewie_procedure_validation import (
    get_start_state,
    normalize_error_handler,
    normalize_name,
    safe_eval_condition,
)
from hardware_registry import HardwareRegistry, HardwareRegistryError


class RuntimeEngineError(RuntimeError):
    """A runtime command or procedure action could not be completed safely."""


def parse_duration(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    total = 0.0
    for part in value.strip().split():
        match = re.match(r"^(\d+(?:\.\d+)?)([smh])$", part)
        if not match:
            return None
        amount = float(match.group(1))
        total += amount * {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    return total


def resolve_value(value, namespaces):
    if not isinstance(value, str):
        return value
    for namespace in namespaces:
        if value in namespace:
            return namespace[value]
    return value


class SimulatedHAL:
    """Deterministic B20 process model driven by virtual seconds."""

    mode = "simulation"

    def __init__(self):
        self.registry = HardwareRegistry()
        self.valves = {
            device_id: False for device_id in self.registry.identifiers({"valve"})
        }
        self.pumps = {
            device_id: False for device_id in self.registry.identifiers({"pump"})
        }
        self.heaters = {
            device_id: {"targetC": None, "output": False}
            for device_id in self.registry.identifiers({"heater"})
        }
        self.sensors = {
            "tempMashC": 20.0,
            "tempBoilC": 20.0,
            "boilVolumeL": 0.0,
            "mashVolumeL": 0.0,
            "systemWeightKg": 0.0,
            "mashPumpTacho": 0.0,
            "boilPumpTacho": 0.0,
            "mashPumpDiagnostic": 0,
            "boilPumpDiagnostic": 0,
        }
        self.last_error = None
        self.operation = None

    def connected(self):
        return True

    def set_device(self, device_id, action):
        try:
            device = self.registry.resolve(device_id)
        except HardwareRegistryError as error:
            raise RuntimeEngineError(str(error)) from error
        states = self.valves if device["kind"] == "valve" else self.pumps
        if device["kind"] not in {"valve", "pump"}:
            raise RuntimeEngineError(f"{device_id} is not a valve or pump")
        current = states[device["id"]]
        if action == "toggle":
            enabled = not current
        elif action in {"open", "on", True}:
            enabled = True
        elif action in {"close", "closed", "off", False}:
            enabled = False
        else:
            raise RuntimeEngineError(f"Unsupported action {action!r} for {device_id}")
        states[device["id"]] = enabled

    def set_heater_target(self, device_id, target_c):
        try:
            device = self.registry.resolve(device_id, "heater")
            target = float(target_c)
        except (HardwareRegistryError, TypeError, ValueError) as error:
            raise RuntimeEngineError(str(error)) from error
        if target != 0 and not 5 <= target <= 110:
            raise RuntimeEngineError("Heater target must be 0 or 5..110 C")
        heater = self.heaters[device["id"]]
        heater["targetC"] = target if target >= 5 else None
        heater["output"] = target >= 5

    def close_all(self):
        self.valves = {key: False for key in self.valves}
        self.pumps = {key: False for key in self.pumps}
        for heater in self.heaters.values():
            heater.update({"targetC": None, "output": False})
        self.operation = None

    def reset_level(self):
        self.sensors["boilVolumeL"] = 0.0

    def start_operation(self, operation_id, parameters, runtime_step_id):
        if operation_id != "run_hop_stage":
            raise RuntimeEngineError(f"Unsupported simulated operation {operation_id}")
        self.operation = {"id": operation_id, **parameters, "step_id": runtime_step_id}
        for cage in range(1, 5):
            self.valves[f"hop_cage_{cage}_valve"] = cage in parameters["open_cages"]
        self.valves["boil_return_valve"] = True
        self.pumps["boil_pump"] = True
        self.set_heater_target("boil_heater", 100)

    def finish_operation(self, operation_id):
        if operation_id == "run_hop_stage":
            for cage in range(1, 5):
                self.valves[f"hop_cage_{cage}_valve"] = False
            self.valves["boil_return_valve"] = False
            self.pumps["boil_pump"] = False
            self.set_heater_target("boil_heater", 0)
        self.operation = None

    def user_input(self, procedure, key, value, globals_snapshot):
        if procedure == "prepare_lme_brew" and key == "lme_charges_loaded" and value == "loaded":
            boil_volume = globals_snapshot.get("lme_boil_tank_volume_L")
            mash_volume = globals_snapshot.get("lme_mash_tank_volume_L")
            if isinstance(boil_volume, (int, float)):
                self.sensors["boilVolumeL"] = float(boil_volume)
            if isinstance(mash_volume, (int, float)):
                self.sensors["mashVolumeL"] = float(mash_volume)
            return
        if key != "manual_fill_done" or value != "done":
            return
        target_name = (
            "mash_water_volume_L" if procedure == "fill_mash_water"
            else "sparge_water_volume_L" if procedure == "fill_sparge_water"
            else None
        )
        if target_name and isinstance(globals_snapshot.get(target_name), (int, float)):
            self.sensors["boilVolumeL"] = float(globals_snapshot[target_name])

    @staticmethod
    def _move_toward(current, target, maximum_change):
        if current < target:
            return min(target, current + maximum_change)
        return max(target, current - maximum_change)

    def tick(self, delta_s):
        if delta_s <= 0:
            return
        if self.valves.get("water_inlet_valve"):
            self.sensors["boilVolumeL"] += 0.065 * delta_s

        boil_target = self.heaters["boil_heater"]["targetC"]
        mash_target = self.heaters["mash_heater"]["targetC"]
        if boil_target is not None:
            self.sensors["tempBoilC"] = self._move_toward(
                self.sensors["tempBoilC"], boil_target, 0.09 * delta_s
            )
        if mash_target is not None:
            self.sensors["tempMashC"] = self._move_toward(
                self.sensors["tempMashC"], mash_target, 0.09 * delta_s
            )

        transfer_rate = 0.18 * delta_s
        if self.pumps.get("boil_pump"):
            source = self.sensors["boilVolumeL"]
            moved = min(source, transfer_rate)
            if self.valves.get("outlet_valve"):
                self.sensors["boilVolumeL"] -= moved
            elif self.valves.get("mash_inlet_valve") or self.valves.get("mash_return_valve"):
                previous = self.sensors["mashVolumeL"]
                self.sensors["boilVolumeL"] -= moved
                self.sensors["mashVolumeL"] += moved
                if previous <= 0.05 and moved:
                    self.sensors["tempMashC"] = self.sensors["tempBoilC"]
        if self.pumps.get("mash_pump") and self.valves.get("boil_inlet_valve"):
            moved = min(self.sensors["mashVolumeL"], transfer_rate)
            self.sensors["mashVolumeL"] -= moved
            self.sensors["boilVolumeL"] += moved

        cooling = (
            self.valves.get("wort_cooling_valve")
            and self.valves.get("cooling_water_inlet_valve")
            and self.pumps.get("boil_pump")
        )
        if cooling:
            self.sensors["tempBoilC"] = max(18.0, self.sensors["tempBoilC"] - 0.12 * delta_s)

        self.sensors["mashPumpTacho"] = 220 if self.pumps.get("mash_pump") else 0
        self.sensors["boilPumpTacho"] = 220 if self.pumps.get("boil_pump") else 0
        self.sensors["mashPumpDiagnostic"] = (
            1 if self.pumps.get("mash_pump") and self.sensors["mashVolumeL"] > 1.5
            else 2 if self.pumps.get("mash_pump") else 0
        )
        self.sensors["boilPumpDiagnostic"] = (
            1 if self.pumps.get("boil_pump") and self.sensors["boilVolumeL"] > 1.5
            else 2 if self.pumps.get("boil_pump") else 0
        )
        self.sensors["systemWeightKg"] = self.sensors["boilVolumeL"] + self.sensors["mashVolumeL"]

    def condition_sensors(self):
        sensors = self.sensors
        return {
            "temp_mash_tank": sensors["tempMashC"],
            "temp_boil_tank": sensors["tempBoilC"],
            "weight_boil_tank": sensors["boilVolumeL"],
            "weight_mash_tank": sensors["mashVolumeL"],
            "water_volume": sensors["boilVolumeL"],
            "mash_pump_tacho": sensors["mashPumpTacho"],
            "boil_pump_tacho": sensors["boilPumpTacho"],
            "mash_pump_diagnostic": sensors["mashPumpDiagnostic"],
            "boil_pump_diagnostic": sensors["boilPumpDiagnostic"],
        }

    def status(self):
        heaters = {}
        both = all(self.heaters[key]["targetC"] is not None for key in self.heaters)
        for device_id, heater in self.heaters.items():
            output = heater["output"] and (device_id == "mash_heater" or not both)
            state = "off" if heater["targetC"] is None else "on" if output else "deferred"
            heaters[device_id] = {
                "active": output,
                "targetC": heater["targetC"],
                "powerPercent": 100 if output else 0,
                "state": state,
            }
        return {
            "source": "simulation",
            "connected": True,
            "serialDevice": None,
            "lastError": self.last_error,
            "valves": dict(self.valves),
            "pumps": dict(self.pumps),
            "heaters": heaters,
            "sensors": dict(self.sensors),
        }


class AvrHAL:
    """Real hardware adapter around the acknowledged AVR serial bridge."""

    mode = "hardware"

    def __init__(self, bridge):
        self.bridge = bridge
        self.registry = bridge.registry
        self.runtime_step_id = 0
        self.active_operations = set()

    def connected(self):
        return bool(self.bridge.status().get("connected"))

    def _call(self, method, *args):
        try:
            return method(*args)
        except Exception as error:
            raise RuntimeEngineError(str(error)) from error

    def set_device(self, device_id, action):
        return self._call(self.bridge.set_device, device_id, action)

    def set_heater_target(self, device_id, target_c):
        return self._call(self.bridge.set_heater_target, device_id, target_c)

    def close_all(self):
        self.cancel_operations()
        return self._call(self.bridge.close_all)

    def prepare_session(self):
        self.cancel_operations()
        return self._call(self.bridge.prepare_hardware_session)

    def reset_level(self):
        return self._call(self.bridge.reset_level)

    def start_operation(self, operation_id, parameters, runtime_step_id):
        try:
            compiled = self.registry.compile_operation(
                operation_id, parameters, runtime_step_id=runtime_step_id
            )
        except HardwareRegistryError as error:
            raise RuntimeEngineError(str(error)) from error
        if compiled.get("before_command"):
            self._call(self.bridge.send_payload, compiled["before_command"])
        self.active_operations.add(operation_id)
        payload = " ".join([compiled["command"], *[str(value) for value in compiled["arguments"]]])
        try:
            self._call(self.bridge.send_payload, payload)
        except RuntimeEngineError:
            self.cancel_operations()
            raise

    def finish_operation(self, operation_id):
        try:
            compiled = self.registry.compile_session_finish(operation_id)
        except HardwareRegistryError as error:
            raise RuntimeEngineError(str(error)) from error
        self._call(self.bridge.send_payload, compiled["command"])
        self.active_operations.discard(operation_id)

    def cancel_operations(self):
        for operation_id in list(self.active_operations):
            try:
                compiled = self.registry.compile_session_cancel(operation_id)
            except HardwareRegistryError as error:
                raise RuntimeEngineError(str(error)) from error
            self._call(self.bridge.send_payload, compiled["command"])
            self.active_operations.discard(operation_id)

    def user_input(self, procedure, key, value, globals_snapshot):
        return None

    def tick(self, delta_s):
        return None

    def condition_sensors(self):
        status = self.bridge.status()
        sensors = status.get("sensors", {})
        return {
            "temp_mash_tank": sensors.get("tempMashC", 0.0),
            "temp_boil_tank": sensors.get("tempBoilC", 0.0),
            "weight_boil_tank": sensors.get("boilVolumeL", 0.0),
            "weight_mash_tank": sensors.get("mashVolumeL", 0.0),
            "water_volume": sensors.get("boilVolumeL", 0.0),
            "mash_pump_tacho": sensors.get("mashPumpTacho", 0.0),
            "boil_pump_tacho": sensors.get("boilPumpTacho", 0.0),
            "mash_pump_diagnostic": sensors.get("mashPumpDiagnostic", 0),
            "boil_pump_diagnostic": sensors.get("boilPumpDiagnostic", 0),
        }

    def status(self):
        return self.bridge.status()


class ProcedureExecution:
    """One transition-driven procedure instance sharing its workflow HAL."""

    def __init__(self, procedure, globals_snapshot, hal, node_id=None):
        self.data = deepcopy(procedure)
        self.name = normalize_name(procedure)
        self.node_id = node_id or self.name
        self.states = self.data.get("states", {})
        self.current_state = get_start_state(self.data)
        self.globals = globals_snapshot
        self.hal = hal
        self.variables = {}
        self.inputs = {}
        self.elapsed_s = 0.0
        self.state_elapsed_s = 0.0
        self.status = "running"
        self.error = None
        self.log = []
        self._entered = False
        self._runtime_step_id = 0

    @property
    def state(self):
        return self.states.get(self.current_state, {})

    def _namespaces(self):
        return (self.variables, self.globals, self.data.get("parameters", {}))

    def _resolve(self, value):
        return resolve_value(value, self._namespaces())

    def _input_definition(self):
        for action in self.state.get("action", []):
            if not isinstance(action, dict) or "wait_for_user_input" not in action:
                continue
            config = action["wait_for_user_input"]
            if not isinstance(config, dict):
                continue
            key = config.get("key", f"{self.current_state}_response")
            values = [value for value in config.get("expected_values", []) if isinstance(value, str)]
            return {"key": key, "options": values}
        return None

    def waiting_for_input(self):
        definition = self._input_definition()
        return bool(definition and definition["key"] not in self.inputs)

    def provide_input(self, key, value):
        definition = self._input_definition()
        if not definition or key != definition["key"]:
            raise RuntimeEngineError(f"Procedure {self.name} is not waiting for input '{key}'")
        if value not in definition["options"]:
            raise RuntimeEngineError(f"Expected one of {definition['options']}, got {value!r}")
        self.inputs[key] = value
        self.hal.user_input(self.name, key, value, self.globals)
        self.log.append(f"Input {key}={value}")

    def _execute_action(self, action):
        if not isinstance(action, dict) or not action:
            return
        kind, raw = next(iter(action.items()))
        config = raw if isinstance(raw, dict) else {}
        if kind in {"read", "read_sensor", "notify_user", "wait_for_user_input", "log", "log_event", "wait"}:
            return
        if kind == "set_valve":
            self.hal.set_device(config.get("valve"), config.get("state", "open"))
        elif kind == "set_pump":
            self.hal.set_device(config.get("device"), config.get("state", "on"))
        elif kind == "set_heater":
            device = config.get("device")
            state = config.get("state")
            target = self._resolve(config.get("target_temp"))
            if state in {"off", False}:
                self.hal.set_heater_target(device, 0)
            elif isinstance(target, (int, float)):
                self.hal.set_heater_target(device, target)
        elif kind == "enable_pid":
            target = self._resolve(config.get("target_temp"))
            if not isinstance(target, (int, float)):
                raise RuntimeEngineError(f"Cannot resolve PID target {config.get('target_temp')!r}")
            self.hal.set_heater_target(config.get("device"), target)
        elif kind == "disable_pid":
            return
        elif kind == "set_counter":
            self.variables[config.get("name")] = self._resolve(config.get("value", 0))
        elif kind == "increment_counter":
            name = config.get("name")
            self.variables[name] = self.variables.get(name, 0) + self._resolve(config.get("amount", 1))
        elif kind == "run_hop_stage":
            resolved = {
                **config,
                "start_minutes_remaining": self._resolve(config.get("start_minutes_remaining")),
                "end_minutes_remaining": self._resolve(config.get("end_minutes_remaining")),
            }
            start = resolved["start_minutes_remaining"]
            end = resolved["end_minutes_remaining"]
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start <= end:
                raise RuntimeEngineError("Invalid hop-stage duration")
            self._runtime_step_id += 1
            self.hal.start_operation("run_hop_stage", resolved, self._runtime_step_id)
            self.variables["action_duration_s"] = (start - end) * 60
            self.variables["action_complete"] = False
        elif kind == "finish_avr_step_session":
            operation = config.get("operation")
            self.hal.finish_operation(operation)
            self.variables["action_complete"] = True
        else:
            raise RuntimeEngineError(f"Unsupported runtime action '{kind}'")

    def enter(self):
        if self._entered or self.status != "running":
            return
        if self.current_state not in self.states:
            self.fail(f"State '{self.current_state}' does not exist")
            return
        self._entered = True
        self.state_elapsed_s = 0.0
        self.log.append(f"Enter {self.current_state}")
        try:
            for action in self.state.get("action", []):
                self._execute_action(action)
        except RuntimeEngineError as error:
            self.fail(str(error))

    def _timeout_exceeded(self):
        timeout = parse_duration(self.state.get("timeout_s") or self.state.get("timeout"))
        return timeout is not None and self.state_elapsed_s >= timeout

    def _condition_env(self, parallel_primary_complete=False):
        sensors = self.hal.condition_sensors()
        env = {
            **sensors,
            **self.data.get("parameters", {}),
            **self.globals,
            **self.variables,
            **self.inputs,
            "elapsed_time": self.elapsed_s,
            "timeout_after": self.state_elapsed_s,
            "timeout_exceeded": self._timeout_exceeded(),
            "parallel_primary_complete": parallel_primary_complete,
            "iteration_count": 0,
        }
        return env

    @staticmethod
    def _parse_transition(item):
        if not isinstance(item, dict) or not item:
            return None, None, False
        if "default" in item:
            return None, item["default"], True
        if "condition" in item and "then" in item:
            return item["condition"], item["then"], False
        if "if" in item and "then" in item:
            return item["if"], item["then"], False
        if len(item) == 1:
            condition, target = next(iter(item.items()))
            return (None, target, True) if condition == "default" else (condition, target, False)
        return None, None, False

    def _evaluate_transition(self, parallel_primary_complete=False):
        transitions = self.state.get("transition", self.state.get("transitions", []))
        if not isinstance(transitions, list):
            return None
        default = None
        env = self._condition_env(parallel_primary_complete)
        def duration_reached(duration):
            seconds = parse_duration(duration)
            return seconds is not None and self.state_elapsed_s >= seconds

        functions = {"duration_reached": duration_reached}
        for item in transitions:
            condition, target, is_default = self._parse_transition(item)
            if is_default:
                default = target
            elif condition is not None and safe_eval_condition(condition, env, functions):
                return target
        return default

    def _run_on_exit(self):
        for action in self.state.get("on_exit", []):
            self._execute_action(action)

    def _transition(self, target):
        if target in {None, "loops"}:
            return False
        try:
            self._run_on_exit()
        except RuntimeEngineError as error:
            self.fail(str(error))
            return False
        if target in {"next_phase", "complete", "done", "exit"}:
            self.status = "complete"
            return True
        if target == "error_handler" or (isinstance(target, str) and target.startswith("error-")):
            handler = normalize_error_handler(self.data)
            if handler in self.states and handler != self.current_state:
                target = handler
            else:
                self.fail(f"External error handler '{handler or target}'")
                return True
        if target not in self.states:
            self.fail(f"Transition target '{target}' does not exist")
            return True
        self.current_state = target
        self._entered = False
        self.enter()
        return True

    def tick(self, delta_s, parallel_primary_complete=False):
        if self.status != "running":
            return
        self.enter()
        if self.status != "running":
            return
        self.elapsed_s += max(0.0, delta_s)
        self.state_elapsed_s += max(0.0, delta_s)
        duration = self.variables.get("action_duration_s")
        if isinstance(duration, (int, float)):
            self.variables["action_complete"] = self.state_elapsed_s >= duration
        for _ in range(20):
            target = self._evaluate_transition(parallel_primary_complete)
            if not self._transition(target):
                break
            if self.status != "running":
                break

    def abort(self):
        if self.status == "running" and self._entered:
            try:
                self._run_on_exit()
            except RuntimeEngineError:
                pass
        self.status = "aborted"

    def fail(self, message):
        self.error = message
        self.status = "error"
        self.log.append(f"ERROR: {message}")

    def screen(self):
        notify = None
        for action in self.state.get("action", []):
            if isinstance(action, dict) and "notify_user" in action:
                notify = action["notify_user"]
                break
        notify = notify if isinstance(notify, dict) else {}
        input_definition = self._input_definition()
        choices = []
        if input_definition and input_definition["key"] not in self.inputs:
            choices = [
                {"value": value, "label": value.replace("_", " ").upper()}
                for value in input_definition["options"]
            ]
        return {
            "title": self.name.replace("_", " ").title(),
            "message": notify.get("text", self.state.get("description", self.current_state)),
            "footer_message": notify.get("footer_text", ""),
            "readout_definitions": deepcopy(notify.get("readouts", [])),
            "choices": choices,
            "progress": self.progress(),
        }

    def progress(self):
        timeout = parse_duration(self.state.get("timeout_s"))
        for item in self.state.get("transition", []):
            condition, _, _ = self._parse_transition(item)
            if not isinstance(condition, str):
                continue
            match = re.fullmatch(r"duration_reached\((.+)\)", condition.strip())
            if match:
                expression = match.group(1)
                holder = {"value": None}
                safe_eval_condition(
                    f"capture({expression})",
                    {**self.globals, **self.variables},
                    {"capture": lambda value: holder.update(value=float(value)) or True},
                )
                timeout = holder["value"]
                break
        if not timeout:
            return None
        return max(0.0, min(100.0, self.state_elapsed_s / timeout * 100))

    def snapshot(self):
        definition = self._input_definition()
        return {
            "node_id": self.node_id,
            "procedure": self.name,
            "state": self.current_state,
            "status": "waiting_for_input" if self.waiting_for_input() else self.status,
            "elapsed_s": self.elapsed_s,
            "state_elapsed_s": self.state_elapsed_s,
            "input": definition if self.waiting_for_input() else None,
            "variables": deepcopy(self.variables),
            "error": self.error,
            "screen": self.screen(),
            "log": list(self.log[-30:]),
        }


class WorkflowSession:
    """Runs an ordered workflow using the same procedure engine in both modes."""

    TERMINAL = {"complete", "error", "aborted"}

    def __init__(self, workflow, procedures, globals_snapshot, hal, speed=60, autostart=True):
        self.id = uuid.uuid4().hex
        self.workflow = deepcopy(workflow)
        self.procedures = procedures
        self.globals = deepcopy(globals_snapshot)
        self.hal = hal
        self.mode = hal.mode
        self.speed = float(speed if hal.mode == "simulation" else 1)
        self.status = "running"
        self.step_index = 0
        self.elapsed_s = 0.0
        self.completed_nodes = []
        self.error = None
        self.active = []
        self.log = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        if self.mode == "hardware":
            if not self.hal.connected():
                raise RuntimeEngineError("AVR hardware is not connected")
            self.hal.prepare_session()
            if not self.hal.connected():
                raise RuntimeEngineError("AVR hardware preparation did not complete")
        self._start_step()
        self.tick(0)
        if autostart:
            self._thread = threading.Thread(target=self._run_loop, name="brewie-runtime", daemon=True)
            self._thread.start()

    @property
    def steps(self):
        return self.workflow.get("steps", [])

    def _procedure(self, name):
        try:
            return self.procedures[name]
        except KeyError as error:
            raise RuntimeEngineError(f"Procedure '{name}' is not loaded") from error

    def _start_step(self):
        if self.step_index >= len(self.steps):
            self.status = "complete"
            self.hal.close_all()
            return
        step = self.steps[self.step_index]
        self.active = []
        if "parallel" in step:
            parallel = step["parallel"]
            for branch in parallel.get("branches", []):
                execution = ProcedureExecution(
                    self._procedure(branch["procedure"]), self.globals, self.hal, branch["id"]
                )
                self.active.append({"node": branch, "execution": execution})
        else:
            execution = ProcedureExecution(
                self._procedure(step["procedure"]), self.globals, self.hal, step["id"]
            )
            self.active.append({"node": step, "execution": execution})
        for item in self.active:
            item["execution"].enter()

    def _run_loop(self):
        last = time.monotonic()
        while not self._stop.wait(0.1):
            now = time.monotonic()
            real_delta = now - last
            last = now
            with self._lock:
                if self.status in self.TERMINAL:
                    return
                if self.status == "paused":
                    continue
                waiting = any(item["execution"].waiting_for_input() for item in self.active)
                delta = real_delta if self.mode == "hardware" else 0 if waiting else real_delta * self.speed
                self.tick(delta)

    def _advance(self):
        self.step_index += 1
        self._start_step()

    def tick(self, delta_s):
        with self._lock:
            if self.status in self.TERMINAL or self.status == "paused":
                return
            self.hal.tick(delta_s)
            self.elapsed_s += max(0.0, delta_s)
            step = self.steps[self.step_index] if self.step_index < len(self.steps) else None
            try:
                if step and "parallel" in step:
                    parallel = step["parallel"]
                    primary_id = parallel.get("primary")
                    primary = next(item for item in self.active if item["node"]["id"] == primary_id)
                    primary["execution"].tick(delta_s)
                    primary_complete = primary["execution"].status == "complete"
                    for item in self.active:
                        if item is primary:
                            continue
                        item["execution"].tick(delta_s, parallel_primary_complete=primary_complete)
                    if any(item["execution"].status == "error" for item in self.active):
                        failed = next(item["execution"] for item in self.active if item["execution"].status == "error")
                        self._fail(failed.error or f"{failed.name} failed")
                        return
                    if primary_complete:
                        required = parallel.get("join", {}).get("require", {})
                        missing = [
                            node_id for node_id, expected in required.items()
                            if expected == "completed" and not any(
                                item["node"]["id"] == node_id and item["execution"].status == "complete"
                                for item in self.active
                            )
                        ]
                        if missing:
                            self._fail(f"Parallel deadline reached before {', '.join(missing)} completed")
                            return
                        self.completed_nodes.extend(item["node"]["id"] for item in self.active)
                        self._advance()
                else:
                    execution = self.active[0]["execution"]
                    execution.tick(delta_s)
                    if execution.status == "error":
                        self._fail(execution.error or f"{execution.name} failed")
                    elif execution.status == "complete":
                        self.completed_nodes.append(self.active[0]["node"]["id"])
                        self._advance()
            except (RuntimeEngineError, StopIteration) as error:
                self._fail(str(error))
            if self.status not in self.TERMINAL:
                self.status = "waiting_for_input" if any(
                    item["execution"].waiting_for_input() for item in self.active
                ) else "running"

    def _fail(self, message):
        self.error = message
        self.status = "error"
        try:
            self.hal.close_all()
        except RuntimeEngineError as close_error:
            self.error += f"; safe shutdown failed: {close_error}"

    def pause(self):
        with self._lock:
            if self.status not in self.TERMINAL:
                self.status = "paused"

    def resume(self):
        with self._lock:
            if self.status == "paused":
                self.status = "running"
                self.tick(0)

    def provide_input(self, key, value, procedure=None):
        with self._lock:
            candidates = [
                item["execution"] for item in self.active
                if item["execution"].waiting_for_input()
                and (procedure is None or item["execution"].name == procedure)
            ]
            if not candidates:
                raise RuntimeEngineError("No active procedure is waiting for input")
            execution = candidates[0]
            execution.provide_input(key, value)
            self.status = "running"
            self.tick(0)

    def navigate(self, direction):
        with self._lock:
            target = self.step_index + (1 if direction == "next" else -1)
            if not 0 <= target < len(self.steps):
                raise RuntimeEngineError(f"Cannot navigate {direction} from this procedure")
            for item in self.active:
                item["execution"].abort()
            self.step_index = target
            self.status = "running"
            self._start_step()
            self.tick(0)

    def abort(self):
        with self._lock:
            for item in self.active:
                item["execution"].abort()
            try:
                self.hal.close_all()
            finally:
                self.status = "aborted"
                self._stop.set()

    def set_speed(self, speed):
        if self.mode != "simulation":
            raise RuntimeEngineError("Hardware runtime speed is fixed at 1x")
        speed = float(speed)
        if speed not in {1, 10, 30, 60, 120}:
            raise RuntimeEngineError("Simulation speed must be 1, 10, 30, 60, or 120")
        self.speed = speed

    def _resolved_readouts(self, execution, definitions):
        sensors = self.hal.condition_sensors()
        result = []
        for definition in definitions if isinstance(definitions, list) else []:
            if not isinstance(definition, dict):
                continue
            raw = sensors.get(definition.get("sensor")) if definition.get("sensor") else self.globals.get(definition.get("global"))
            value = "—" if raw is None else f"{raw:.1f}" if isinstance(raw, float) else str(raw)
            result.append({
                "label": definition.get("label", "Value"),
                "value": value,
                **({"unit": definition["unit"]} if definition.get("unit") else {}),
            })
        return result

    def snapshot(self):
        with self._lock:
            procedures = [item["execution"].snapshot() for item in self.active]
            primary = procedures[0] if procedures else None
            screen = primary["screen"] if primary else {
                "title": "Brew complete", "message": "Brewing workflow completed.",
                "footer_message": "", "readout_definitions": [], "choices": [], "progress": 100,
            }
            if primary:
                screen["readouts"] = self._resolved_readouts(
                    self.active[0]["execution"], screen.pop("readout_definitions", [])
                )
            else:
                screen["readouts"] = []
                screen.pop("readout_definitions", None)
            active_nodes = [item["node"]["id"] for item in self.active]
            if self.status == "error":
                screen.update({
                    "title": "Brew stopped",
                    "message": self.error or (primary or {}).get("error") or "The procedure failed.",
                    "footer_message": "All outputs were closed. Review the error before continuing.",
                    "choices": [],
                })
            return {
                "id": self.id,
                "mode": self.mode,
                "status": self.status,
                "speed": self.speed,
                "elapsed_s": self.elapsed_s,
                "workflow": self.workflow.get("name", "beer_brewing"),
                "step_index": self.step_index,
                "step_count": len(self.steps),
                "active_nodes": active_nodes,
                "completed_nodes": list(dict.fromkeys(self.completed_nodes)),
                "active_procedure": primary["procedure"] if primary else None,
                "active_state": primary["state"] if primary else None,
                "procedures": procedures,
                "screen": {
                    **screen,
                    "status": self.status,
                    "allowed_controls": ["pause", "abort"] if self.status not in self.TERMINAL else ["reset"],
                },
                "machine": self.hal.status(),
                "error": self.error,
            }


class RuntimeManager:
    """Own the single machine-wide workflow session."""

    def __init__(self, avr_bridge, state_file=None):
        self.avr_bridge = avr_bridge
        self.session = None
        self.state_file = state_file
        self.interrupted = None
        self.revision = 0
        self._fingerprint = None
        self._commands = {}
        self._pending_control = None
        self._browser_lease = None
        self._lock = threading.RLock()
        self._load_interrupted()

    def _load_interrupted(self):
        if not self.state_file:
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as source:
                saved = json.load(source)
            snapshot = saved.get("snapshot", {})
            if saved.get("mode") == "hardware" and snapshot.get("status") not in WorkflowSession.TERMINAL:
                self.interrupted = saved
                self.revision = int(snapshot.get("revision", 0)) + 1
        except (OSError, ValueError, TypeError):
            self.interrupted = None

    def _write_state(self):
        if not self.state_file:
            return
        if self.interrupted and not self.session:
            return
        if not self.session or self.session.mode != "hardware" or self.session.status in WorkflowSession.TERMINAL:
            try:
                os.unlink(self.state_file)
            except FileNotFoundError:
                pass
            return
        payload = {
            "version": 1,
            "mode": self.session.mode,
            "snapshot": {**self.session.snapshot(), "revision": self.revision},
            "workflow": self.session.workflow,
            "procedures": self.session.procedures,
            "globals": self.session.globals,
        }
        directory = os.path.dirname(self.state_file)
        os.makedirs(directory, exist_ok=True)
        temporary = self.state_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as target:
            json.dump(payload, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.state_file)

    @staticmethod
    def _session_fingerprint(snapshot):
        return (
            snapshot.get("id"), snapshot.get("status"), snapshot.get("step_index"),
            snapshot.get("active_state"), snapshot.get("error"),
        )

    def _observe(self):
        if not self.session:
            return
        snapshot = self.session.snapshot()
        fingerprint = self._session_fingerprint(snapshot)
        if fingerprint != self._fingerprint:
            self.revision += 1
            self._fingerprint = fingerprint
            self._write_state()

    def _control_state(self, client_id, client_kind, is_loopback):
        now = time.time()
        if self._browser_lease and self._browser_lease["expires_at"] <= now:
            self._browser_lease = None
        kiosk = client_kind == "kiosk" and is_loopback
        browser = bool(
            self._browser_lease and self._browser_lease["client_id"] == client_id
        )
        return {
            "can_control": kiosk or browser,
            "controller": "touchscreen" if kiosk else "browser" if browser else None,
            "lease_expires_at": self._browser_lease["expires_at"] if browser else None,
            "pending_request": deepcopy(self._pending_control) if kiosk else None,
        }

    def snapshot(self, client_id="", client_kind="browser", is_loopback=False):
        with self._lock:
            self._observe()
            control = self._control_state(client_id, client_kind, is_loopback)
            if self.session:
                return {**self.session.snapshot(), "revision": self.revision, "control": control}
            if self.interrupted:
                old = deepcopy(self.interrupted.get("snapshot", {}))
                old.update({
                    "status": "interrupted", "revision": self.revision, "control": control,
                    "error": "Power or backend restart interrupted this hardware session.",
                    "screen": {
                        "title": "Brew interrupted",
                        "message": "The previous hardware brew did not shut down normally.",
                        "footer_message": "Use the touchscreen to discard it or restart its current procedure.",
                        "readouts": old.get("screen", {}).get("readouts", []),
                        "choices": [], "progress": old.get("screen", {}).get("progress"),
                        "status": "interrupted", "allowed_controls": ["recover", "discard"],
                    },
                })
                return old
            return {
                "id": None, "mode": None, "status": "idle", "revision": self.revision,
                "speed": 1, "elapsed_s": 0, "workflow": None, "step_index": 0,
                "step_count": 0, "active_nodes": [], "completed_nodes": [],
                "active_procedure": None, "active_state": None, "procedures": [],
                "screen": {"title": "Select a program", "message": "Choose a program to begin.",
                           "footer_message": "", "readouts": [], "choices": [], "progress": None,
                           "status": "idle", "allowed_controls": []},
                "machine": self.avr_bridge.status(), "error": None, "control": control,
            }

    def request_control(self, client_id, label="Remote browser"):
        if not client_id:
            raise RuntimeEngineError("A browser client ID is required")
        with self._lock:
            self._pending_control = {
                "id": uuid.uuid4().hex, "client_id": client_id,
                "label": str(label)[:80], "requested_at": time.time(),
            }
            self.revision += 1
            return deepcopy(self._pending_control)

    def permit_control(self, request_id, allow, duration_s=900):
        with self._lock:
            pending = self._pending_control
            if not pending or pending["id"] != request_id:
                raise RuntimeEngineError("That browser control request is no longer pending")
            if allow:
                duration_s = max(60, min(3600, int(duration_s)))
                self._browser_lease = {
                    "client_id": pending["client_id"], "expires_at": time.time() + duration_s,
                }
            self._pending_control = None
            self.revision += 1

    def authorize(self, client_id, client_kind, is_loopback):
        if not self._control_state(client_id, client_kind, is_loopback)["can_control"]:
            raise RuntimeEngineError("Remote browser is view-only; request control on the touchscreen")

    def command(self, client_id, command_id, expected_revision, callback):
        with self._lock:
            key = (client_id, command_id)
            if command_id and key in self._commands:
                return self._commands[key]
            self._observe()
            if expected_revision is not None:
                try:
                    matches = int(expected_revision) == self.revision
                except (TypeError, ValueError):
                    matches = False
                if not matches:
                    raise RuntimeEngineError("Session changed; refresh before sending that command")
            result = callback()
            self.revision += 1
            self._fingerprint = self._session_fingerprint(self.session.snapshot()) if self.session else None
            self._write_state()
            if command_id:
                self._commands[key] = result
                if len(self._commands) > 128:
                    self._commands.pop(next(iter(self._commands)))
            return result

    def start(self, workflow, procedures, globals_snapshot, mode="simulation", speed=60):
        with self._lock:
            if self.session and self.session.status not in WorkflowSession.TERMINAL:
                raise RuntimeEngineError("A Brewie runtime session is already active")
            hal = SimulatedHAL() if mode == "simulation" else AvrHAL(self.avr_bridge) if mode == "hardware" else None
            if hal is None:
                raise RuntimeEngineError("Runtime mode must be simulation or hardware")
            self.session = WorkflowSession(workflow, procedures, globals_snapshot, hal, speed=speed)
            self.interrupted = None
            self._fingerprint = None
            return self.session

    def recover(self, action):
        with self._lock:
            if not self.interrupted:
                raise RuntimeEngineError("No interrupted hardware session exists")
            saved = self.interrupted
            if action == "discard":
                self.interrupted = None
                self._write_state()
                return None
            if action != "restart_current_procedure":
                raise RuntimeEngineError("Recovery action must be discard or restart_current_procedure")
            step_index = int(saved.get("snapshot", {}).get("step_index", 0))
            workflow = deepcopy(saved["workflow"])
            if step_index:
                workflow["steps"] = workflow.get("steps", [])[step_index:]
            self.interrupted = None
            return self.start(workflow, saved["procedures"], saved["globals"], mode="hardware", speed=1)

    def require(self):
        if not self.session:
            raise RuntimeEngineError("No Brewie runtime session exists")
        return self.session
