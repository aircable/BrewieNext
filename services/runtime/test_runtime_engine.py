import os
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from editor_backend import RuntimeManager, _resolve_procedure_path, app, recipe_global_values
from hardware_registry import HardwareRegistry
from runtime_engine import AvrHAL, ProcedureExecution, SimulatedHAL, WorkflowSession


EDITOR_ROOT = Path(os.environ["PROCEDURES_DIR"])
RECIPE_ROOT = Path(os.environ["BUNDLED_RECIPES_DIR"])


def load_yaml(name):
    path = _resolve_procedure_path(name)
    if path is None:
        raise FileNotFoundError(name)
    return yaml.safe_load(path.read_text())


class RuntimeProcedureTests(unittest.TestCase):
    def test_transition_order_follows_yaml_and_can_skip_states(self):
        execution = ProcedureExecution(
            load_yaml("prepare_brew.yml"),
            {"recipe_name": "Test", "batch_volume_L": 20,
             "mash_water_volume_L": 15, "sparge_water_volume_L": 10},
            SimulatedHAL(),
        )
        execution.enter()
        self.assertEqual(execution.current_state, "review_recipe")
        execution.provide_input("recipe_reviewed", "continue")
        execution.tick(0)
        self.assertEqual(execution.current_state, "confirm_machine_ready")

    def test_duration_reached_accepts_yaml_duration_strings(self):
        procedure = {
            "name": "duration_test",
            "start_state": "settle",
            "states": {
                "settle": {
                    "action": [],
                    "transition": [{"duration_reached(\"4s\")": "next_phase"}],
                }
            },
        }
        execution = ProcedureExecution(procedure, {}, SimulatedHAL())
        execution.tick(3.9)
        self.assertEqual(execution.status, "running")
        execution.tick(0.1)
        self.assertEqual(execution.status, "complete")

    def test_manual_intervention_is_not_replayed_each_tick_or_resume(self):
        procedure = {
            "name": "intervention_test",
            "start_state": "circulate",
            "states": {
                "circulate": {
                    "action": [{"set_valve": {"valve": "boil_return_valve", "state": "open"}}],
                    "transition": [{"default": "loops"}],
                }
            },
        }
        workflow = {
            "name": "test",
            "steps": [{"id": "circulate", "procedure": "intervention_test"}],
        }
        hal = SimulatedHAL()
        session = WorkflowSession(workflow, {"intervention_test": procedure}, {}, hal, autostart=False)
        self.assertTrue(hal.valves["boil_return_valve"])
        hal.set_device("boil_return_valve", "closed")
        session.tick(10)
        session.pause()
        session.resume()
        self.assertFalse(hal.valves["boil_return_valve"])

    def test_navigation_runs_on_exit_before_entering_next_procedure(self):
        first = {
            "name": "first",
            "start_state": "active",
            "states": {
                "active": {
                    "action": [{"set_pump": {"device": "boil_pump", "state": "on"}}],
                    "on_exit": [{"set_pump": {"device": "boil_pump", "state": "off"}}],
                    "transition": [{"default": "loops"}],
                }
            },
        }
        second = {
            "name": "second",
            "start_state": "active",
            "states": {"active": {"action": [], "transition": [{"default": "loops"}]}},
        }
        workflow = {
            "name": "test",
            "steps": [
                {"id": "first", "procedure": "first"},
                {"id": "second", "procedure": "second"},
            ],
        }
        hal = SimulatedHAL()
        session = WorkflowSession(workflow, {"first": first, "second": second}, {}, hal, autostart=False)
        self.assertTrue(hal.pumps["boil_pump"])
        session.navigate("next")
        self.assertFalse(hal.pumps["boil_pump"])
        self.assertEqual(session.snapshot()["active_procedure"], "second")

    def test_current_workflow_completes_in_authoritative_simulation(self):
        workflow = load_yaml("beer_brewing.yml")
        names = []
        for step in workflow["steps"]:
            if "procedure" in step:
                names.append(step["procedure"])
            else:
                names.extend(branch["procedure"] for branch in step["parallel"]["branches"])
        procedures = {name: load_yaml(f"{name}.yml") for name in names}
        recipe = yaml.safe_load((RECIPE_ROOT / "development_test.yml").read_text())
        session = WorkflowSession(
            workflow, procedures, recipe_global_values(recipe), SimulatedHAL(),
            speed=120, autostart=False,
        )

        for _ in range(30000):
            for item in session.active:
                execution = item["execution"]
                definition = execution._input_definition()
                if definition and execution.waiting_for_input():
                    value = "automatic" if "automatic" in definition["options"] else definition["options"][0]
                    session.provide_input(definition["key"], value, execution.name)
                    break
            session.tick(2)
            if session.status in session.TERMINAL:
                break

        self.assertEqual(session.status, "complete", session.snapshot())

    def test_hardware_mode_uses_same_state_engine_and_safe_baseline(self):
        class RecordingBridge:
            def __init__(self):
                self.registry = HardwareRegistry()
                self.calls = []

            def status(self):
                return {
                    "connected": True,
                    "sensors": {},
                    "valves": {},
                    "pumps": {},
                    "heaters": {},
                }

            def close_all(self):
                self.calls.append(("close_all",))

            def set_device(self, device, action):
                self.calls.append(("set_device", device, action))

            def set_heater_target(self, device, target):
                self.calls.append(("set_heater_target", device, target))

            def reset_level(self):
                self.calls.append(("reset_level",))

            def send_payload(self, payload):
                self.calls.append(("send_payload", payload))

        procedure = {
            "name": "hardware_test",
            "start_state": "active",
            "states": {
                "active": {
                    "action": [{"set_valve": {"valve": "boil_return_valve", "state": "open"}}],
                    "on_exit": [{"set_valve": {"valve": "boil_return_valve", "state": "closed"}}],
                    "transition": [{"default": "next_phase"}],
                }
            },
        }
        workflow = {"name": "hardware", "steps": [{"id": "test", "procedure": "hardware_test"}]}
        bridge = RecordingBridge()
        session = WorkflowSession(
            workflow, {"hardware_test": procedure}, {}, AvrHAL(bridge), autostart=False,
        )
        self.assertEqual(session.status, "complete")
        self.assertEqual(
            bridge.calls,
            [
                ("close_all",),
                ("set_device", "boil_return_valve", "open"),
                ("set_device", "boil_return_valve", "closed"),
                ("close_all",),
            ],
        )

    def test_hardware_close_all_cancels_active_hop_session_first(self):
        class RecordingBridge:
            def __init__(self):
                self.registry = HardwareRegistry()
                self.calls = []

            def send_payload(self, payload):
                self.calls.append(payload)

            def close_all(self):
                self.calls.append("P999")

        bridge = RecordingBridge()
        hal = AvrHAL(bridge)
        hal.start_operation(
            "run_hop_stage",
            {
                "open_cages": [1],
                "start_minutes_remaining": 60,
                "end_minutes_remaining": 30,
                "session": "start",
            },
            1,
        )
        hal.close_all()
        self.assertEqual(bridge.calls[0], "P200")
        self.assertTrue(bridge.calls[1].startswith("P103 1 "))
        self.assertEqual(bridge.calls[-2:], ["P201", "P999"])


