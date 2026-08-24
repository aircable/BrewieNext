#!/usr/bin/env python3
"""
Shared Brewie Procedure Validation Module

Single source of truth for all procedure validation logic.
Imported by both the CLI validator (validate_procedures.py) and the
Flask backend (editor_backend.py) to prevent divergent validation rules.

Extracted in Phase 2 of the brewie-procedure-cleanup-plan.
"""
import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set

# AST node types allowed in sandboxed condition expressions.
# Anything not in this set is rejected with a SecurityError,
# preventing attribute access, imports, subscripts, lambdas, etc.
_ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,          # and, or
    ast.BinOp,           # +, -, *, /, //, %, **
    ast.UnaryOp,         # not, -x, +x
    ast.Compare,         # <, >, <=, >=, ==, !=, in, not in
    ast.Name,            # variable references
    ast.Constant,        # numbers, strings, booleans, None
    ast.Call,            # function calls (only to registered functions)
    ast.List,            # [1, 2, 3]
    ast.Tuple,           # (1, 2, 3)
    ast.Load,            # load context
)

# BinOp operator types we permit
_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

# UnaryOp operator types we permit
_ALLOWED_UNARYOPS = {
    ast.Not: lambda a: not a,
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
}

# Compare operator types we permit
_ALLOWED_CMPOPS = {
    ast.Lt: lambda a, b: a < b,
    ast.Gt: lambda a, b: a > b,
    ast.LtE: lambda a, b: a <= b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

try:
    import yaml
except ImportError:
    yaml = None

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None


# ─── ValidationIssue ────────────────────────────────────────────────────────────

class ValidationIssue:
    """Represents a validation issue with severity and message."""
    def __init__(self, severity, message):
        self.severity = severity  # 'error' or 'warning'
        self.message = message

    def __repr__(self):
        return f"ValidationIssue({self.severity}, {self.message!r})"


# ─── Constants ─────────────────────────────────────────────────────────────────

VALID_ACTION_KEYS = {
    "read_sensor", "read", "set_valve", "set_pump", "set_heater",
    "open_valve_for_seconds", "start_weight_monitoring",
    "notify_user", "log_event", "log", "wait_for_user_input",
    "check_mutex", "acquire_mutex", "release_mutex", "enable_pid",
    "disable_pid", "wait", "stop_weight_monitoring",
    "release_scheduled_hops", "run_hop_stage", "finish_avr_step_session",
    "set_counter", "increment_counter",
}

KNOWN_SENSORS = [
    "weight_boil_tank", "weight_mash_tank", "weight_source_tank",
    "weight_destination_tank", "temp_boil_tank", "temp_mash_tank",
    "temp_water_manual", "pump_tacho", "pump_current",
    "mash_pump_diagnostic", "boil_pump_diagnostic",
    "water_volume", "mash_pump_tacho", "boil_pump_tacho",
    "mash_pump_diagnostics", "boil_pump_diagnostics",
    "weight-boil-tank", "weight-mash-tank", "weight-source-tank",
    "weight-destination-tank", "temp-boil-tank", "temp-boil-water",
    "temp-mash-tank", "temp-water-manual", "pump_tacho",
    "pump-current", "elapsed_time", "valves_configured",
    "ro_water_flow_rate", "system_pressure",
    "temperature_boil_tank", "temperature_mash_water",
    "temperature_mash_tank",
    "temperatureAmbientBoilTank", "temperatureAmbientMashIn",
    "temperatureAmbientMashOut", "temperatureAmbientWaterFeed",
    "temperatureAmbientPump", "temperatureAmbientHeaterBox",
    "tachoBoilPump", "tachoMashPump",
    "weightBoilTank", "weightMashTank",
    "valve1_state", "valve2_state", "valve3_state",
    "valve4_state", "valve5_state", "valve6_state",
    "pump_boil", "pump_mash",
    "manual_fill", "water_fill_method",
    "previous_weight_destination",
]

KNOWN_DEVICES = [
    "mash_heater", "boil_heater", "mash-heater", "boil-heater",
    "water_inlet", "water_inlet_valve",
    "boil_inlet_valve", "mash_inlet_valve",
    "boil_return_valve", "mash_return_valve",
    "cooling_valve", "cool_out_valve", "outlet_valve",
    "boil_pump", "mash_pump", "boil-pump", "mash-pump", "pump",
    "heater_power_grid", "mash_tank_valve", "boil_tank_valve",
    "hop1", "hop2", "hop3", "hop4",
]

# Reserved transition targets that are not local state names
RESERVED_TRANSITION_TARGETS = {
    "error_handler", "next_phase", "exit", "halt", "complete",
    "error", "done", "step_complete", "loops",
}

# Error-handler-like targets that are external (defined in parent orchestrator)
# These start with "error-" and are resolved by the orchestrator, not locally.
ERROR_PREFIX = "error-"


# ─── Normalizers ───────────────────────────────────────────────────────────────

def normalize_name(data):
    """Extract the procedure name from either 'phase' (v2) or 'name' (v1) field."""
    return data.get("name") or data.get("phase", "")


def normalize_error_handler(data):
    """Extract the error handler from either 'error_handler' (v1) or 'error' (v2) field."""
    return data.get("error_handler") or data.get("error", "")


def get_start_state(data):
    """Derive start_state: use explicit field, or first state in the states dict.

    Supports both v1 (explicit start_state/startState) and v2 (implicit:
    starts at first key of states dict) formats.
    """
    explicit = data.get("start_state") or data.get("startState")
    if explicit:
        return explicit
    states = data.get("states", {})
    if isinstance(states, dict) and states:
        return next(iter(states))
    elif isinstance(states, list) and states:
        first = states[0]
        return first.get("name") or first.get("state", "")
    return ""


# ─── Classification ────────────────────────────────────────────────────────────

def classify_procedure(data):
    """Classify a loaded YAML dict into one of:
    state_machine, state_machine_list, graph, constants, invalid, unknown.

    Unified logic: checks for graph format (entry_point + edges/phases),
    state machine (states as dict), list-based states (legacy),
    or constants (consts/constants keys).
    """
    if not isinstance(data, dict):
        return "invalid"
    # Canonical ordered orchestration. The API may derive visual nodes and
    # edges from this list, but they are not authored in the source document.
    if isinstance(data.get("steps"), list) and normalize_name(data):
        return "graph"
    # Legacy graph format: has entry_point + phases or edges
    if "entry_point" in data and ("phases" in data or "edges" in data):
        return "graph"
    if "entry_point" in data and "edges" in data:
        return "graph"
    # State machine format (primary): states as dict (v1 with start_state, or v2 with name/phase)
    if "states" in data and isinstance(data["states"], dict):
        if get_start_state(data) or normalize_name(data):
            return "state_machine"
    # Legacy list-based states
    if "states" in data and isinstance(data["states"], list):
        return "state_machine_list"
    # Constants
    if "consts" in data or "constants" in data:
        return "constants"
    # Has 'phase' or 'name' with transitions but no states dict
    if normalize_name(data) and ("phases" in data or "transition" in data):
        if "phases" in data:
            return "graph"
    # Fallback: has states as list
    if "states" in data and isinstance(data["states"], list):
        return "state_machine_list"
    return "unknown"


# ─── Custom Checks ─────────────────────────────────────────────────────────────

def get_error_handler(data):
    """Get error handler from either 'error' (v2) or 'error_handler' (v1) field."""
    return data.get("error_handler") or data.get("error", "")


def is_external_target(target, states):
    """Check if a transition target is a valid local state, reserved, or external."""
    return (
        target in states
        or target in RESERVED_TRANSITION_TARGETS
        or target.startswith(ERROR_PREFIX)
    )


def custom_checks(data, filepath=None):
    """Semantic checks that JSON Schema alone cannot express.

    Returns a list of ValidationIssue objects, each with a severity
    ('error' or 'warning') and a message.
    """
    issues = []
    states = data.get("states", {})
    states_dict = states if isinstance(states, dict) else {}

    # --- Error handler ---
    eh = get_error_handler(data)
    if not eh:
        issues.append(ValidationIssue("error", "Missing 'error' or 'error_handler' field"))
    elif isinstance(states_dict, dict) and states_dict and eh not in states_dict:
        # Error handler may be external (defined in parent orchestrator) — warn only
        issues.append(ValidationIssue(
            "warning",
            f"error handler '{eh}' not in local states (may be defined in parent orchestrator)"
        ))

    # Procedure cleanup is state-scoped so it remains visible and executes on
    # every departure path. Procedure-level finalize blocks are unsupported.
    if "finalize" in data:
        issues.append(ValidationIssue(
            "error", "Procedure-level 'finalize' is unsupported; use state 'on_exit'"
        ))

    # --- Phase without name (informational warning only) ---
    if "phase" in data and "name" not in data:
        issues.append(ValidationIssue(
            "warning",
            "Uses 'phase' field without 'name' — both are accepted (dual-format support)"
        ))

    # --- start_state must reference a real local state ---
    start = data.get("start_state") or data.get("startState")
    if start and isinstance(states_dict, dict) and states_dict and start not in states_dict:
        issues.append(ValidationIssue(
            "error",
            f"start_state '{start}' not found in states: {sorted(states_dict.keys())}"
        ))

    # --- Transition target references ---
    if isinstance(states_dict, dict):
        for state_name, state_def in states_dict.items():
            if not isinstance(state_def, dict):
                continue

            # Check transition list (v2 format: list of {condition_expr: target} dicts)
            trans = state_def.get("transition", [])
            if isinstance(trans, list):
                for i, item in enumerate(trans):
                    if not isinstance(item, dict):
                        continue
                    if "default" in item:
                        continue
                    if "condition" in item and "then" in item:
                        target = item["then"]
                        if isinstance(target, str) and not is_external_target(target, states_dict):
                            issues.append(ValidationIssue(
                                "warning",
                                f"State '{state_name}': transition[{i}] targets '{target}' "
                                f"which is not in local states (may be external)"
                            ))
                    elif len(item) == 1:
                        cond, target = next(iter(item.items()))
                        if isinstance(target, str) and not is_external_target(target, states_dict):
                            issues.append(ValidationIssue(
                                "warning",
                                f"State '{state_name}': transition[{i}] targets '{target}' "
                                f"which is not in local states (may be external)"
                            ))

            # Check legacy transitions list format (v1: [{if: expr, then: target}, ...])
            trans_legacy = state_def.get("transitions", [])
            if isinstance(trans_legacy, list):
                for i, trans in enumerate(trans_legacy):
                    if isinstance(trans, dict) and "then" in trans:
                        target = trans["then"]
                        if isinstance(target, str) and not is_external_target(target, states_dict):
                            issues.append(ValidationIssue(
                                "warning",
                                f"State '{state_name}': transitions[{i}] targets '{target}' "
                                f"which is not in local states (may be external)"
                            ))

            # Check action keys
            for i, action in enumerate(state_def.get("action", [])):
                if isinstance(action, dict):
                    key = list(action.keys())[0]
                    if key not in VALID_ACTION_KEYS:
                        issues.append(ValidationIssue(
                            "warning",
                            f"State '{state_name}': action[{i}] uses unrecognized key '{key}'"
                        ))
                    elif key == "run_hop_stage":
                        config = action[key]
                        required = {
                            "open_cages", "start_minutes_remaining",
                            "end_minutes_remaining", "session",
                        }
                        if not isinstance(config, dict):
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': run_hop_stage must be a mapping",
                            ))
                            continue
                        missing = sorted(required - set(config))
                        if missing:
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': run_hop_stage missing {missing}",
                            ))
                        cages = config.get("open_cages")
                        if not isinstance(cages, list) or not cages or cages != list(range(1, len(cages) + 1)):
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': run_hop_stage open_cages must be cumulative from cage 1",
                            ))
                        if config.get("session") not in {"start", "continue"}:
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': run_hop_stage session must be start or continue",
                            ))
                    elif key == "finish_avr_step_session":
                        config = action[key]
                        if not isinstance(config, dict) or not isinstance(config.get("operation"), str):
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': finish_avr_step_session requires an operation",
                            ))

            # Check on_exit actions
            for i, action in enumerate(state_def.get("on_exit", [])):
                if isinstance(action, dict):
                    key = list(action.keys())[0]
                    if key not in VALID_ACTION_KEYS:
                        issues.append(ValidationIssue(
                            "warning",
                            f"State '{state_name}': on_exit[{i}] uses unrecognized key '{key}'"
                        ))

            # Check loops for recognized keys (from old validate_procedures.py)
            loops = state_def.get("loops", [])
            if isinstance(loops, list):
                recognized_loop_keys = {
                    "read_sensor", "read", "wait", "log", "log_event",
                    "notify_user", "notify", "if", "if_timeout_exceeded",
                    "if_timeout", "if_temp_too_high", "then",
                    "condition", "next_state",
                }
                for i, loop in enumerate(loops):
                    if isinstance(loop, dict):
                        loop_keys = set(loop.keys())
                        if not loop_keys.intersection(recognized_loop_keys):
                            issues.append(ValidationIssue(
                                "error",
                                f"State '{state_name}': loop item {i} has unrecognized keys: {list(loop.keys())}"
                            ))

    return issues


