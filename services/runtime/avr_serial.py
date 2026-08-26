"""Brewie B20 AVR serial transport and machine-state projection.

The browser sends semantic device commands to the Flask backend.  Only this
module knows the legacy packet framing or accepts access to ``/dev/ttyS1``.
"""

import json
import math
import os
import select
import termios
import threading
import time
import fcntl
from pathlib import Path

from hardware_registry import HardwareRegistry, HardwareRegistryError


class AvrSerialError(RuntimeError):
    """The AVR transport rejected or could not acknowledge a command."""


def load_calibration(path):
    """Load the machine-specific values required by the AVR P80 command."""
    if not path:
        return None
    calibration_path = Path(path)
    if not calibration_path.is_file():
        return None
    try:
        stored = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AvrSerialError("Cannot read AVR calibration %s: %s" % (path, error)) from error

    keys = {
        "toLiter": "toLiter",
        "toLiterNull": "toLiterNull",
        "mashTemperatureDelta": "mashTemperatureDelta",
        "boilTemperatureDelta": "boilTemperatureDelta",
    }
    calibration = {}
    try:
        for output_key, stored_key in keys.items():
            calibration[output_key] = float(stored[stored_key])
        calibration["boilingPoint"] = float(stored.get("boilingPoint", 100.0))
    except (KeyError, TypeError, ValueError) as error:
        raise AvrSerialError("AVR calibration is missing or invalid: %s" % error) from error
    if calibration["toLiter"] <= 0 or not all(math.isfinite(value) for value in calibration.values()):
        raise AvrSerialError("AVR calibration values must be finite and toLiter must be positive")
    calibration["path"] = str(calibration_path)
    return calibration


def encode_command(payload, packet_id):
    """Encode the legacy host-to-AVR packet.

    The firmware expects ``$``, an opaque packet id, the command-text length,
    the command text, a trailing field separator, ``*``, CR, and LF.
    """
    if not isinstance(payload, str) or not payload:
        raise AvrSerialError("AVR command payload must be a non-empty string")
    if any(char in payload for char in "$*\r\n"):
        raise AvrSerialError("AVR command contains a reserved framing character")
    encoded = payload.encode("ascii", "strict")
    if len(encoded) > 119:
        raise AvrSerialError("AVR command exceeds the 119-byte firmware limit")
    if not 1 <= packet_id <= 255:
        raise AvrSerialError("AVR packet id must be between 1 and 255")
    return b"$" + bytes((packet_id, len(encoded))) + encoded + b" *\r\n"


def parse_status_record(record):
    """Parse one tab-delimited, one-second AVR status record."""
    if isinstance(record, bytes):
        record = record.decode("ascii", "replace")
    fields = record.rstrip("\r\n").split("\t")
    if len(fields) < 19 or "V" not in fields[4]:
        return None

    def number(index, default=0.0):
        try:
            return float(fields[index].strip())
        except (IndexError, TypeError, ValueError):
            return default

    return {
        "step": int(number(0, -1)),
        "total_time_s": int(number(1)),
        "water_volume_l": number(6),
        "mash_temperature_c": number(7),
        "boil_temperature_c": number(8),
        # Fields 9 and 10 are duplicate corrected temperatures in tenths of
        # a degree. Pump telemetry starts at field 11 in the AVR writer.
        "mash_pump_tacho": number(11),
        "boil_pump_tacho": number(12),
        "mash_pump_diagnostic": int(number(13)),
        "boil_pump_diagnostic": int(number(15)),
        "mash_heater_output": bool(int(number(17))),
        "boil_heater_output": bool(int(number(18))),
    }


