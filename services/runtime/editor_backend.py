#!/usr/bin/env python3
"""
Brewie Procedure Editor Backend
Manages YAML procedure files: list, view, edit, validate, and track execution state.

This backend serves as the local API for BrewieNext.
It provides:
  - REST API for listing, viewing, creating, updating, and deleting procedure YAML files
  - JSON Schema validation (including custom semantic checks)
  - Simulated execution of state-machine procedures
  - Static file serving for the frontend
  - Real-time execution state via Server-Sent Events (SSE)

Paths are resolved relative to this file's location so the entire
runtime service directory is self-contained.
"""
import ipaddress
import json
import os
import re
import yaml
import threading
import time
from pathlib import Path
from datetime import datetime
from copy import deepcopy
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from jsonschema import Draft7Validator

# ─── Shared Validation Module ─────────────────────────────────────────
# Import all validation logic from the single shared module to prevent
# divergent validation rules between the CLI validator and this backend.
from brewie_procedure_validation import (
    VALID_ACTION_KEYS,
    KNOWN_SENSORS,
    KNOWN_DEVICES,
    RESERVED_TRANSITION_TARGETS,
    ValidationIssue,
    normalize_name,
    normalize_error_handler,
    get_start_state,
    classify_procedure,
    custom_checks,
    validate_procedure,
    normalize_sensor_name,
    safe_eval_condition,
)
from avr_serial import AvrSerialBridge, AvrSerialError, load_calibration
from runtime_engine import RuntimeEngineError, RuntimeManager, WorkflowSession

# ─── Configuration ─────────────────────────────────────────────────────────
# Resolve the monorepo root while retaining compatibility with a packaged backend.
BACKEND_DIR = Path(__file__).resolve().parent
_default_root = BACKEND_DIR.parent
if _default_root.name == "services":
    _default_root = _default_root.parent
BREWIE_ROOT = Path(os.environ.get("BREWIE_APP_ROOT", str(_default_root)))

PROCEDURES_DIR = os.environ.get("PROCEDURES_DIR", str(BREWIE_ROOT))
SCHEMA_PATH    = os.environ.get("SCHEMA_PATH",    str(BACKEND_DIR / "procedure.schema.json"))
GRAPH_SCHEMA_PATH = os.environ.get("GRAPH_SCHEMA_PATH", str(BACKEND_DIR / "procedure-graph.schema.json"))
RECIPE_SCHEMA_PATH = os.environ.get("RECIPE_SCHEMA_PATH", str(BACKEND_DIR / "recipe.schema.json"))
BUNDLED_RECIPES_DIR = os.environ.get("BUNDLED_RECIPES_DIR", str(BREWIE_ROOT / "recipes"))
RECIPES_DIR = os.environ.get("RECIPES_DIR", BUNDLED_RECIPES_DIR)
UI_STATE_FILE  = os.environ.get("UI_STATE_FILE", "/tmp/brewie-editor/ui_state.json")
AVR_DEVICE = os.environ.get("BREWIE_AVR_DEVICE", "/dev/ttyS1")
AVR_STATE_FILE = os.environ.get("BREWIE_AVR_STATE_FILE", "/var/lib/brewie/avr_state.json")
AVR_SAFE_START = os.environ.get("BREWIE_AVR_SAFE_START", "1").lower() in {"1", "true", "yes"}
AVR_CALIBRATION_FILE = os.environ.get("BREWIE_AVR_CALIBRATION_FILE", "/etc/brewie/machine.json")
if not os.path.isfile(AVR_CALIBRATION_FILE) and "BREWIE_AVR_CALIBRATION_FILE" not in os.environ:
    legacy_calibration = "/usr/share/brewie/config.json"
    if os.path.isfile(legacy_calibration):
        AVR_CALIBRATION_FILE = legacy_calibration
AVR_CALIBRATION = load_calibration(AVR_CALIBRATION_FILE)
_avr_enabled_setting = os.environ.get("BREWIE_AVR_ENABLED", "auto").lower()
AVR_ENABLED = _avr_enabled_setting in {"1", "true", "yes"} or (
    _avr_enabled_setting == "auto" and os.path.exists(AVR_DEVICE)
)
_default_frontend = BREWIE_ROOT / "apps/web/dist"
if not _default_frontend.is_dir():
    _default_frontend = BREWIE_ROOT
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", str(_default_frontend)))

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="/editor")
app.json.sort_keys = False  # State mapping order is meaningful to the editor.
CORS(app)

AVR_BRIDGE = AvrSerialBridge(
    device=AVR_DEVICE,
    state_file=AVR_STATE_FILE,
    enabled=AVR_ENABLED,
    safe_start=AVR_SAFE_START,
    calibration=AVR_CALIBRATION,
)
RUNTIME_MANAGER = RuntimeManager(AVR_BRIDGE)

# ─── Schema Loading ────────────────────────────────────────────────────────

def load_json_schema(path):
    with open(path, "r") as f:
        return json.load(f)

SCHEMA = load_json_schema(SCHEMA_PATH)
try:
    GRAPH_SCHEMA = load_json_schema(GRAPH_SCHEMA_PATH)
except (FileNotFoundError, json.JSONDecodeError):
    GRAPH_SCHEMA = None  # Graph format not yet defined — validation will skip graph schema checks
try:
    RECIPE_SCHEMA = load_json_schema(RECIPE_SCHEMA_PATH)
except (FileNotFoundError, json.JSONDecodeError):
    RECIPE_SCHEMA = None

# ─── Execution Simulation ──────────────────────────────────────────────────