class RuntimeApiTests(unittest.TestCase):
    def test_simulation_session_api_uses_yaml_transition(self):
        manager = RuntimeManager(None)
        with patch("editor_backend.RUNTIME_MANAGER", manager):
            client = app.test_client()
            response = client.post(
                "/api/runtime/sessions",
                json={"mode": "simulation", "recipe_id": "development_test", "speed": 60},
            )
            self.assertEqual(response.status_code, 201, response.get_json())
            snapshot = response.get_json()["data"]
            self.assertEqual(snapshot["active_state"], "review_recipe")
            self.assertEqual(snapshot["status"], "waiting_for_input")

            response = client.post(
                "/api/runtime/session/input",
                json={"key": "recipe_reviewed", "value": "continue"},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["data"]["active_state"], "confirm_machine_ready")

            response = client.post(
                "/api/runtime/session/control",
                json={"action": "abort"},
            )
            self.assertEqual(response.get_json()["data"]["status"], "aborted")

    def test_runtime_mutations_reject_cross_host_origin(self):
        manager = RuntimeManager(None)
        with patch("editor_backend.RUNTIME_MANAGER", manager):
            response = app.test_client().post(
                "/api/runtime/sessions",
                json={"mode": "simulation", "recipe_id": "development_test"},
                headers={"Origin": "http://untrusted.example"},
            )
            self.assertEqual(response.status_code, 403)

    def test_runtime_mutations_accept_localhost_loopback_alias(self):
        manager = RuntimeManager(None)
        with patch("editor_backend.RUNTIME_MANAGER", manager):
            response = app.test_client().post(
                "/api/runtime/sessions",
                base_url="http://127.0.0.1:8081",
                json={"mode": "simulation", "recipe_id": "development_test"},
                headers={"Origin": "http://localhost:5173"},
            )
            self.assertEqual(response.status_code, 201, response.get_json())


if __name__ == "__main__":
    unittest.main()