class AvrSerialBridge:
    VALVES = (
        "water_inlet_valve", "mash_inlet_valve", "boil_inlet_valve",
        "hop_cage_1_valve", "hop_cage_2_valve", "hop_cage_3_valve",
        "hop_cage_4_valve", "cooling_water_inlet_valve",
        "wort_cooling_valve", "outlet_valve", "mash_return_valve",
        "boil_return_valve",
    )
    PUMPS = ("mash_pump", "boil_pump")
    HEATERS = ("mash_heater", "boil_heater")

    def __init__(self, device="/dev/ttyS1", state_file=None, enabled=True, safe_start=True, registry=None, calibration=None):
        self.device = device
        self.state_file = Path(state_file) if state_file else None
        self.enabled = enabled
        self.safe_start = safe_start
        self.registry = registry or HardwareRegistry()
        self.calibration = calibration
        self._fd = None
        self._packet_id = 0
        self._transport_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._status_condition = threading.Condition(self._state_lock)
        self._status_sequence = 0
        self._ack_lock = threading.Lock()
        self._acks = {}
        self._stop = threading.Event()
        self._thread = None
        self._safe_start_thread = None
        self._safe_start_complete = not safe_start
        self._initialization_complete = not safe_start
        self._last_status_at = 0.0
        self._last_error = None
        self._volume_zero_l = 0.0
        self._raw_water_volume_l = 0.0
        self._valves = {device_id: False for device_id in self.VALVES}
        self._pumps = {device_id: False for device_id in self.PUMPS}
        self._heaters = {
            device_id: {"targetC": None, "output": False}
            for device_id in self.HEATERS
        }
        self._sensors = {
            "tempMashC": 0.0, "tempBoilC": 0.0,
            "boilVolumeL": 0.0, "mashVolumeL": 0.0,
            "systemWeightKg": 0.0,
            "mashPumpTacho": 0.0, "boilPumpTacho": 0.0,
            "mashPumpDiagnostic": 0, "boilPumpDiagnostic": 0,
        }
        self._load_state()
        if enabled:
            self.start()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._reader_loop, name="brewie-avr", daemon=True)
        self._thread.start()
        if self.safe_start:
            self._safe_start_thread = threading.Thread(
                target=self._safe_start_loop,
                name="brewie-avr-safe-start",
                daemon=True,
            )
            self._safe_start_thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        if self._safe_start_thread:
            self._safe_start_thread.join(timeout=1.5)
        self._close_serial()

    def _safe_start_loop(self):
        """Issue P999 once after opening the serial transport.

        A separate thread is required because the reader thread must remain
        available to receive the AVR ACK while ``close_all`` waits for it.
        """
        while not self._stop.is_set() and not self._safe_start_complete:
            if self._fd is None:
                self._stop.wait(0.1)
                continue
            try:
                self._perform_safe_start()
            except AvrSerialError as error:
                self._last_error = "AVR safe start failed: %s" % error
                self._stop.wait(1.0)

    def _perform_safe_start(self):
        self.close_all()
        if self.enabled:
            with self._status_condition:
                acknowledged_sequence = self._status_sequence
                completed = self._status_condition.wait_for(
                    lambda: self._status_sequence > acknowledged_sequence,
                    timeout=15.0,
                )
            if not completed:
                raise AvrSerialError("AVR close-all completion status was not received")
        if self.calibration:
            self.initialize()
        else:
            self._last_error = "AVR is safe but not initialized: machine calibration file is missing"
        with self._state_lock:
            self._safe_start_complete = True

    def initialize(self):
        """Load calibration and power on the AVR using its P80 protocol."""
        if not self.calibration:
            raise AvrSerialError("AVR initialization requires a machine calibration file")
        values = self.calibration
        payload = "P80 %.6f %.6f %.5f %.5f %.2f" % (
            values["toLiter"],
            values["toLiterNull"],
            values["mashTemperatureDelta"],
            values["boilTemperatureDelta"],
            values["boilingPoint"],
        )
        self.send_payload(payload)
        with self._state_lock:
            self._initialization_complete = True

    def _load_state(self):
        if not self.state_file:
            return
        try:
            stored = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._valves.update({key: bool(value) for key, value in stored.get("valves", {}).items() if key in self._valves})
            self._pumps.update({key: bool(value) for key, value in stored.get("pumps", {}).items() if key in self._pumps})
            for key, value in stored.get("heater_targets_C", {}).items():
                if key in self._heaters and isinstance(value, (int, float)):
                    self._heaters[key]["targetC"] = float(value) if value >= 5 else None
            self._volume_zero_l = float(stored.get("volume_zero_l", 0.0))
        except (OSError, ValueError, TypeError):
            pass

    def _save_state(self):
        if not self.state_file:
            return
        data = {
            "valves": self._valves,
            "pumps": self._pumps,
            "heater_targets_C": {key: value["targetC"] for key, value in self._heaters.items()},
            "volume_zero_l": self._volume_zero_l,
        }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(str(temporary), str(self.state_file))
        except OSError as error:
            self._last_error = "Cannot persist AVR state: %s" % error

    def _open_serial(self):
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        if hasattr(termios, "TIOCEXCL"):
            fcntl.ioctl(fd, termios.TIOCEXCL)
        attributes = termios.tcgetattr(fd)
        attributes[0] = 0
        attributes[1] = 0
        attributes[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attributes[3] = 0
        attributes[4] = termios.B115200
        attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = 0
        attributes[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, attributes)
        termios.tcflush(fd, termios.TCIOFLUSH)
        self._fd = fd
        self._last_error = None

    def _close_serial(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _reader_loop(self):
        buffer = bytearray()
        while not self._stop.is_set():
            if self._fd is None:
                try:
                    self._open_serial()
                except OSError as error:
                    self._last_error = "Cannot open %s: %s" % (self.device, error)
                    self._stop.wait(2.0)
                    continue
            try:
                readable, _, _ = select.select([self._fd], [], [], 1.0)
                if not readable:
                    continue
                chunk = os.read(self._fd, 1024)
                if not chunk:
                    raise OSError("serial device returned EOF")
                buffer.extend(chunk)
                while b"\n" in buffer:
                    record, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    self._process_record(bytes(record).rstrip(b"\r"))
            except (OSError, ValueError) as error:
                self._last_error = "AVR serial read failed: %s" % error
                self._close_serial()
                buffer.clear()

    def _process_record(self, record):
        if len(record) >= 4 and record[0] == ord("$") and record[1] == 1 and record[3] == ord("*"):
            with self._ack_lock:
                event = self._acks.get(record[2])
            if event:
                event.set()
            return
        parsed = parse_status_record(record)
        if not parsed:
            return
        with self._state_lock:
            self._status_sequence += 1
            self._last_status_at = time.monotonic()
            self._raw_water_volume_l = parsed["water_volume_l"]
            self._sensors.update({
                "tempMashC": parsed["mash_temperature_c"],
                "tempBoilC": parsed["boil_temperature_c"],
                "boilVolumeL": max(0.0, parsed["water_volume_l"] - self._volume_zero_l),
                "mashPumpTacho": parsed["mash_pump_tacho"],
                "boilPumpTacho": parsed["boil_pump_tacho"],
                "mashPumpDiagnostic": parsed["mash_pump_diagnostic"],
                "boilPumpDiagnostic": parsed["boil_pump_diagnostic"],
            })
            self._heaters["mash_heater"]["output"] = parsed["mash_heater_output"]
            self._heaters["boil_heater"]["output"] = parsed["boil_heater_output"]
            self._status_condition.notify_all()

    def _next_packet_id(self):
        self._packet_id = self._packet_id % 255 + 1
        return self._packet_id

    def send_payload(self, payload, timeout=0.7, attempts=2):
        if not self.enabled or self._fd is None:
            raise AvrSerialError("AVR serial transport is not connected")
        with self._transport_lock:
            packet_id = self._next_packet_id()
            frame = encode_command(payload, packet_id)
            acknowledged = threading.Event()
            with self._ack_lock:
                self._acks[packet_id] = acknowledged
            try:
                for _ in range(attempts):
                    try:
                        os.write(self._fd, frame)
                    except OSError as error:
                        self._close_serial()
                        raise AvrSerialError("AVR serial write failed: %s" % error) from error
                    if acknowledged.wait(timeout):
                        self._last_error = None
                        return packet_id
                raise AvrSerialError("No ACK received for %s" % payload.split(" ", 1)[0])
            finally:
                with self._ack_lock:
                    self._acks.pop(packet_id, None)

    def set_device(self, device_id, action="toggle"):
        try:
            device = self.registry.resolve(device_id)
        except HardwareRegistryError as error:
            raise AvrSerialError(str(error)) from error
        kind = device["kind"]
        if kind not in {"valve", "pump"}:
            raise AvrSerialError("%s is not a direct valve or pump control" % device_id)
        states = self._valves if kind == "valve" else self._pumps
        current = states[device["id"]]
        enabled = not current if action == "toggle" else action in {"open", "on"}
        if action not in {"toggle", "open", "close", "on", "off"}:
            raise AvrSerialError("Unsupported %s action: %s" % (kind, action))
        command_key = ("open_command" if enabled else "close_command") if kind == "valve" else ("on_command" if enabled else "off_command")
        command = device["control"].get(command_key)
        if not command:
            raise AvrSerialError("No %s command is registered for %s" % (action, device_id))
        self.send_payload(command)
        with self._state_lock:
            states[device["id"]] = enabled
            self._save_state()

    def set_cooling_path(self, action="toggle"):
        current = self._valves["wort_cooling_valve"] or self._valves["cooling_water_inlet_valve"]
        enabled = not current if action == "toggle" else action in {"open", "on"}
        requested = "open" if enabled else "close"
        self.set_device("cooling_water_inlet_valve", requested)
        self.set_device("wort_cooling_valve", requested)

    def set_heater_target(self, device_id, target_c):
        try:
            device = self.registry.resolve(device_id, "heater")
        except HardwareRegistryError as error:
            raise AvrSerialError(str(error)) from error
        try:
            target_c = float(target_c)
        except (TypeError, ValueError) as error:
            raise AvrSerialError("Heater target must be numeric") from error
        if target_c != 0 and not 5 <= target_c <= 110:
            raise AvrSerialError("Heater target must be 0 (off) or between 5 and 110 C")
        command = "%s %d" % (device["control"]["target_command_prefix"], round(target_c * 10))
        self.send_payload(command)
        with self._state_lock:
            self._heaters[device["id"]]["targetC"] = target_c if target_c >= 5 else None
            if target_c < 5:
                self._heaters[device["id"]]["output"] = False
            self._save_state()

    def close_all(self):
        self.send_payload("P999")
        with self._state_lock:
            self._valves = {device_id: False for device_id in self.VALVES}
            self._pumps = {device_id: False for device_id in self.PUMPS}
            for heater in self._heaters.values():
                heater.update({"targetC": None, "output": False})
            self._save_state()

    def reset_level(self):
        with self._state_lock:
            self._volume_zero_l = self._raw_water_volume_l
            self._sensors["boilVolumeL"] = 0.0
            self._save_state()

    def status(self):
        with self._state_lock:
            connected = (
                self._safe_start_complete
                and self._initialization_complete
                and self._fd is not None
                and time.monotonic() - self._last_status_at < 3.5
            )
            heaters = {}
            for device_id, heater in self._heaters.items():
                target = heater["targetC"]
                output = bool(heater["output"])
                state = "off" if target is None and not output else "on" if output else "deferred"
                heaters[device_id] = {
                    "active": output,
                    "targetC": target,
                    "powerPercent": 100 if output else 0,
                    "state": state,
                }
            return {
                "source": "hardware",
                "connected": connected,
                "safeStartComplete": self._safe_start_complete,
                "initializationConfigured": self.calibration is not None,
                "initializationComplete": self._initialization_complete,
                "calibrationFile": None if self.calibration is None else self.calibration.get("path"),
                "serialDevice": self.device,
                "lastError": self._last_error,
                "valves": dict(self._valves),
                "pumps": dict(self._pumps),
                "heaters": heaters,
                "sensors": dict(self._sensors),
            }