class ProcedureRunner:
    """Simulates execution of a procedure state machine.

    Handles the actual YAML format used by Brewie procedures:
      - transition as a list of {expression: target} or {condition: ..., then: ...} mappings
      - 'read' as an action alias for 'read_sensor'
      - 'phase'/'name' and 'error'/'error_handler' field aliases
      - 'timeout_s' at the state level
      - Special targets: 'next_phase', 'error_handler', 'loops'
      - Special condition: 'step_complete' (always True)
    """

    def __init__(self, procedure_data, constants=None, global_values=None):
        self.proc = procedure_data
        self.constants = constants or {}
        self.global_values = deepcopy(global_values or {})
        self.parameters = dict(procedure_data.get("parameters", {}))
        self.states = procedure_data.get("states", {})
        self.start_state = get_start_state(procedure_data)
        self.current_state = self.start_state
        self.running = False
        self.sensor_values = {}
        self.user_inputs = {}
        self.variables = {}
        self.start_time = None
        self.state_enter_time = None
        self.iteration_count = 0
        self.previous_weights = {}
        self.mutexes_held = set()
        self.hops_released = set()
        self.log = []
        self._lock = threading.Lock()
        self._simulate_thread = None

        self._init_sensors()

    def _init_sensors(self):
        """Initialize sensor values to realistic defaults."""
        self.sensor_values = {
            # Weights (both naming conventions)
            "weight_boil_tank": 0.0,    "weight-boil-tank": 0.0,
            "weight_mash_tank": 0.0,    "weight-mash-tank": 0.0,
            "weight_source_tank": 25.0, "weight-source-tank": 25.0,
            "weight_destination_tank": 0.0, "weight-destination-tank": 0.0,
            "weightBoilTank": 0.0,      "weightMashTank": 0.0,
            # Temperatures
            "temp_boil_tank": 20.0,     "temp-boil-tank": 20.0,
            "temp_boil_water": 20.0,   "temp-boil-water": 20.0,
            "temp_mash_tank": 20.0,     "temp-mash-tank": 20.0,
            "temp_water_manual": 20.0,  "temp-water-manual": 20.0,
            "temperature_boil_tank": 20.0,
            "temperature_mash_water": 20.0,
            "temperature_mash_tank": 20.0,
            "temperatureAmbientBoilTank": 20.0,
            "temperatureAmbientMashIn": 20.0,
            "temperatureAmbientMashOut": 20.0,
            "temperatureAmbientWaterFeed": 20.0,
            "temperatureAmbientPump": 20.0,
            "temperatureAmbientHeaterBox": 20.0,
            # Pumps
            "pump_tacho": 0,
            "pump_current": 0.0,
            "pump-current": 0.0,
            "mash_pump_diagnostic": 0,
            "boil_pump_diagnostic": 0,
            "mash_pump_tacho": 0,
            "boil_pump_tacho": 0,
            "water_volume": 0.0,
            "tachoBoilPump": 0,
            "tachoMashPump": 0,
            # Flow / pressure
            "ro_water_flow_rate": 0.0,
            "system_pressure": 0.0,
            # Misc
            "elapsed_time": 0,
            "valves_configured": True,
            "manual_fill": False,
            "water_fill_method": "",
            "previous_weight_destination": 0.0,
            "pump_boil": False,
            "pump_mash": False,
        }

    def _normalize_sensor_key(self, key):
        """Map hyphenated sensor names to underscore versions and vice versa."""
        if key in self.sensor_values:
            return key
        # Try swapping hyphens↔underscores
        alt = key.replace("-", "_")
        if alt in self.sensor_values:
            return alt
        return key

    def set_sensor(self, name, value):
        """Update a sensor value (called by simulation or real hardware bridge)."""
        with self._lock:
            key = self._normalize_sensor_key(name)
            self.sensor_values[key] = value
            self.sensor_values[name] = value  # store under original name too
            self.log.append(f"[{self._elapsed():.1f}s] Sensor {name} = {value}")

    def set_user_input(self, var_name, value="confirmed"):
        """Simulate user providing input."""
        with self._lock:
            self.user_inputs[var_name] = value

    def _get_sensor(self, name):
        """Get a sensor value, normalizing key names."""
        key = self._normalize_sensor_key(name)
        return self.sensor_values.get(key, self.sensor_values.get(name, 0))

    def _eval_condition(self, expr):
        """Evaluate a condition expression against current state using the
        shared sandboxed evaluator (Phase 3 — replaces eval()).

        Delegates to safe_eval_condition() which uses Python's ast module to
        parse the expression and only permits arithmetic, comparisons,
        boolean ops, variable lookups, and calls to registered functions.
        Attribute access, imports, subscripts, lambdas, etc. are all blocked.
        """
        # Build the environment (sensor values, parameters, constants, etc.)
        env = {
            "iteration_count": self.iteration_count,
            "elapsed_time": self._elapsed(),
            "timeout_after": self._elapsed_since_state(),
            "timeout_exceeded": self._timeout_exceeded(),
            "initial_weight": self.sensor_values.get("weight_boil_tank", 0.0),
            "previous_weight": self.previous_weights.get("weight_boil_tank", 0.0),
            "previous_weight_destination": self.sensor_values.get("previous_weight_destination", 0.0),
        }
        env.update(self.sensor_values)
        env.update(self.user_inputs)
        env.update(self.parameters)
        env.update(self.constants)
        # Recipe globals are an immutable session snapshot and take precedence
        # over legacy procedure defaults and imported constants.
        env.update(self.global_values)
        # Engine variables are mutable procedure-local runtime state.
        env.update(self.variables)
        # Compute weight change
        cur = self.sensor_values.get("weight_boil_tank", 0.0)
        prev = self.previous_weights.get("weight_boil_tank", 0.0)
        env["weight_change"] = cur - prev
        rate = (cur - prev) / max(self._elapsed_since_state(), 1.0)
        env["weight_transfer_rate"] = rate

        # Register condition functions (callable only from within condition
        # expressions — NOT exposed as general Python functions).
        # duration_reached() accepts duration strings like '600s', '10m', etc.
        # pending_hop_addition() returns False in simulation (no hop schedule).
        functions = {
            "duration_reached": self._duration_reached,
            "pending_hop_addition": lambda: False,
        }

        result = safe_eval_condition(expr, env, functions)
        if not result:
            self.log.append(f"Condition false: '{expr}'")
        return result

    def _duration_reached(self, duration):
        """Check if elapsed time in current state exceeds the given duration.

        Accepts duration strings like '600s', '10m', '1h 30m', or numeric values.
        Returns False for unparseable durations (e.g. template placeholders
        like '{{parameters.max_duration_s}}' that haven't been substituted).
        """
        total_s = self._parse_duration(duration)
        if total_s is None:
            return False
        return self._elapsed_since_state() >= total_s

    def _elapsed(self):
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def _elapsed_since_state(self):
        if self.state_enter_time is None:
            return 0.0
        return time.time() - self.state_enter_time

    def _timeout_exceeded(self):
        """Check if current state's timeout has been exceeded."""
        state = self.states.get(self.current_state, {})
        if not isinstance(state, dict):
            return False
        # Check state-level timeout_s (used by actual YAML files)
        timeout_s = state.get("timeout_s")
        if not timeout_s:
            # Fall back to transition-level timeout_s (used by frontend-created procedures)
            trans = state.get("transition") or (
                state.get("transitions", [{}])[0] if state.get("transitions") else None
            )
            if trans and isinstance(trans, dict):
                timeout_s = trans.get("timeout_s") or state.get("timeout")
        if not timeout_s:
            return False
        total_s = self._parse_duration(timeout_s)
        if total_s is None:
            return False
        return self._elapsed_since_state() >= total_s

    @staticmethod
    def _parse_duration(duration_str):
        """Parse a duration string like '30s', '5m', '1h 30m' into seconds."""
        if not duration_str or not isinstance(duration_str, str):
            # Accept numeric as seconds
            try:
                return float(duration_str)
            except (TypeError, ValueError):
                return None
        total = 0
        parts = duration_str.strip().split()
        for part in parts:
            match = re.match(r'^(\d+(?:\.\d+)?)([smh])$', part)
            if match:
                val = float(match.group(1))
                unit = match.group(2)
                if unit == 's':
                    total += val
                elif unit == 'm':
                    total += val * 60
                elif unit == 'h':
                    total += val * 3600
            else:
                return None
        return total if total > 0 else None

    def start(self):
        """Start simulated execution in a background thread."""
        if self.running:
            return False
        self.running = True
        self.start_time = time.time()
        self.state_enter_time = time.time()
        self._simulate_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._simulate_thread.start()
        return True

    def stop(self):
        """Stop execution."""
        self.running = False

    def _run_loop(self):
        """Main simulation loop."""
        while self.running and self.current_state in self.states:
            with self._lock:
                state = self.states.get(self.current_state, {})
                if not isinstance(state, dict) or not state:
                    break

                # Execute actions
                actions = state.get("action", [])
                for action in actions:
                    self._execute_action(action)

                # Run on_exit actions when transitioning away (checked below)

                # Evaluate transitions
                next_state = self._evaluate_transitions(state)

                if next_state is None:
                    pass  # Stay in current state
                else:
                    # Every departure runs state cleanup before the target is
                    # entered, including next_phase and errors.
                    for action in state.get("on_exit", []):
                        self._execute_action(action)

                    if next_state == "error_handler":
                        eh_name = normalize_error_handler(self.proc)
                        if eh_name and eh_name in self.states:
                            self.current_state = eh_name
                            self.state_enter_time = time.time()
                        else:
                            self.log.append(f"ERROR: External error handler '{eh_name}' triggered")
                            self.running = False
                            break
                    elif next_state == "next_phase":
                        break
                    else:
                        self.current_state = next_state
                        self.state_enter_time = time.time()

                self.iteration_count += 1

            # Simulate sensor drift / changes
            self._simulate_sensors()
            time.sleep(0.5)

        self.running = False

    def _execute_action(self, action):
        """Execute a single action from an action list."""
        if not isinstance(action, dict):
            if isinstance(action, str):
                self.log.append(f"  Action: {action}")
            return

        key = list(action.keys())[0]
        params = action[key]

        # Normalize key aliases
        if key == "read_sensor":
            key = "read"

        if key == "read":
            val = params if isinstance(params, str) else str(params)
            self.log.append(f"  Read sensor: {val}")
        elif key == "set_valve":
            if isinstance(params, str):
                self.log.append(f"  Set valve: {params}")
            else:
                # Normalize state: on/off booleans → strings
                params = dict(params)
                if "state" in params:
                    if params["state"] is True:
                        params["state"] = "on"
                    elif params["state"] is False:
                        params["state"] = "off"
                self.log.append(f"  Set valve: {params}")
        elif key == "set_pump":
            if isinstance(params, str):
                self.log.append(f"  Set pump: {params}")
            else:
                params = dict(params)
                if "state" in params and isinstance(params["state"], bool):
                    params["state"] = "on" if params["state"] else "off"
                self.log.append(f"  Set pump: {params}")
        elif key == "set_heater":
            if isinstance(params, str):
                self.log.append(f"  Set heater: {params}")
            else:
                params = dict(params)
                if "state" in params and isinstance(params["state"], bool):
                    params["state"] = "on" if params["state"] else "off"
                self.log.append(f"  Set heater: {params}")
        elif key == "open_valve_for_seconds":
            self.log.append(f"  Open valve: {params}")
        elif key == "start_weight_monitoring":
            self.log.append(f"  Start weight monitoring: {params}")
        elif key == "stop_weight_monitoring":
            self.log.append(f"  Stop weight monitoring: {params}")
        elif key == "notify_user":
            if isinstance(params, str):
                self.log.append(f"  Notify: {params}")
            else:
                self.log.append(f"  Notify: {params.get('text', params)}")
        elif key in ("log_event", "log"):
            self.log.append(f"  Log: {params}")
        elif key == "wait_for_user_input":
            if isinstance(params, str):
                self.log.append(f"  Waiting for: {params}")
            else:
                self.log.append(f"  Waiting for: {params.get('key', params)}")
        elif key == "wait":
            self.log.append(f"  Wait: {params}")
        elif key == "check_mutex":
            if isinstance(params, str):
                self.log.append(f"  Check mutex: {params}")
            else:
                dev = params.get("device", params) if isinstance(params, dict) else params
                self.log.append(f"  Check mutex: {dev}")
        elif key == "acquire_mutex":
            if isinstance(params, str):
                self.mutexes_held.add(params)
                self.log.append(f"  Acquired mutex: {params}")
            elif isinstance(params, dict):
                dev = params.get("device", "")
                self.mutexes_held.add(dev)
                self.log.append(f"  Acquired mutex: {dev}")
        elif key == "release_mutex":
            if isinstance(params, str):
                self.mutexes_held.discard(params)
                self.log.append(f"  Released mutex: {params}")
            elif isinstance(params, dict):
                dev = params.get("device", "")
                self.mutexes_held.discard(dev)
                self.log.append(f"  Released mutex: {dev}")
        elif key == "enable_pid":
            self.log.append(f"  Enable PID: {params}")
        elif key == "disable_pid":
            self.log.append(f"  Disable PID: {params}")
        elif key == "run_hop_stage":
            config = params if isinstance(params, dict) else {}

            def resolve_number(value):
                if isinstance(value, (int, float)):
                    return value
                for namespace in (
                    self.global_values, self.variables, self.parameters, self.constants
                ):
                    resolved = namespace.get(value) if isinstance(value, str) else None
                    if isinstance(resolved, (int, float)):
                        return resolved
                return None

            start = resolve_number(config.get("start_minutes_remaining"))
            end = resolve_number(config.get("end_minutes_remaining"))
            cages = config.get("open_cages", [])
            if start is None or end is None or start <= end or not isinstance(cages, list):
                self.variables["action_complete"] = False
                self.log.append(f"  Invalid hop-stage configuration: {params}")
            else:
                duration_s = (start - end) * 60
                self.variables["hop_stage_duration_s"] = duration_s
                self.variables["hop_stage_open_cages"] = list(cages)
                self.variables["hop_stage_session"] = config.get("session")
                self.variables["avr_step_session_active"] = True
                self.variables["action_complete"] = self._elapsed_since_state() >= duration_s
                self.hops_released.update(
                    cage for cage in cages if isinstance(cage, int) and 1 <= cage <= 4
                )
                marker = f"hop-stage:{self.current_state}"
                if self.variables.get("_last_operation_marker") != marker:
                    self.log.append(
                        f"  Run AVR hop stage: cages={cages}, duration_s={duration_s:g}, "
                        f"session={config.get('session')}"
                    )
                    self.variables["_last_operation_marker"] = marker
        elif key == "finish_avr_step_session":
            config = params if isinstance(params, dict) else {}
            operation = config.get("operation")
            if not isinstance(operation, str):
                self.variables["action_complete"] = False
                self.log.append(f"  Invalid AVR session finish configuration: {params}")
            else:
                # Simulation models a successful P201 ACK. The production HAL
                # must not complete this action until that ACK is received.
                self.variables["avr_step_session_active"] = False
                self.variables["action_complete"] = True
                marker = f"avr-session-finish:{self.current_state}"
                if self.variables.get("_last_operation_marker") != marker:
                    self.log.append(
                        f"  Finish AVR step session: operation={operation}, command=P201, ACK=ok"
                    )
                    self.variables["_last_operation_marker"] = marker
        elif key == "set_counter":
            config = params if isinstance(params, dict) else {"name": params}
            name = config.get("name")
            value = config.get("value", 0)
            if isinstance(name, str) and isinstance(value, (int, float)):
                self.variables[name] = value
                self.log.append(f"  Set counter {name} = {value}")
            else:
                self.log.append(f"  Invalid counter configuration: {params}")
        elif key == "increment_counter":
            config = params if isinstance(params, dict) else {"name": params}
            name = config.get("name")
            amount = config.get("amount", 1)
            if isinstance(name, str) and isinstance(amount, (int, float)):
                self.variables[name] = self.variables.get(name, 0) + amount
                self.log.append(f"  Increment counter {name} = {self.variables[name]}")
            else:
                self.log.append(f"  Invalid counter configuration: {params}")
        elif key == "release_scheduled_hops":
            config = params if isinstance(params, dict) else {}
            schedule_name = config.get("schedule_parameter", "hop_additions")
            duration_name = config.get("boil_duration_parameter", "boil_duration_min")
            schedule = self.global_values.get(
                schedule_name, self.parameters.get(schedule_name, [])
            )
            duration_min = self.global_values.get(
                duration_name, self.parameters.get(duration_name, 0)
            )
            if not isinstance(schedule, list) or not isinstance(duration_min, (int, float)):
                self.log.append("  Invalid hop schedule configuration")
                return
            elapsed_s = self._elapsed_since_state()
            for addition in schedule:
                if not isinstance(addition, dict):
                    continue
                cage = addition.get("cage")
                remaining = addition.get("minutes_remaining")
                if cage in self.hops_released or not isinstance(cage, int) or not isinstance(remaining, (int, float)):
                    continue
                release_at_s = max(0, (duration_min - remaining) * 60)
                if elapsed_s < release_at_s:
                    continue
                valve = f"hop{cage}"
                self.log.append(
                    f"  Release hop cage {cage}: {addition.get('hop', 'hop')} "
                    f"({addition.get('amount_g', 0)}g, {remaining} min remaining)"
                )
                self._execute_action({"open_valve_for_seconds": {"valve": valve, "duration": "2s"}})
                self.hops_released.add(cage)
        else:
            self.log.append(f"  Unknown action: {key} = {params}")

    def _parse_transition_item(self, item):
        """Parse a transition list item into (condition_expr, target, is_default).

        Handles three formats:
          1. { "expression": "target_state" }  — single-key mapping where key is condition
          2. { "condition": "expr", "then": "target" }
          3. { "default": "target_state" }
        """
        if not isinstance(item, dict):
            return None, None, False

        if "default" in item:
            return None, item["default"], True

        if "condition" in item and "then" in item:
            return item["condition"], item["then"], False

        # Single-key mapping: the sole key is the condition expression,
        # the value is the target state
        if len(item) == 1:
            cond, target = next(iter(item.items()))
            if cond.lower() == "default":
                return None, target, True
            # 'then' without 'condition' — unlikely but handle defensively
            if cond == "then":
                return "true", target, False
            return cond, target, False

        # Multi-key without 'condition'/'then'/'default' — take first non-default pair
        for k, v in item.items():
            if k.lower() != "default":
                return k, v, False

        return None, None, False

    def _evaluate_transitions(self, state):
        """Evaluate transition rules and return the next state.

        Handles the list-based transition format used by Brewie YAML files:
          transition:
            - "expression": target_state
            - condition: "expr"
              then: target_state
            - default: fallback_target
        Also falls back to the dict-based format (conditions + next_state)
        used by frontend-created procedures.
        """
        # Check loops first (for states with loop-based iteration)
        loops = state.get("loops", [])
        if loops:
            for loop_item in loops:
                if not isinstance(loop_item, dict):
                    continue
                if "condition" in loop_item and "next_state" in loop_item:
                    if self._eval_condition(loop_item["condition"]):
                        return loop_item["next_state"]
                # Also handle {expr: target} format in loops
                if len(loop_item) == 1:
                    cond, target = next(iter(loop_item.items()))
                    if cond.lower() != "default":
                        if self._eval_condition(cond):
                            return target

        # Get transition(s) — support both list and dict formats
        transition = state.get("transition")

        # Dict-based format (frontend-created procedures): {conditions: [...], next_state: {...}}
        if isinstance(transition, dict):
            return self._eval_dict_transition(transition)

        # List-based format (YAML procedure files): [{expr: target}, ...]
        if isinstance(transition, list):
            return self._eval_list_transition(transition)

        # Also check 'transitions' (plural) as a list of {if:..., then:...}
        transitions_list = state.get("transitions", [])
        if transitions_list:
            return self._eval_transitions_list(transitions_list)

        return None

    def _eval_dict_transition(self, transition):
        """Evaluate dict-based transition: {conditions: [...], next_state: {...}}."""
        conditions = transition.get("conditions", [])
        next_state_map = transition.get("next_state", {})

        for cond in conditions:
            if self._eval_condition(cond):
                # Find matching next_state key
                target = next_state_map.get(cond)
                if target is not None:
                    return self._resolve_special_target(target)
                # If no exact match, return first non-default
                for key, target in next_state_map.items():
                    if key != "default":
                        return self._resolve_special_target(target)
                return self._resolve_special_target(next_state_map.get("default"))

        # No condition matched — use default
        default = next_state_map.get("default")
        if default is not None:
            return self._resolve_special_target(default)
        return None

    def _eval_list_transition(self, transition_list):
        """Evaluate list-based transition: [{"expr": target}, ..., {default: target}]."""
        default_target = None

        for item in transition_list:
            cond, target, is_default = self._parse_transition_item(item)
            if is_default:
                default_target = target
                continue

            if cond is not None and self._eval_condition(cond):
                return self._resolve_special_target(target)

        # No condition matched — use default
        if default_target is not None:
            return self._resolve_special_target(default_target)

        return None  # Stay in current state

    def _eval_transitions_list(self, transitions_list):
        """Evaluate legacy transitions list format: [{if: expr, then: target}, ...]."""
        for trans in transitions_list:
            if not isinstance(trans, dict):
                continue
            cond = trans.get("if")
            target = trans.get("then")
            if cond is None and target is not None:
                # No condition — always fires
                return self._resolve_special_target(target)
            if cond is not None and self._eval_condition(cond):
                return self._resolve_special_target(target)
        return None

    def _resolve_special_target(self, target):
        """Resolve special transition targets to actionable values."""
        if target == "error_handler":
            return "error_handler"
        if target == "loops":
            return None  # Stay in current state
        if target == "next_phase":
            return "next_phase"
        # Check for timeout → error_handler
        if isinstance(target, str) and target.startswith("error-"):
            eh = normalize_error_handler(self.proc)
            if eh:
                return "error_handler"
            # External error — log and stop
            self.log.append(f"ERROR: External error handler '{target}' triggered")
            self.running = False
        return target

    def _simulate_sensors(self):
        """Simulate sensor drift for demonstration purposes."""
        current = self.current_state
        state = self.states.get(current, {})

        # Determine target temperature from parameters
        target_temp = None
        for key in ("target_temp_C", "mash_temp", "mash_water_temp", "sparge_water_temp", "sparge_temp", "target_cool_temp", "target_boil_temp"):
            if key in self.parameters:
                target_temp = self.parameters[key]
                break

        # Simulate heating ramp
        if current == "heat_mash_water" or current == "heat_sparge_water" or current == "heat_to_boil":
            cur = self._get_sensor("temp_boil_tank")
            if target_temp and cur < target_temp:
                step = (target_temp - cur) / 10.0
                self.sensor_values["temp_boil_tank"] = min(cur + step, target_temp + 0.2)
                self.sensor_values["temp-boil-tank"] = self.sensor_values["temp_boil_tank"]
                self.sensor_values["temp_boil_water"] = self.sensor_values["temp_boil_tank"]
                self.sensor_values["temp-boil-water"] = self.sensor_values["temp_boil_tank"]

        # Inject mock water weight for check_water_level states so simulation progresses
        if "check_water_level" in current or "check_water" in current:
            vol_key = None
            for k in ("mash_water_volume", "sparge_water_volume", "water_volume",
                       "transfer_volume", "cleaning_solution_volume"):
                if k in self.parameters:
                    vol_key = k
                    break
            if vol_key:
                target = self.parameters[vol_key]
                self.sensor_values["weight-boil-tank"] = target * 0.95
                self.sensor_values["weight_boil_tank"] = target * 0.95

        # Simulate pump tacho
        pump_running_states = ("prime_pump", "prepare_transfer", "pump_until_current_drop",
                               "execute_transfer", "transfer_cycle", "execute_burp_cycles",
                               "circulate_cleaning", "circulate_rinse", "circulate_caustic",
                               "circulate_sanitizer", "maintain_sparge", "start_sparge",
                               "recirculate_mash", "start_mash_recirculation", "rolling_boil")
        if current in pump_running_states:
            self.sensor_values["pump_tacho"] = 150
        else:
            self.sensor_values["pump_tacho"] = 0

        # Simulate weight changes during filling
        if current == "fill_automatic":
            procedure_name = normalize_name(self.proc)
            if procedure_name == "fill_mash_water":
                target = self.global_values.get("mash_water_volume_L")
            elif procedure_name == "fill_sparge_water":
                target = self.global_values.get("sparge_water_volume_L")
            else:
                target = None
            cur = self._get_sensor("weight_boil_tank")
            if isinstance(target, (int, float)) and cur < target - 0.1:
                self.sensor_values["weight_boil_tank"] = min(cur + 1.0, target)
                self.sensor_values["weight-boil-tank"] = self.sensor_values["weight_boil_tank"]

        # Simulate weight decrease during transfers/emptying
        if current in ("pump_until_current_drop", "execute_transfer", "transfer_cycle"):
            cur = self._get_sensor("weight_boil_tank")
            if cur > 0.5:
                self.sensor_values["weight_boil_tank"] = max(cur - 0.8, 0.0)
                self.sensor_values["weight-boil-tank"] = self.sensor_values["weight_boil_tank"]
                dest = self._get_sensor("weight_destination_tank")
                self.sensor_values["weight_destination_tank"] = dest + 0.8
                self.sensor_values["weight-destination-tank"] = self.sensor_values["weight_destination_tank"]

        # Simulate cooling
        if current == "monitor_cooling" and target_temp:
            cur = self._get_sensor("temp_boil_tank")
            if cur > target_temp:
                self.sensor_values["temp_boil_tank"] = max(cur - 1.0, target_temp)
                self.sensor_values["temp-boil-tank"] = self.sensor_values["temp_boil_tank"]
                self.sensor_values["temp_boil_water"] = self.sensor_values["temp_boil_tank"]
                self.sensor_values["temp-boil-water"] = self.sensor_values["temp_boil_tank"]

        # Simulate pump current
        if self.sensor_values["pump_tacho"] > 100:
            self.sensor_values["pump_current"] = 1.2
            self.sensor_values["pump-current"] = 1.2
            self.sensor_values["boil_pump_diagnostic"] = (
                2 if current == "pump_until_current_drop" and self._elapsed_since_state() >= 3.0 else 1
            )
        else:
            self.sensor_values["pump_current"] = 0.0
            self.sensor_values["pump-current"] = 0.0
            self.sensor_values["boil_pump_diagnostic"] = 0

        # Update elapsed_time sensor
        self.sensor_values["elapsed_time"] = int(self._elapsed())

    def get_state(self):
        """Return current execution state."""
        return {
            "procedure": normalize_name(self.proc),
            "current_state": self.current_state,
            "parameters": self.parameters,
            "global_values": deepcopy(self.global_values),
            "sensor_values": self.sensor_values,
            "running": self.running,
            "last_updated": datetime.now().isoformat(),
            "elapsed_s": self._elapsed() if self.start_time else 0,
            "state_enter_time": datetime.fromtimestamp(self.state_enter_time).isoformat() if self.state_enter_time else None,
            "iteration_count": self.iteration_count,
            "variables": dict(self.variables),
            "log": list(self.log[-20:]),
            "mutexes_held": list(self.mutexes_held),
            "hops_released": sorted(self.hops_released),
        }


