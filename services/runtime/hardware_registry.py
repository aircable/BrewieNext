"""Load and resolve canonical Brewie hardware device identifiers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml
from jsonschema import Draft7Validator


class HardwareRegistryError(ValueError):
    """The hardware registry is missing, invalid, or ambiguous."""


def _default_registry_path() -> Path:
    configured = os.environ.get("BREWIE_HARDWARE_REGISTRY")
    if configured:
        return Path(configured)

    development_path = Path(__file__).resolve().parent.parent / "hardware-device-registry.yml"
    if development_path.exists():
        return development_path
    return Path(__file__).resolve().with_name("hardware-device-registry.yml")


def _schema_path(registry_path: Path) -> Path:
    adjacent = registry_path.with_name("hardware-device-registry.schema.json")
    if adjacent.exists():
        return adjacent
    return Path(__file__).resolve().with_name("hardware-device-registry.schema.json")


def validate_registry(data: Dict[str, Any], schema_path: Path) -> None:
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise HardwareRegistryError(detail)

    identifiers: Dict[str, str] = {}
    for canonical_id, device in data["devices"].items():
        for identifier in [canonical_id, *device.get("aliases", [])]:
            if identifier in identifiers:
                raise HardwareRegistryError(
                    f"Device identifier '{identifier}' is assigned to both "
                    f"'{identifiers[identifier]}' and '{canonical_id}'"
                )
            identifiers[identifier] = canonical_id

    for operation_id, operation in data["operations"].items():
        argument_names = set(operation["argument_order"])
        supplied_names = set(operation["fixed_arguments"]) | set(operation["dynamic_arguments"])
        if argument_names != supplied_names:
            missing = sorted(argument_names - supplied_names)
            extra = sorted(supplied_names - argument_names)
            raise HardwareRegistryError(
                f"Operation '{operation_id}' argument mapping mismatch: "
                f"missing={missing}, extra={extra}"
            )


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    registry_path = path or _default_registry_path()
    if not registry_path.exists():
        raise HardwareRegistryError(f"Hardware registry not found: {registry_path}")
    with registry_path.open(encoding="utf-8") as registry_file:
        data = yaml.safe_load(registry_file)
    if not isinstance(data, dict):
        raise HardwareRegistryError("Hardware registry root must be a mapping")
    validate_registry(data, _schema_path(registry_path))
    return data


class HardwareRegistry:
    """Read-only identifier and capability lookup for the HAL and validators."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = data or load_registry()
        self.devices: Dict[str, Dict[str, Any]] = self.data["devices"]
        self.operations: Dict[str, Dict[str, Any]] = self.data["operations"]
        self._identifiers: Dict[str, str] = {}
        for canonical_id, device in self.devices.items():
            self._identifiers[canonical_id] = canonical_id
            for alias in device.get("aliases", []):
                self._identifiers[alias] = canonical_id

    def canonical_id(self, identifier: str) -> str:
        try:
            return self._identifiers[identifier]
        except KeyError as error:
            raise HardwareRegistryError(f"Unknown hardware device '{identifier}'") from error

    def resolve(self, identifier: str, expected_kind: Optional[str] = None) -> Dict[str, Any]:
        canonical_id = self.canonical_id(identifier)
        device = self.devices[canonical_id]
        if expected_kind and device["kind"] != expected_kind:
            raise HardwareRegistryError(
                f"Device '{identifier}' resolves to '{canonical_id}', which is "
                f"a {device['kind']}, not a {expected_kind}"
            )
        return {"id": canonical_id, **device}

    def identifiers(self, kinds: Optional[Iterable[str]] = None) -> list[str]:
        accepted = set(kinds) if kinds else None
        return [
            device_id
            for device_id, device in self.devices.items()
            if accepted is None or device["kind"] in accepted
        ]

    def resolve_operation(self, operation_id: str) -> Dict[str, Any]:
        try:
            return {"id": operation_id, **self.operations[operation_id]}
        except KeyError as error:
            raise HardwareRegistryError(
                f"Unknown hardware operation '{operation_id}'"
            ) from error

    def compile_operation(
        self,
        operation_id: str,
        parameters: Dict[str, Any],
        *,
        runtime_step_id: int,
    ) -> Dict[str, Any]:
        """Compile a semantic operation into ordered AVR step arguments."""
        operation = self.resolve_operation(operation_id)
        required = set(operation["action_parameters"])
        missing = sorted(required - set(parameters))
        if missing:
            raise HardwareRegistryError(
                f"Operation '{operation_id}' missing action parameters: {missing}"
            )
        session = parameters.get("session")
        if session not in {"start", "continue"}:
            raise HardwareRegistryError(
                f"Operation '{operation_id}' has invalid session phase '{session}'"
            )

        values = dict(operation["fixed_arguments"])
        for target, binding in operation["dynamic_arguments"].items():
            source = binding["source"]
            if source == "runtime_step_id":
                value = runtime_step_id
            elif source == "remaining_interval_s":
                start = parameters.get("start_minutes_remaining")
                end = parameters.get("end_minutes_remaining")
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    raise HardwareRegistryError(
                        f"Operation '{operation_id}' requires resolved numeric hop times"
                    )
                value = (start - end) * 60
                if value <= 0:
                    raise HardwareRegistryError(
                        f"Operation '{operation_id}' requires a positive hop interval"
                    )
            elif source == "open_cages":
                value = int(binding["contains"] in parameters.get("open_cages", []))
            else:  # Protected by schema validation.
                raise HardwareRegistryError(
                    f"Operation '{operation_id}' has unsupported source '{source}'"
                )
            if not isinstance(value, (int, float, bool)):
                raise HardwareRegistryError(
                    f"Operation '{operation_id}' produced invalid '{target}' value: {value!r}"
                )
            values[target] = int(value)

        return {
            "command": operation["lifecycle"]["step_command"],
            "arguments": [values[name] for name in operation["argument_order"]],
            "before_command": (
                operation["lifecycle"]["start_command"] if session == "start" else None
            ),
            "lifecycle": dict(operation["lifecycle"]),
        }

    def compile_session_finish(self, operation_id: str) -> Dict[str, Any]:
        """Return the acknowledged command that normally ends an AVR step session."""
        operation = self.resolve_operation(operation_id)
        return {
            "command": operation["lifecycle"]["finish_command"],
            "wait_for_ack": True,
        }

    def compile_session_cancel(self, operation_id: str) -> Dict[str, Any]:
        """Return the fail-safe command used when an AVR step session is interrupted."""
        operation = self.resolve_operation(operation_id)
        return {
            "command": operation["lifecycle"]["cancel_command"],
            "wait_for_ack": True,
        }