def validate_file(data, schema, filepath, proc_dir):
    """Validate a single loaded procedure file.

    Returns (status_string, errors_list, warnings_list).

    Status is one of:
      COMPLIANT, COMPLIANT_WITH_WARNINGS, SCHEMA_VIOLATIONS,
      GRAPH_SCHEMA_VIOLATIONS, LEGACY_FORMAT, UNKNOWN_FORMAT,
      PARSE_ERROR, LOAD_ERROR, GRAPH_FORMAT
    """
    if not isinstance(data, dict):
        return "UNKNOWN_FORMAT", [f"Cannot classify format (data: {data})"], []

    # Handle YAML/parse errors
    if "__yaml_error__" in data:
        return "PARSE_ERROR", [data["__yaml_error__"]], []
    if "__load_error__" in data:
        return "LOAD_ERROR", [data["__load_error__"]], []

    fmt = classify_procedure(data)

    if fmt == "constants":
        return "OK (constants)", [], []

    if fmt == "graph":
        graph_schema_path = proc_dir / "procedure-graph.schema.json"
        if graph_schema_path and Path(graph_schema_path).exists():
            graph_schema = _load_json_schema(Path(graph_schema_path))
            if graph_schema and Draft7Validator:
                graph_errors = sorted(
                    Draft7Validator(graph_schema).iter_errors(data),
                    key=lambda e: list(e.path)
                )
                errors = [str(e.message) for e in graph_errors]
                if errors:
                    return "GRAPH_SCHEMA_VIOLATIONS", errors, []
                return "GRAPH_FORMAT", [], []
        return "GRAPH_FORMAT", ["Graph schema not found - skipping validation"], []

    if fmt == "state_machine_list":
        return "LEGACY_FORMAT", ["Uses legacy list-based states format (schema targets dict-based states)"], []

    if fmt != "state_machine":
        return "UNKNOWN_FORMAT", [f"Cannot classify format (keys: {list(data.keys())})"], []

    # Primary: JSON Schema validation
    schema_errors = []
    if Draft7Validator and schema:
        schema_errors = sorted(
            Draft7Validator(schema).iter_errors(data),
            key=lambda e: list(e.path)
        )

    # Custom semantic checks
    issues = custom_checks(data, filepath)
    custom_errors = [i.message for i in issues if i.severity == "error"]
    custom_warnings = [i.message for i in issues if i.severity == "warning"]

    schema_error_msgs = [str(e.message) for e in schema_errors]
    all_errors = schema_error_msgs + custom_errors

    if all_errors:
        return "SCHEMA_VIOLATIONS", all_errors, custom_warnings
    if custom_warnings:
        return "COMPLIANT_WITH_WARNINGS", [], custom_warnings
    return "COMPLIANT", [], []