# ─── Global Execution Trackers ─────────────────────────────────────────────

_runners = {}
_runners_lock = threading.Lock()

# ─── File I/O ──────────────────────────────────────────────────────────────

def is_procedure_file(path):
    """Check if a YAML file is a procedure file."""
    if not path.suffix in (".yml", ".yaml"):
        return False
    skip = {"procedure.schema.json", "README.md", "procedure-editor-api.yaml"}
    if path.name in skip:
        return False
    # Skip backup directories and non-procedure config
    if "old" in path.parts:
        return False
    if ".backups" in path.parts:
        return False
    return True


def _resolve_procedure_path(name):
    """Find a procedure file by name/stem, trying .yml and .yaml extensions."""
    p = Path(PROCEDURES_DIR)
    for ext in (".yml", ".yaml"):
        candidate = p / f"{name}{ext}"
        if candidate.exists() and is_procedure_file(candidate):
            return candidate
    # Also try the full name as-is (may include extension)
    candidate = p / name
    if candidate.exists() and is_procedure_file(candidate):
        return candidate
    return None


def list_procedure_files():
    """List all YAML procedure files in the procedures directory."""
    files = []
    p = Path(PROCEDURES_DIR)
    if not p.exists():
        return files
    for f in sorted(p.glob("*.yml")):
        if is_procedure_file(f):
            files.append(f)
    for f in sorted(p.glob("*.yaml")):
        if is_procedure_file(f):
            files.append(f)
    return files


def load_yaml_file(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_yaml_file(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _recipe_directories():
    """Return recipe directories in precedence order (bundled, then user)."""
    directories = [Path(BUNDLED_RECIPES_DIR)]
    writable = Path(RECIPES_DIR)
    if writable != directories[0]:
        directories.append(writable)
    return directories


def _recipe_files():
    """Return recipe files keyed by id; user recipes override bundled recipes."""
    files = {}
    for directory in _recipe_directories():
        if not directory.exists():
            continue
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(directory.glob(pattern)):
                if ".backups" not in path.parts:
                    files[path.stem] = path
    return files


def _resolve_recipe_path(recipe_id):
    if not isinstance(recipe_id, str) or not re.match(r"^[a-z][a-z0-9_-]*$", recipe_id):
        return None
    return _recipe_files().get(recipe_id)


def _recipe_timeline(data):
    boil = data.get("boil", {}) if isinstance(data, dict) else {}
    duration = boil.get("duration_min", 0)
    additions = []
    for addition in boil.get("hop_additions", []):
        if not isinstance(addition, dict):
            continue
        remaining = addition.get("minutes_remaining", 0)
        additions.append({
            **addition,
            "release_after_min": duration - remaining,
        })
    additions.sort(key=lambda item: (-item.get("minutes_remaining", 0), item.get("cage", 0)))
    maximum_remaining = max((item.get("minutes_remaining", 0) for item in additions), default=0)
    return {
        "boil_duration_min": duration,
        "initial_unhopped_boil_min": duration - maximum_remaining,
        "additions": additions,
    }


def validate_recipe(data):
    errors = []
    warnings = []
    if not isinstance(data, dict):
        return {"valid": False, "errors": ["root: recipe must be an object"], "warnings": []}

    if RECIPE_SCHEMA and Draft7Validator:
        schema_errors = sorted(
            Draft7Validator(RECIPE_SCHEMA).iter_errors(data),
            key=lambda error: list(error.path),
        )
        for error in schema_errors:
            field_path = ".".join(str(part) for part in error.path) or "root"
            errors.append(f"{field_path}: {error.message}")

    boil = data.get("boil", {})
    duration = boil.get("duration_min", 0) if isinstance(boil, dict) else 0
    additions = boil.get("hop_additions", []) if isinstance(boil, dict) else []
    cages = [item.get("cage") for item in additions if isinstance(item, dict)]
    if sorted(cages) != [1, 2, 3, 4]:
        errors.append("boil.hop_additions: define each physical cage 1 through 4 exactly once")
    remaining_by_cage = {}
    for index, addition in enumerate(additions):
        if not isinstance(addition, dict):
            continue
        remaining = addition.get("minutes_remaining")
        cage = addition.get("cage")
        if isinstance(cage, int) and isinstance(remaining, (int, float)):
            remaining_by_cage[cage] = remaining
        if isinstance(remaining, (int, float)) and isinstance(duration, (int, float)) and remaining > duration:
            errors.append(
                f"boil.hop_additions.{index}.minutes_remaining: {remaining} exceeds boil duration {duration}"
            )
    ordered_remaining = [remaining_by_cage.get(cage) for cage in range(1, 5)]
    if all(isinstance(value, (int, float)) for value in ordered_remaining):
        if ordered_remaining != sorted(ordered_remaining, reverse=True):
            errors.append(
                "boil.hop_additions: cage times must descend from cage 1 through cage 4"
            )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _save_recipe(recipe_id, data):
    validation = validate_recipe(data)
    if not validation["valid"]:
        return None, validation
    if data.get("id") != recipe_id:
        return None, {
            "valid": False,
            "errors": [f"id: payload id '{data.get('id')}' must match URL id '{recipe_id}'"],
            "warnings": [],
        }

    directory = Path(RECIPES_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{recipe_id}.yml"
    if path.exists():
        backup_dir = directory / ".backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{recipe_id}.{datetime.now().strftime('%Y%m%d%H%M%S%f')}.yml"
        save_yaml_file(backup, load_yaml_file(path))
    save_yaml_file(path, data)
    return path, validation


def _lookup_binding(context, expression):
    current = context
    for part in expression.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(expression)
        current = current[part]
    return deepcopy(current)


def _resolve_bindings(value, context):
    """Resolve ${recipe.path} values while preserving non-string types."""
    if isinstance(value, dict):
        return {key: _resolve_bindings(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_bindings(item, context) for item in value]
    if not isinstance(value, str):
        return value
    full = re.fullmatch(r"\$\{([^}]+)\}", value)
    if full:
        try:
            return _lookup_binding(context, full.group(1))
        except KeyError:
            return value

    def replace(match):
        try:
            return str(_lookup_binding(context, match.group(1)))
        except KeyError:
            return match.group(0)

    return re.sub(r"\$\{([^}]+)\}", replace, value)


def recipe_global_values(recipe):
    """Project a validated recipe into the immutable runtime namespace."""
    water = recipe.get("water", {})
    mash = recipe.get("mash", {})
    sparge = recipe.get("sparge", {})
    boil = recipe.get("boil", {})
    cooling = recipe.get("cooling", {})
    sedimentation = recipe.get("sedimentation", {})
    mash_steps = mash.get("steps", [])
    first_rest = mash_steps[0] if len(mash_steps) > 0 else {}
    second_rest = mash_steps[1] if len(mash_steps) > 1 else {}
    total_water_volume = water.get("mash_volume_L", 0) + water.get("sparge_volume_L", 0)
    mash_tank_sparge_limit = 20.0
    hop_additions = boil.get("hop_additions", [])
    hop_by_cage = {
        addition.get("cage"): addition
        for addition in hop_additions
        if isinstance(addition, dict) and isinstance(addition.get("cage"), int)
    }
    hopping_duration = max(
        (
            addition.get("minutes_remaining", 0)
            for addition in hop_additions
            if isinstance(addition, dict)
            and isinstance(addition.get("minutes_remaining"), (int, float))
        ),
        default=0,
    )
    return deepcopy({
        "recipe_id": recipe.get("id"),
        "recipe_name": recipe.get("name"),
        "batch_volume_L": recipe.get("batch_volume_L"),
        "mash_water_volume_L": water.get("mash_volume_L"),
        "sparge_water_volume_L": water.get("sparge_volume_L"),
        "fermentables": recipe.get("fermentables", []),
        "mash_in_temperature_C": mash.get("mash_in_temperature_C"),
        "mash_steps": mash_steps,
        "mash_rest_1_temperature_C": first_rest.get("target_temperature_C"),
        "mash_rest_1_duration_min": first_rest.get("duration_min"),
        "mash_rest_2_temperature_C": second_rest.get("target_temperature_C"),
        "mash_rest_2_duration_min": second_rest.get("duration_min"),
        "mash_total_duration_min": sum(
            step.get("duration_min", 0) for step in mash_steps if isinstance(step, dict)
        ),
        "sparge_target_temperature_C": sparge.get("target_temperature_C"),
        "sparge_cycle_count": sparge.get("cycle_count"),
        "mash_tank_sparge_limit_L": mash_tank_sparge_limit,
        "sparge_boil_reserve_volume_L": max(0.0, total_water_volume - mash_tank_sparge_limit),
        "boil_duration_min": boil.get("duration_min"),
        "boil_target_temperature_C": boil.get("target_temperature_C"),
        "hop_additions": hop_additions,
        "hopping_duration_min": hopping_duration,
        "initial_unhopped_boil_duration_min": max(
            0, boil.get("duration_min", 0) - hopping_duration
        ),
        "hop_cage_1_minutes_remaining": hop_by_cage.get(1, {}).get("minutes_remaining"),
        "hop_cage_2_minutes_remaining": hop_by_cage.get(2, {}).get("minutes_remaining"),
        "hop_cage_3_minutes_remaining": hop_by_cage.get(3, {}).get("minutes_remaining"),
        "hop_cage_4_minutes_remaining": hop_by_cage.get(4, {}).get("minutes_remaining"),
        "cooling_target_temperature_C": cooling.get("target_temperature_C"),
        "sedimentation_duration_min": sedimentation.get("duration_min"),
        "yeasts": recipe.get("yeasts", []),
    })


def persist_validated(path, data):
    """Validate and back up a mutation before it reaches the live YAML file."""
    fmt = classify_procedure(data)
    validation = validate_procedure(data, SCHEMA, fmt, graph_schema=GRAPH_SCHEMA)
    if not validation.get("valid", False):
        return jsonify({"error": "Validation failed", "validation": validation}), 422
    backup_dir = path.parent / ".backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.{datetime.now().strftime('%Y%m%d%H%M%S%f')}{path.suffix}"
    save_yaml_file(backup_path, load_yaml_file(path))
    save_yaml_file(path, data)
    return None


# ─── API Routes ────────────────────────────────────────────────────────────

@app.route("/api/recipes", methods=["GET"])
def api_list_recipes():
    recipes = []
    writable_dir = Path(RECIPES_DIR)
    for recipe_id, path in sorted(_recipe_files().items()):
        data = load_yaml_file(path) or {}
        recipes.append({
            "id": recipe_id,
            "name": data.get("name", recipe_id),
            "style": data.get("style", ""),
            "batch_volume_L": data.get("batch_volume_L"),
            "source": "user" if path.parent == writable_dir else "bundled",
        })
    return jsonify({"recipes": recipes})


@app.route("/api/recipes/<recipe_id>", methods=["GET"])
def api_get_recipe(recipe_id):
    path = _resolve_recipe_path(recipe_id)
    if not path:
        return jsonify({"error": f"Recipe '{recipe_id}' not found"}), 404
    data = load_yaml_file(path)
    return jsonify({
        "id": recipe_id,
        "file": path.name,
        "data": data,
        "timeline": _recipe_timeline(data),
        "validation": validate_recipe(data),
    })


@app.route("/api/recipes/<recipe_id>/validate", methods=["POST"])
def api_validate_recipe(recipe_id):
    body = request.get_json(silent=True) or {}
    data = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(data, dict) and data.get("id") not in (None, recipe_id):
        return jsonify({
            "valid": False,
            "errors": [f"id: payload id '{data.get('id')}' must match URL id '{recipe_id}'"],
            "warnings": [],
        })
    return jsonify(validate_recipe(data))


@app.route("/api/recipes/<recipe_id>", methods=["PUT"])
def api_save_recipe(recipe_id):
    if not re.match(r"^[a-z][a-z0-9_-]*$", recipe_id):
        return jsonify({"error": "Recipe id must use lowercase snake_case"}), 400
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No recipe data provided"}), 400
    path, validation = _save_recipe(recipe_id, data)
    if not path:
        return jsonify({"error": "Recipe validation failed", "validation": validation}), 422
    return jsonify({"id": recipe_id, "file": path.name, "saved": True, "validation": validation})

@app.route("/api/procedures", methods=["GET"])
def api_list_procedures():
    """List all procedures."""
    files = list_procedure_files()
    procedure_names = []
    graph_procedures = []

    for f in files:
        data = load_yaml_file(f)
        fmt = classify_procedure(data)
        # Use filename stem as the canonical name — matches _resolve_procedure_path
        name = f.stem
        display_name = normalize_name(data) or name
        entry = {"name": name, "file": f.name, "format": fmt, "display_name": display_name}

        if fmt == "graph":
            entry["entry_point"] = data.get("entry_point", "")
            graph_procedures.append(entry)
        elif fmt == "state_machine":
            entry["start_state"] = get_start_state(data) or ""
            entry["states_count"] = len(data.get("states", {}))
            entry["error_handler"] = normalize_error_handler(data)
            procedure_names.append(entry)
        elif fmt == "constants":
            entry["type"] = "constants"
            procedure_names.append(entry)
        else:
            # Unknown format — list it but don't categorize
            entry["states_count"] = len(data.get("states", {})) if isinstance(data.get("states"), dict) else 0
            entry["error_handler"] = normalize_error_handler(data)
            procedure_names.append(entry)

    return jsonify({
        "procedures": procedure_names,
        "graph_procedures": graph_procedures
    })


@app.route("/api/procedures/<name>", methods=["GET"])
def api_get_procedure(name):
    """Get a procedure definition."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Procedure '{name}' not found"}), 404
    data = load_yaml_file(path)
    fmt = classify_procedure(data)
    # Normalize the data for the frontend
    if fmt == "state_machine":
        data.setdefault("name", normalize_name(data))
        data.setdefault("error_handler", normalize_error_handler(data))
        if "start_state" not in data:
            data["start_state"] = get_start_state(data)
    return jsonify({
        "name": name,
        "file": path.name,
        "format": fmt,
        "data": data
    })


def _graph_api_model(data):
    """Derive the frontend visualization from ordered orchestration data."""
    nodes = []
    edges = []
    if isinstance(data.get("steps"), list):
        previous_ids = []
        y = 70
        center_x = 360
        edge_index = 0

        for step in data["steps"]:
            if not isinstance(step, dict):
                continue
            parallel = step.get("parallel")
            if isinstance(parallel, dict):
                branches = parallel.get("branches", [])
                branch_ids = []
                spacing = 280
                first_x = center_x - ((len(branches) - 1) * spacing / 2)
                for branch_index, branch in enumerate(branches):
                    if not isinstance(branch, dict):
                        continue
                    branch_id = branch.get("id", branch.get("procedure", f"parallel-{branch_index}"))
                    branch_ids.append(branch_id)
                    nodes.append({
                        **branch,
                        "id": branch_id,
                        "position": {"x": first_x + branch_index * spacing, "y": y},
                        "parallel_group": parallel.get("id"),
                        "parallel_role": "primary" if branch_id == parallel.get("primary") else "concurrent",
                    })
                    for source in previous_ids:
                        edges.append({
                            "id": f"edge-{edge_index}",
                            "from": source,
                            "to": branch_id,
                            "label": "parallel",
                        })
                        edge_index += 1
                previous_ids = branch_ids
                y += 170
                continue

            node_id = step.get("id", step.get("procedure", f"step-{len(nodes)}"))
            nodes.append({**step, "id": node_id, "position": {"x": center_x, "y": y}})
            for source in previous_ids:
                edges.append({
                    "id": f"edge-{edge_index}",
                    "from": source,
                    "to": node_id,
                    "label": "join" if len(previous_ids) > 1 else "",
                })
                edge_index += 1
            previous_ids = [node_id]
            y += 140
    elif isinstance(data.get("nodes"), list):
        nodes = data["nodes"]
        edges = data.get("edges", [])
    elif isinstance(data.get("phases"), list):
        for phase in data["phases"]:
            phase_id = phase.get("phase", phase.get("name", ""))
            nodes.append({
                "id": phase_id,
                "label": phase.get("description", phase_id),
                "procedure": phase_id,
                "description": phase.get("description", ""),
                "parameters": phase.get("parameters", {})
            })
            if phase.get("next_phase"):
                edges.append({"from": phase_id, "to": phase["next_phase"]})
    elif isinstance(data.get("states"), dict):
        for state_id, state in data["states"].items():
            procedures = [a.get("call_procedure") for a in state.get("action", []) if isinstance(a, dict) and a.get("call_procedure")]
            procedure = procedures[0] if procedures else state_id
            nodes.append({
                "id": state_id,
                "label": state.get("description", state_id),
                "procedure": procedure,
                "description": state.get("description", ""),
                "parameters": state.get("parameters", {})
            })
            for transition in state.get("transition", state.get("transitions", [])):
                if isinstance(transition, dict):
                    target = transition.get("then") or transition.get("default")
                    if target:
                        edges.append({"from": state_id, "to": target, "condition": transition.get("if") or transition.get("condition")})

    normalized_edges = []
    for index, edge in enumerate(edges):
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            source, target = edge[0], edge[1]
            normalized_edges.append({"id": f"edge-{index}", "source": source, "target": target})
        elif isinstance(edge, dict):
            source = edge.get("source", edge.get("from", ""))
            target = edge.get("target", edge.get("to", ""))
            if source and target:
                normalized_edges.append({
                    "id": edge.get("id", f"edge-{index}"),
                    "source": source,
                    "target": target,
                    "label": edge.get("label"),
                    "condition": edge.get("condition"),
                    "choice_key": edge.get("choice_key"),
                    "choice_value": edge.get("choice_value")
                })

    normalized_nodes = []
    for index, node in enumerate(nodes):
        node_id = node.get("id", node.get("phase", node.get("name", f"node-{index}"))) if isinstance(node, dict) else str(node)
        normalized_nodes.append({
            "id": node_id,
            "label": node.get("label", node.get("description", node_id)) if isinstance(node, dict) else node_id,
            "procedure": node.get("procedure", node_id) if isinstance(node, dict) else node_id,
            "description": node.get("description", "") if isinstance(node, dict) else "",
            "parameters": node.get("parameters", {}) if isinstance(node, dict) else {},
            "position": node.get("position", {"x": 360, "y": 70 + index * 140}) if isinstance(node, dict) else {"x": 360, "y": 70 + index * 140},
            "parallel_group": node.get("parallel_group") if isinstance(node, dict) else None,
            "parallel_role": node.get("parallel_role") if isinstance(node, dict) else None,
        })
    return {
        "name": data.get("name", "beer_brewing"),
        "description": data.get("description", ""),
        "entry_point": data.get("entry_point", data.get("start_state", normalized_nodes[0]["id"] if normalized_nodes else "")),
        "source_format": "sequence" if isinstance(data.get("steps"), list) else "graph",
        "nodes": normalized_nodes,
        "edges": normalized_edges
    }


def _workflow_step_ids(steps):
    """Return all top-level and parallel-branch identifiers."""
    identifiers = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "id" in step:
            identifiers.append(step["id"])
            continue
        parallel = step.get("parallel", {})
        identifiers.extend(
            branch.get("id") for branch in parallel.get("branches", [])
            if isinstance(branch, dict) and branch.get("id")
        )
    return identifiers


def _insert_workflow_step(graph_data, new_step, after=None):
    """Insert a sequential step after a top-level step or parallel group."""
    updated = deepcopy(graph_data)
    steps = updated.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Workflow does not use ordered steps")
    if new_step["id"] in _workflow_step_ids(steps):
        raise ValueError(f"Workflow step '{new_step['id']}' already exists")
    if not after:
        steps.append(new_step)
        return updated

    for index, step in enumerate(steps):
        if step.get("id") == after:
            steps.insert(index + 1, new_step)
            return updated
        branch_ids = {
            branch.get("id") for branch in step.get("parallel", {}).get("branches", [])
            if isinstance(branch, dict)
        }
        if after in branch_ids:
            steps.insert(index + 1, new_step)
            return updated
    raise ValueError(f"Workflow step '{after}' not found")


def _remove_workflow_step(graph_data, step_id):
    """Remove a top-level sequential step while preserving its procedure file."""
    updated = deepcopy(graph_data)
    steps = updated.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Workflow does not use ordered steps")
    for index, step in enumerate(steps):
        if step.get("id") == step_id:
            del steps[index]
            return updated
        branch_ids = {
            branch.get("id") for branch in step.get("parallel", {}).get("branches", [])
            if isinstance(branch, dict)
        }
        if step_id in branch_ids:
            raise ValueError("Parallel branches must be edited as a parallel group")
    raise ValueError(f"Workflow step '{step_id}' not found")


@app.route("/api/graphs/<name>", methods=["GET"])
def api_get_graph(name):
    """Return an orchestration graph in the frontend's explicit graph model."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Graph '{name}' not found"}), 404
    data = load_yaml_file(path)
    fmt = classify_procedure(data)
    if fmt != "graph" and not (name == "beer_brewing" and isinstance(data.get("states"), dict)):
        return jsonify({"error": f"'{name}' is not an orchestration graph"}), 400
    return jsonify({"name": name, "file": path.name, "data": _graph_api_model(data)})


@app.route("/api/graphs/<name>/steps", methods=["POST"])
def api_add_graph_step(name):
    """Add a sequential procedure step to an ordered workflow."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Graph '{name}' not found"}), 404
    graph_data = load_yaml_file(path)
    if classify_procedure(graph_data) != "graph" or not isinstance(graph_data.get("steps"), list):
        return jsonify({"error": f"'{name}' is not an ordered workflow"}), 400

    body = request.get_json(silent=True) or {}
    step_id = body.get("id", "")
    procedure = body.get("procedure", "")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", step_id):
        return jsonify({"error": "Step id must use lowercase snake_case"}), 400
    if not re.fullmatch(r"[a-z][a-z0-9_]*", procedure):
        return jsonify({"error": "Procedure id must use lowercase snake_case"}), 400
    procedure_path = _resolve_procedure_path(procedure)
    if not procedure_path or classify_procedure(load_yaml_file(procedure_path)) != "state_machine":
        return jsonify({"error": f"Procedure '{procedure}' not found"}), 404

    step = {"id": step_id, "label": body.get("label") or step_id.replace("_", " ").title(), "procedure": procedure}
    try:
        updated = _insert_workflow_step(graph_data, step, body.get("after"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 409
    failure = persist_validated(path, updated)
    if failure:
        return failure
    return jsonify({"name": name, "data": _graph_api_model(updated)}), 201


@app.route("/api/graphs/<name>/steps/<step_id>", methods=["DELETE"])
def api_remove_graph_step(name, step_id):
    """Remove a sequential workflow step without deleting its procedure."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Graph '{name}' not found"}), 404
    graph_data = load_yaml_file(path)
    if classify_procedure(graph_data) != "graph" or not isinstance(graph_data.get("steps"), list):
        return jsonify({"error": f"'{name}' is not an ordered workflow"}), 400
    try:
        updated = _remove_workflow_step(graph_data, step_id)
    except ValueError as error:
        return jsonify({"error": str(error)}), 409
    failure = persist_validated(path, updated)
    if failure:
        return failure
    return jsonify({"name": name, "data": _graph_api_model(updated)})


@app.route("/api/graphs/<name>/resolve", methods=["GET"])
def api_resolve_graph(name):
    """Resolve a graph's recipe bindings into an immutable session snapshot."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Graph '{name}' not found"}), 404
    graph_data = load_yaml_file(path)
    if classify_procedure(graph_data) != "graph":
        return jsonify({"error": f"'{name}' is not an orchestration graph"}), 400

    recipe_id = request.args.get("recipe", graph_data.get("default_recipe", ""))
    recipe_path = _resolve_recipe_path(recipe_id)
    if not recipe_path:
        return jsonify({"error": f"Recipe '{recipe_id}' not found"}), 404
    recipe = load_yaml_file(recipe_path)
    validation = validate_recipe(recipe)
    if not validation["valid"]:
        return jsonify({"error": "Recipe validation failed", "validation": validation}), 422

    context = {
        "recipe": recipe,
        "constants": graph_data.get("constants", {}),
    }
    resolved = _resolve_bindings(_graph_api_model(graph_data), context)
    return jsonify({
        "name": name,
        "recipe_id": recipe_id,
        "recipe": deepcopy(recipe),
        "global_values": recipe_global_values(recipe),
        "data": resolved,
    })


@app.route("/api/procedures", methods=["POST"])
def api_create_procedure():
    """Create a new procedure."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    name = data.get("name", data.get("phase", ""))
    if not name:
        return jsonify({"error": "Procedure name required"}), 400

    if not re.match(r'^[a-z_][a-z0-9_-]*$', name):
        return jsonify({"error": "Name must be snake_case or kebab-case"}), 400

    path = Path(PROCEDURES_DIR) / f"{name}.yml"
    if path.exists():
        return jsonify({"error": f"Procedure '{name}' already exists"}), 409

    fmt = classify_procedure(data)
    validation = validate_procedure(data, SCHEMA, fmt, graph_schema=GRAPH_SCHEMA)
    if not validation.get("valid", False):
        return jsonify({"error": "Validation failed", "validation": validation}), 422
    save_yaml_file(path, data)
    return jsonify({"name": name, "file": path.name, "created": True}), 201


@app.route("/api/procedures/<name>", methods=["PUT"])
def api_update_procedure(name):
    """Update an existing procedure."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Procedure '{name}' not found"}), 404

    existing = load_yaml_file(path) or {}
    merged = deepcopy(existing)
    merged.update(data)
    data = merged

    # Normalize: ensure name/phase and error/error_handler are consistent
    if "name" in data:
        data.setdefault("phase", data["name"])
    elif "phase" in data:
        data.setdefault("name", data["phase"])

    if "error" in data:
        data.setdefault("error_handler", data["error"])
    elif "error_handler" in data:
        data.setdefault("error", data["error_handler"])

    fmt = classify_procedure(data)
    validation = validate_procedure(data, SCHEMA, fmt, graph_schema=GRAPH_SCHEMA)
    if not validation.get("valid", False):
        return jsonify({"error": "Validation failed", "validation": validation}), 422

    backup_dir = path.parent / ".backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.{datetime.now().strftime('%Y%m%d%H%M%S')}{path.suffix}"
    save_yaml_file(backup_path, existing)
    save_yaml_file(path, data)
    return jsonify({"name": name, "file": path.name, "updated": True})


@app.route("/api/procedures/<name>", methods=["DELETE"])
def api_delete_procedure(name):
    """Delete a procedure."""
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Procedure '{name}' not found"}), 404
    path.unlink()
    return jsonify({"deleted": True}), 204


@app.route("/api/procedures/<name>/validate", methods=["POST"])
def api_validate_procedure(name):
    """Validate a procedure against schema."""
    body = request.get_json(silent=True)
    if body and "data" in body:
        data = body["data"]
    else:
        path = _resolve_procedure_path(name)
        if not path:
            return jsonify({"error": f"Procedure '{name}' not found"}), 404
        data = load_yaml_file(path)

    fmt = classify_procedure(data)
    result = validate_procedure(data, SCHEMA, fmt, graph_schema=GRAPH_SCHEMA)
    return jsonify(result)


@app.route("/api/procedures/<name>/execute", methods=["POST"])
def api_execute_procedure(name):
    """Start simulated execution of a procedure."""
    with _runners_lock:
        if name in _runners and _runners[name].running:
            return jsonify({"error": "Procedure already running"}), 409

    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Procedure '{name}' not found"}), 404

    data = load_yaml_file(path)
    fmt = classify_procedure(data)

    if fmt != "state_machine":
        return jsonify({"error": "Only state_machine procedures can be executed"}), 400

    # Load constants if import is specified
    constants = {}
    import_file = data.get("import", data.get("import_file", ""))
    if import_file:
        const_path = Path(PROCEDURES_DIR) / import_file
        if not const_path.exists():
            const_path = BACKEND_DIR / import_file
        if const_path.exists():
            constants = load_yaml_file(const_path)

    # Get legacy overrides and the immutable selected-recipe snapshot.
    body = request.get_json(silent=True) or {}
    overrides = body.get("overrides", {})
    global_values = {}
    recipe_id = body.get("recipe_id")
    if recipe_id:
        recipe_path = _resolve_recipe_path(recipe_id)
        if not recipe_path:
            return jsonify({"error": f"Recipe '{recipe_id}' not found"}), 404
        recipe = load_yaml_file(recipe_path)
        validation = validate_recipe(recipe)
        if not validation["valid"]:
            return jsonify({"error": "Recipe validation failed", "validation": validation}), 422
        global_values = recipe_global_values(recipe)
    with _runners_lock:
        runner = ProcedureRunner(data, constants, global_values)
        runner.parameters.update(overrides)
        _runners[name] = runner

    runner.start()
    return jsonify({"started": True, "procedure": name})


@app.route("/api/procedures/<name>/state", methods=["GET"])
def api_get_execution_state(name):
    """Get current execution state."""
    with _runners_lock:
        runner = _runners.get(name)
    if not runner:
        return jsonify({"error": "No execution in progress"}), 404
    return jsonify(runner.get_state())


@app.route("/api/procedures/<name>/interrupt", methods=["POST"])
def api_interrupt_procedure(name):
    """Interrupt execution."""
    with _runners_lock:
        runner = _runners.get(name)
    if not runner:
        return jsonify({"error": "No execution in progress"}), 404
    runner.stop()
    return jsonify({"interrupted": True})


@app.route("/api/procedures/<name>/actions/<action>", methods=["POST"])
def api_procedure_action(name, action):
    """Execute an editor action on a procedure."""
    supported_actions = ["add_state", "remove_state", "connect_transition",
                         "set_parameter", "inject_action", "modify_state"]
    if action not in supported_actions:
        return jsonify({"error": f"Unsupported action: {action}. Supported: {supported_actions}"}), 400

    body = request.get_json(silent=True) or {}
    payload = body.get("payload", {})
    path = _resolve_procedure_path(name)
    if not path:
        return jsonify({"error": f"Procedure '{name}' not found"}), 404
    data = load_yaml_file(path)

    if action == "add_state":
        state_name = payload.get("state_name")
        if not state_name:
            return jsonify({"error": "state_name required"}), 400
        state_def = payload.get("state_definition", {"description": "", "action": [], "transition": [{"default": "loops"}], "timeout_s": "30s"})
        data.setdefault("states", {})[state_name] = state_def
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "State added", "state": state_name})

    elif action == "remove_state":
        state_name = payload.get("state_name")
        states = data.get("states", {})
        if not isinstance(states, dict) or state_name not in states:
            return jsonify({"error": f"State '{state_name}' not found"}), 404
        del states[state_name]
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "State removed", "state": state_name})

    elif action == "modify_state":
        state_name = payload.get("state_name")
        updates = payload.get("updates", {})
        states = data.get("states", {})
        if not isinstance(states, dict) or state_name not in states:
            return jsonify({"error": f"State '{state_name}' not found"}), 404
        states[state_name].update(updates)
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "State modified", "state": state_name})

    elif action == "set_parameter":
        param_name = payload.get("parameter_name")
        param_value = payload.get("value")
        data.setdefault("parameters", {})[param_name] = param_value
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "Parameter set", "parameter": param_name, "value": param_value})

    elif action == "inject_action":
        state_name = payload.get("state_name")
        action_def = payload.get("action_definition")
        if not action_def:
            return jsonify({"error": "action_definition required"}), 400
        states = data.get("states", {})
        if not isinstance(states, dict) or state_name not in states:
            return jsonify({"error": f"State '{state_name}' not found"}), 404
        states[state_name].setdefault("action", []).append(action_def)
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "Action injected", "state": state_name})

    elif action == "connect_transition":
        from_state = payload.get("from_state")
        to_state = payload.get("to_state")
        condition = payload.get("condition", "")
        states = data.get("states", {})
        if not isinstance(states, dict) or from_state not in states:
            return jsonify({"error": f"State '{from_state}' not found"}), 404
        trans_list = states[from_state].setdefault("transition", [])
        if condition:
            trans_list.append({"condition": condition, "then": to_state})
        else:
            trans_list.append({"default": to_state})
        failure = persist_validated(path, data)
        if failure:
            return failure
        return jsonify({"result": "Transition connected", "from": from_state, "to": to_state})

    return jsonify({"error": "Unhandled action"}), 400


# ─── Reference Data Endpoints ──────────────────────────────────────────────

@app.route("/api/sensors", methods=["GET"])
def api_list_sensors():
    return jsonify({"sensors": KNOWN_SENSORS})


@app.route("/api/devices", methods=["GET"])
def api_list_devices():
    return jsonify({"devices": KNOWN_DEVICES})


@app.route("/api/actions", methods=["GET"])
def api_list_actions():
    return jsonify({"actions": sorted(VALID_ACTION_KEYS)})


@app.route("/api/schema", methods=["GET"])
def api_get_schema():
    """Get the procedure schema."""
    return jsonify(SCHEMA)


def _runtime_bundle(workflow_name, recipe_id):
    workflow_path = _resolve_procedure_path(workflow_name)
    if not workflow_path:
        raise RuntimeEngineError(f"Workflow '{workflow_name}' not found")
    workflow = load_yaml_file(workflow_path)
    if classify_procedure(workflow) != "graph" or not isinstance(workflow.get("steps"), list):
        raise RuntimeEngineError(f"Workflow '{workflow_name}' is not an ordered workflow")

    recipe_id = recipe_id or workflow.get("default_recipe")
    recipe_path = _resolve_recipe_path(recipe_id) if recipe_id else None
    if not recipe_path:
        raise RuntimeEngineError(f"Recipe '{recipe_id}' not found")
    recipe = load_yaml_file(recipe_path)
    validation = validate_recipe(recipe)
    if not validation["valid"]:
        raise RuntimeEngineError("Recipe validation failed: " + "; ".join(validation["errors"]))

    procedure_names = []
    for step in workflow["steps"]:
        if not isinstance(step, dict):
            continue
        if step.get("procedure"):
            procedure_names.append(step["procedure"])
        for branch in step.get("parallel", {}).get("branches", []):
            if isinstance(branch, dict) and branch.get("procedure"):
                procedure_names.append(branch["procedure"])
    procedures = {}
    for name in dict.fromkeys(procedure_names):
        path = _resolve_procedure_path(name)
        if not path:
            raise RuntimeEngineError(f"Procedure '{name}' not found")
        procedure = load_yaml_file(path)
        result = validate_procedure(procedure, SCHEMA, classify_procedure(procedure), graph_schema=GRAPH_SCHEMA)
        if not result["valid"]:
            raise RuntimeEngineError(f"Procedure '{name}' is invalid: {'; '.join(result['errors'])}")
        procedures[name] = procedure
    return workflow, procedures, recipe_global_values(recipe)


def _is_loopback_hostname(hostname):
    """Return whether a URL hostname identifies the local loopback host."""
    if not hostname:
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _origin_matches_request_host(origin):
    """Accept an exact host match or equivalent localhost/loopback aliases."""
    origin_host = urlparse(origin).hostname
    request_host = urlparse("//" + request.host).hostname
    if not origin_host or not request_host:
        return False
    if origin_host.rstrip(".").lower() == request_host.rstrip(".").lower():
        return True
    return _is_loopback_hostname(origin_host) and _is_loopback_hostname(request_host)


def _require_local_runtime_command():
    """Limit state-changing runtime calls to the Brewie host UI or localhost."""
    origin = request.headers.get("Origin")
    if origin:
        if not _origin_matches_request_host(origin):
            return jsonify({"error": "Runtime commands require the Brewie same-host UI"}), 403
    elif request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "Non-browser runtime commands are local-only"}), 403
    return None


@app.route("/api/runtime/sessions", methods=["POST"])
def api_start_runtime_session():
    """Start the authoritative workflow engine in simulation or hardware mode."""
    denied = _require_local_runtime_command()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "simulation")
    if mode == "hardware" and body.get("confirm_hardware") is not True:
        return jsonify({"error": "Hardware mode requires confirm_hardware=true"}), 400
    try:
        workflow, procedures, globals_snapshot = _runtime_bundle(
            body.get("workflow", "beer_brewing"), body.get("recipe_id")
        )
        session = RUNTIME_MANAGER.start(
            workflow, procedures, globals_snapshot,
            mode=mode, speed=body.get("speed", 60),
        )
        return jsonify({"data": session.snapshot()}), 201
    except RuntimeEngineError as error:
        status = 409 if "already active" in str(error) else 503 if "not connected" in str(error) else 400
        return jsonify({"error": str(error)}), status


@app.route("/api/runtime/session", methods=["GET"])
def api_get_runtime_session():
    try:
        return jsonify({"data": RUNTIME_MANAGER.require().snapshot()})
    except RuntimeEngineError as error:
        return jsonify({"error": str(error)}), 404


@app.route("/api/runtime/session/control", methods=["POST"])
def api_control_runtime_session():
    denied = _require_local_runtime_command()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    try:
        session = RUNTIME_MANAGER.require()
        if action == "pause":
            session.pause()
        elif action == "resume":
            session.resume()
        elif action == "abort":
            session.abort()
        elif action == "set_speed":
            session.set_speed(body.get("speed"))
        else:
            return jsonify({"error": "Action must be pause, resume, abort, or set_speed"}), 400
        return jsonify({"data": session.snapshot()})
    except RuntimeEngineError as error:
        return jsonify({"error": str(error)}), 409


@app.route("/api/runtime/session/input", methods=["POST"])
def api_runtime_input():
    denied = _require_local_runtime_command()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        session = RUNTIME_MANAGER.require()
        session.provide_input(body.get("key"), body.get("value"), body.get("procedure"))
        return jsonify({"data": session.snapshot()})
    except RuntimeEngineError as error:
        return jsonify({"error": str(error)}), 409


@app.route("/api/runtime/session/navigate", methods=["POST"])
def api_runtime_navigate():
    denied = _require_local_runtime_command()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    direction = body.get("direction")
    if direction not in {"previous", "next"}:
        return jsonify({"error": "Direction must be previous or next"}), 400
    try:
        session = RUNTIME_MANAGER.require()
        session.navigate(direction)
        return jsonify({"data": session.snapshot()})
    except RuntimeEngineError as error:
        return jsonify({"error": str(error)}), 409


@app.route("/api/machine/status", methods=["GET"])
def api_machine_status():
    """Return active runtime HAL state, or AVR state when no simulation exists."""
    session = RUNTIME_MANAGER.session
    status = session.hal.status() if session and session.mode == "simulation" else AVR_BRIDGE.status()
    return jsonify({"data": status})


@app.route("/api/machine/command", methods=["POST"])
def api_machine_command():
    """Compile a semantic Brewmaster command into a whitelisted AVR command."""
    origin = request.headers.get("Origin")
    if origin:
        if not _origin_matches_request_host(origin):
            return jsonify({"error": "Machine commands require the Brewie same-host UI"}), 403
    elif request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "Non-browser machine commands are local-only"}), 403
    command = request.get_json(silent=True)
    if not isinstance(command, dict):
        return jsonify({"error": "A JSON command object is required"}), 400
    session = RUNTIME_MANAGER.session
    hal = session.hal if session and session.mode == "simulation" else None
    if hal is None and not AVR_BRIDGE.status()["connected"]:
        return jsonify({"error": "AVR serial status is not available"}), 503
    try:
        operation = command.get("command")
        device = command.get("device")
        action = command.get("action", "toggle")
        if operation == "close_all":
            (hal.close_all if hal else AVR_BRIDGE.close_all)()
        elif operation == "reset_level":
            (hal.reset_level if hal else AVR_BRIDGE.reset_level)()
        elif device in {"mash_heater", "boil_heater"}:
            if "target_C" not in command:
                return jsonify({"error": "Heater commands require target_C"}), 400
            (hal.set_heater_target if hal else AVR_BRIDGE.set_heater_target)(device, command["target_C"])
        elif device == "cool_valve":
            if hal:
                hal.set_device("cooling_water_inlet_valve", action)
                hal.set_device("wort_cooling_valve", action)
            else:
                AVR_BRIDGE.set_cooling_path(action)
        elif isinstance(device, str):
            (hal.set_device if hal else AVR_BRIDGE.set_device)(device, action)
        else:
            return jsonify({"error": "Unknown machine command"}), 400
    except (AvrSerialError, RuntimeEngineError) as error:
        return jsonify({"error": str(error)}), 502
    return jsonify({"data": hal.status() if hal else AVR_BRIDGE.status()})


@app.route("/api/health", methods=["GET"])
def api_health():
    avr_status = AVR_BRIDGE.status()
    runtime = RUNTIME_MANAGER.session
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "recipes": len(_recipe_files()),
        "avr": {
            "enabled": AVR_ENABLED,
            "connected": avr_status["connected"],
            "safeStart": AVR_SAFE_START,
            "safeStartComplete": avr_status["safeStartComplete"],
            "initializationConfigured": avr_status["initializationConfigured"],
            "initializationComplete": avr_status["initializationComplete"],
            "calibrationFile": avr_status["calibrationFile"],
            "device": AVR_DEVICE,
            "error": avr_status["lastError"],
        },
        "runtime": None if runtime is None else {
            "id": runtime.id,
            "mode": runtime.mode,
            "status": runtime.status,
            "activeProcedure": runtime.snapshot()["active_procedure"],
        },
    })


# ─── UI State ───────────────────────────────────────────────────────────────

def load_ui_state():
    if os.path.exists(UI_STATE_FILE):
        try:
            with open(UI_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"action_log": [], "execution_state": {}, "selection": {}}


def save_ui_state(state):
    os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)
    with open(UI_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_action(action, detail, success=True):
    state = load_ui_state()
    state["action_log"].insert(0, {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "detail": detail,
        "success": success
    })
    state["action_log"] = state["action_log"][:50]
    save_ui_state(state)


@app.route("/api/ui-state", methods=["GET"])
def api_get_ui_state():
    return jsonify(load_ui_state())


@app.route("/api/ui-state", methods=["POST"])
def api_update_ui_state():
    state = request.get_json()
    if not state:
        return jsonify({"error": "No data provided"}), 400
    current = load_ui_state()
    current.update(state)
    save_ui_state(current)
    return jsonify(current)


# ─── SSE for live updates ──────────────────────────────────────────────────

@app.route("/api/procedures/<name>/live")
def api_live(name):
    """Server-Sent Events endpoint for real-time execution updates."""
    def event_stream():
        runner = _runners.get(name)
        if not runner:
            yield f"data: {json.dumps({'error': 'No execution in progress'})}\n\n"
            return
        while True:
            runner = _runners.get(name)
            if not runner:
                yield f"data: {json.dumps({'error': 'Execution ended'})}\n\n"
                break
            state = runner.get_state()
            yield f"data: {json.dumps(state)}\n\n"
            time.sleep(1)

    return Response(event_stream(), mimetype="text/event-stream")


# ─── Static File Serving ───────────────────────────────────────────────────

@app.route("/editor/")
def serve_editor():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/editor/<path:filename>")
def serve_editor_static(filename):
    return send_from_directory(str(FRONTEND_DIR), filename)


if __name__ == "__main__":
    # Allow overriding via environment variables for flexibility
    port = int(os.environ.get("EDITOR_PORT", "8080"))
    host = os.environ.get("EDITOR_HOST", "0.0.0.0")
    print(f"Starting Brewie Procedure Editor Backend")
    print(f"  Procedures: {PROCEDURES_DIR}")
    print(f"  Schema:     {SCHEMA_PATH}")
    print(f"  Recipes:    {RECIPES_DIR}")
    print(f"  Frontend:   {FRONTEND_DIR}")
    print(f"  UI State:   {UI_STATE_FILE}")
    debug = os.environ.get("EDITOR_DEBUG", "0").lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, threaded=True)