def _load_json_schema(path):
    """Load JSON schema from file path."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def validate_procedure(data, schema, format_type=None, graph_schema=None):
    """Validate a procedure dict against the JSON schema plus custom checks.

    Returns a dict: {"valid": bool, "errors": [...], "warnings": [...]}
    This is the interface used by the Flask backend's /validate endpoint.
    """
    if format_type is None:
        format_type = classify_procedure(data)

    errors = []
    warnings = []

    if format_type == "constants":
        return {"valid": True, "errors": [], "warnings": []}

    if format_type == "graph":
        schema_to_use = graph_schema
        if Draft7Validator and schema_to_use:
            schema_errors = sorted(
                Draft7Validator(schema_to_use).iter_errors(data),
                key=lambda e: list(e.path)
            )
        else:
            schema_errors = []
        for e in schema_errors:
            field_path = ".".join(str(p) for p in e.path) or "root"
            errors.append(f"{field_path}: {e.message}")
    else:
        schema_errors = []
        if Draft7Validator and schema:
            schema_errors = sorted(
                Draft7Validator(schema).iter_errors(data),
                key=lambda e: list(e.path)
            )
        for e in schema_errors:
            field_path = ".".join(str(p) for p in e.path) or "root"
            errors.append(f"{field_path}: {e.message}")

    # Custom semantic checks
    if format_type == "state_machine":
        issues = custom_checks(data)
        for issue in issues:
            if issue.severity == "error":
                errors.append(issue.message)
            elif issue.severity == "warning":
                warnings.append(issue.message)

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}
    return {"valid": True, "errors": [], "warnings": warnings}


# ─── File-level helpers ─────────────────────────────────────────────────────────

def load_yaml(path):
    """Load a YAML file, returning data dict or error marker dict.

    Error marker dicts use the keys '__yaml_error__' (parse error) and
    '__load_error__' (file/OS error) so callers can distinguish failure modes.
    """
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        return {"__yaml_error__": str(e)}
    except Exception as e:
        return {"__load_error__": str(e)}


def classify_file(data):
    """Classify a loaded YAML dict, returning the same format string as
    classify_procedure(). Thin wrapper for callers that prefer a file-oriented
    name (maintained for API symmetry with the cleanup plan).
    """
    return classify_procedure(data)


# ─── Sensor Name Normalization ──────────────────────────────────────────
# Moves the hyphen-to-underscore regex from ProcedureRunner._eval_condition
# into the shared module so both the evaluator and validators use the same
# normalization (Phase 3).

# Regex: replace hyphens that sit between two lowercase letters.
# This converts sensor names like 'weight-boil-tank' -> 'weight_boil_tank'
# while preserving subtraction operators (e.g., 'temp - 2.0' stays intact
# because the hyphen is preceded by 'p' and followed by a space, not a letter).
_SENSOR_NAME_RE = re.compile(r'(?<=[a-z])-(?=[a-z])')


def normalize_sensor_name(expr: str) -> str:
    """Normalize hyphenated sensor names in an expression string to underscores.

    Only replaces hyphens between lowercase letters (e.g., 'weight-boil-tank' ->
    'weight_boil_tank'), preserving subtraction operators and hyphenated string
    keys used as named conditions (e.g., 'water hot').
    """
    return _SENSOR_NAME_RE.sub('_', expr)


# ─── Sandboxed Condition Evaluator ──────────────────────────────────────
# Phase 3: Replaces eval() with an AST-based evaluator that only permits
# arithmetic, comparisons, boolean operations, variables, and calls to
# explicitly-registered functions. Blocks attribute access, imports,
# subscripts, lambdas, comprehensions, assignments, and every other
# AST node type — making it safe against untrusted (e.g. AI-generated)
# condition expressions.

class ConditionEvalError(Exception):
    """Raised when a condition expression cannot be safely evaluated.

    Callers should treat this as 'condition not met' (return False),
    matching the previous behavior where eval() failures returned False.
    """


class _SafeEvalVisitor(ast.NodeVisitor):
    """Walks an AST and evaluates only safe node types.

    Any node type not in ``_ALLOWED_AST_NODES`` raises ``ConditionEvalError``,
    preventing code execution via attribute access, imports, etc.
    """

    def __init__(self, env: Dict[str, Any], functions: Dict[str, Any]):
        self.env = env
        self.functions = functions or {}

    # -- Node dispatch --

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BoolOp(self, node):
        results = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for r in results:
                if not r:
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for r in results:
                if r:
                    return True
            return False
        raise ConditionEvalError(f"Unsupported BoolOp: {type(node.op).__name__}")

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise ConditionEvalError(f"Unsupported BinOp: {type(node.op).__name__}")
        try:
            return op_func(left, right)
        except ZeroDivisionError:
            raise ConditionEvalError("Division by zero in condition")
        except TypeError as e:
            raise ConditionEvalError(f"Type error in condition: {e}")

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise ConditionEvalError(f"Unsupported UnaryOp: {type(node.op).__name__}")
        return op_func(operand)

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            op_func = _ALLOWED_CMPOPS.get(type(op))
            if op_func is None:
                raise ConditionEvalError(f"Unsupported Compare: {type(op).__name__}")
            try:
                if not op_func(left, right):
                    return False
            except TypeError as e:
                raise ConditionEvalError(f"Type error in condition: {e}")
            left = right
        return True

    def visit_Name(self, node):
        # Normalize hyphenated sensor names to underscore form for lookup.
        # The expression string was already normalized before parsing,
        # but we also try the raw name as a fallback.
        raw = node.id
        if raw in self.env:
            return self.env[raw]
        normalized = normalize_sensor_name(raw)
        if normalized in self.env:
            return self.env[normalized]
        raise ConditionEvalError(f"Undefined variable: '{raw}'")

    def visit_Constant(self, node):
        return node.value

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise ConditionEvalError("Only named function calls are allowed")
        func_name = node.func.id
        if func_name not in self.functions:
            raise ConditionEvalError(f"Undefined function: '{func_name}'")
        args = [self.visit(a) for a in node.args]
        # No kwargs support in conditions (yet)
        if node.keywords:
            raise ConditionEvalError("Keyword arguments in conditions are not supported")
        try:
            return self.functions[func_name](*args)
        except Exception as e:
            raise ConditionEvalError(f"Function '{func_name}' raised: {e}")

    def visit_List(self, node):
        return [self.visit(e) for e in node.elts]

    def visit_Tuple(self, node):
        return tuple(self.visit(e) for e in node.elts)

    # -- Reject everything else --

    def generic_visit(self, node):
        node_type = type(node).__name__
        if node_type not in _ALLOWED_AST_NODE_NAMES:
            raise ConditionEvalError(f"Disallowed AST node: {node_type}")
        super().generic_visit(node)


# Pre-computed set of allowed node type names for fast reject check
_ALLOWED_AST_NODE_NAMES = {
    n.__name__ for n in _ALLOWED_AST_NODES
}


def safe_eval_condition(
    expr: str,
    env: Dict[str, Any],
    functions: Optional[Dict[str, Any]] = None,
) -> bool:
    """Safely evaluate a condition expression.

    Uses Python's ``ast`` module to parse the expression, then evaluates only
    permitted node types (arithmetic, comparisons, boolean ops, constants,
    variable lookups, and registered function calls). Any disallowed construct
    raises ``ConditionEvalError`` and the function returns ``False``.

    This is a drop-in replacement for ``eval(expr, {"__builtins__": {}}, env)``
    that is actually sandboxed — attribute access, imports, subscripts, lambdas,
    comprehensions, assignments, etc. are all blocked at the AST level.

    Args:
        expr: A condition expression string (e.g., ``"temp_boil_tank >= 78.0"``).
        env: Name-to-value mapping (sensor readings, parameters, etc.).
        functions: Optional dict of registered function names to callables.

    Returns:
        ``True`` or ``False``. Returns ``False`` for any expression that
        cannot be evaluated (undefined names, template placeholders,
        syntax errors, etc.), preserving backward-compatible behavior.
    """
    if not isinstance(expr, str):
        return bool(expr)
    expr = expr.strip()

    # Semantic markers (no parsing needed)
    if expr == "step_complete":
        return True
    if expr == "loops":
        return True

    # Normalize hyphenated sensor names in the expression string BEFORE
    # parsing, so that e.g. 'weight-boil-tank' becomes 'weight_boil_tank'
    # (a valid Python identifier) rather than 'weight - boil - tank'.
    expr = normalize_sensor_name(expr)

    # Parse the expression into an AST. ast.parse with mode='eval'
    # only accepts a single expression (no statements), which already
    # rejects assignments, imports, etc. at the syntax level.
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        # Template placeholders ({{...}}), invalid duration strings (600s),
        # or other non-Python syntax fail here — return False, same as
        # the old eval()-based behavior.
        return False

    # Walk and evaluate with the sandbox
    visitor = _SafeEvalVisitor(env, functions)
    try:
        result = visitor.visit(tree)
        return bool(result)
    except ConditionEvalError:
        return False
    except Exception:
        # Catch-all for unexpected errors (TypeError, ValueError, etc.)
        return False
