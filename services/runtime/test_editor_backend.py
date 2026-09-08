import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from editor_backend import (
    ProcedureRunner,
    _program_catalog_path,
    _resolve_procedure_path,
    _insert_workflow_step,
    _remove_workflow_step,
    _update_workflow_step,
    app,
    recipe_global_values,
)

import editor_backend


def load_procedure(filename):
    path = _resolve_procedure_path(filename)
    if path is None:
        raise FileNotFoundError(filename)
    return yaml.safe_load(path.read_text())


class ProcedureRunnerExitTests(unittest.TestCase):
    def run_procedure(self, procedure):
        runner = ProcedureRunner(procedure)
        runner.running = True
        runner.start_time = time.time()
        runner.state_enter_time = time.time() - 1
        runner._run_loop()
        return runner.log

    def test_on_exit_runs_before_next_phase(self):
        log = self.run_procedure({
            "name": "exit_test",
            "start_state": "active",
            "states": {
                "active": {
                    "action": [],
                    "on_exit": [{"log_event": "state cleanup"}],
                    "transition": [{"step_complete": "next_phase"}],
                }
            },
            "error_handler": "external-error",
        })

        self.assertIn("  Log: state cleanup", log)

    def test_on_exit_runs_before_external_error_handler(self):
        log = self.run_procedure({
            "name": "timeout_test",
            "start_state": "filling",
            "states": {
                "filling": {
                    "action": [],
                    "on_exit": [{"set_valve": {"valve": "boil_inlet_valve", "state": "closed"}}],
                    "timeout_s": "0.1s",
                    "transition": [{"timeout_exceeded": "error_handler"}],
                }
            },
            "error_handler": "fill-water-error",
        })

        close_entry = "  Set valve: {'valve': 'boil_inlet_valve', 'state': 'closed'}"
        error_entry = "ERROR: External error handler 'fill-water-error' triggered"
        self.assertLess(log.index(close_entry), log.index(error_entry))


class RecipeGlobalFillTests(unittest.TestCase):
    editor_root = Path(os.environ["PROCEDURES_DIR"])
    recipe_root = Path(os.environ["BUNDLED_RECIPES_DIR"])

    def test_program_catalog_is_found_from_release_schema_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            schema = root / "releases/0.2.0/schemas/procedure.schema.json"
            catalog = root / "releases/0.2.0/catalog/programs.yml"
            workspace.mkdir()
            schema.parent.mkdir(parents=True)
            catalog.parent.mkdir(parents=True)
            schema.write_text("{}")
            catalog.write_text("schema_version: 1\nprograms: []\n")
            with patch.object(editor_backend, "PROCEDURES_DIR", str(workspace)), \
                    patch.object(editor_backend, "SCHEMA_PATH", str(schema)):
                self.assertEqual(_program_catalog_path(), catalog)

    def test_program_catalog_distinguishes_available_and_design_programs(self):
        response = app.test_client().get("/api/programs")
        self.assertEqual(response.status_code, 200)
        programs = {program["id"]: program for program in response.get_json()["programs"]}
        self.assertEqual(programs["beer_brewing"]["status"], "available")
        self.assertEqual(programs["beer_brewing"]["workflow"], "beer_brewing")
        self.assertEqual(programs["lme_brewing"]["status"], "design")
        self.assertEqual(programs["lme_brewing"]["workflow"], "lme_brewing")
        self.assertEqual(programs["cleaning_short"]["category"], "cleaning")

    def test_design_program_can_be_edited_but_not_executed(self):
        graph_response = app.test_client().get("/api/graphs/lme_brewing")
        self.assertEqual(graph_response.status_code, 200)
        self.assertEqual(graph_response.get_json()["data"]["source_format"], "sequence")
        self.assertEqual(graph_response.get_json()["data"]["default_recipe"], "lme_development")
        nodes = {node["id"]: node for node in graph_response.get_json()["data"]["nodes"]}
        self.assertEqual(nodes["lme_hopping"]["parallel_group"], "hopping_and_mash_heating")
        self.assertEqual(nodes["heat_lme_mash_tank"]["parallel_group"], "hopping_and_mash_heating")

        resolve_response = app.test_client().get(
            "/api/graphs/lme_brewing/resolve?recipe=lme_development"
        )
        self.assertEqual(resolve_response.status_code, 200)
        self.assertEqual(
            resolve_response.get_json()["global_values"]["lme_mash_target_temperature_C"],
            80,
        )

        runtime_response = app.test_client().post(
            "/api/runtime/sessions",
            json={"workflow": "lme_brewing", "mode": "simulation"},
        )
        self.assertEqual(runtime_response.status_code, 400)
        self.assertIn("still in design", runtime_response.get_json()["error"])

    def test_fill_procedures_use_recipe_globals_without_local_parameters(self):
        recipe = yaml.safe_load((self.recipe_root / "development_test.yml").read_text())
        globals_snapshot = recipe_global_values(recipe)
        cases = (
            ("fill_mash_water.yml", "fill_mash_water", "mash_water_volume_L"),
            ("fill_sparge_water.yml", "fill_sparge_water", "sparge_water_volume_L"),
        )

        for filename, procedure_name, global_name in cases:
            with self.subTest(procedure=procedure_name):
                procedure = load_procedure(filename)
                self.assertNotIn("parameters", procedure)
                runner = ProcedureRunner(procedure, global_values=globals_snapshot)
                runner.current_state = "fill_automatic"
                target = globals_snapshot[global_name]
                runner.set_sensor("weight_boil_tank", target - 0.4)
                next_state = runner._evaluate_transitions(procedure["states"]["fill_automatic"])
                self.assertEqual(next_state, "wait_for_user_to_continue")

    def test_resolved_workflow_exposes_recipe_global_snapshot(self):
        response = app.test_client().get(
            "/api/graphs/beer_brewing/resolve?recipe=development_test"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["global_values"]["mash_water_volume_L"], 15)
        self.assertEqual(payload["global_values"]["sparge_water_volume_L"], 10)
        self.assertEqual(payload["global_values"]["lme_boil_tank_volume_L"], 15)
        self.assertEqual(payload["global_values"]["lme_mash_tank_volume_L"], 10)
        self.assertEqual(payload["global_values"]["lme_mash_target_temperature_C"], 78)
        self.assertEqual(payload["global_values"]["mash_rest_1_temperature_C"], 67)
        self.assertEqual(payload["global_values"]["mash_rest_2_temperature_C"], 76)
        self.assertEqual(payload["global_values"]["sparge_cycle_count"], 5)
        self.assertEqual(payload["global_values"]["sparge_boil_reserve_volume_L"], 5)
        self.assertEqual(payload["global_values"]["boil_duration_min"], 100)
        self.assertEqual(payload["global_values"]["initial_unhopped_boil_duration_min"], 40)
        self.assertEqual(payload["global_values"]["hopping_duration_min"], 60)
        self.assertEqual(payload["global_values"]["hop_cage_1_minutes_remaining"], 60)
        self.assertEqual(payload["global_values"]["hop_cage_2_minutes_remaining"], 30)
        self.assertEqual(payload["global_values"]["hop_cage_3_minutes_remaining"], 10)
        self.assertEqual(payload["global_values"]["hop_cage_4_minutes_remaining"], 5)
        self.assertEqual(payload["global_values"]["sedimentation_duration_min"], 20)
        nodes = {node["id"]: node for node in payload["data"]["nodes"]}
        self.assertEqual(nodes["fill_mash_water"]["procedure"], "fill_mash_water")
        self.assertEqual(nodes["fill_sparge_water"]["procedure"], "fill_sparge_water")
        self.assertEqual(nodes["fill_mash_water"]["parameters"], {})
        self.assertEqual(nodes["fill_sparge_water"]["parameters"], {})
        self.assertEqual(nodes["mashing"]["parallel_group"], "mash_and_sparge_heat")
        self.assertEqual(nodes["heat_sparge_water"]["parallel_group"], "mash_and_sparge_heat")
        self.assertLess(nodes["fill_sparge_water"]["position"]["y"], nodes["mashing"]["position"]["y"])
        self.assertEqual(nodes["mashing"]["position"]["y"], nodes["heat_sparge_water"]["position"]["y"])
        self.assertNotIn("empty_boil_tank", nodes)
        edges = {(edge["source"], edge["target"]) for edge in payload["data"]["edges"]}
        self.assertIn(("sparging", "boiling"), edges)
        self.assertIn(("boiling", "hopping"), edges)
        self.assertIn(("hopping", "cooling"), edges)
        self.assertIn(("cooling", "sedimentation"), edges)
        self.assertIn(("sedimentation", "transfer_to_fermenter"), edges)

        workflow = load_procedure("beer_brewing.yml")
        self.assertIn("steps", workflow)
        self.assertNotIn("nodes", workflow)
        self.assertNotIn("edges", workflow)

    def test_sparging_uses_cycle_counter_and_boil_tank_reserve(self):
        recipe = yaml.safe_load((self.recipe_root / "development_test.yml").read_text())
        globals_snapshot = recipe_global_values(recipe)
        procedure = load_procedure("sparging.yml")
        self.assertNotIn("parameters", procedure)
        self.assertNotIn("finalize", procedure)

        runner = ProcedureRunner(procedure, global_values=globals_snapshot)
        runner._execute_action({"set_counter": {"name": "sparge_cycle", "value": 1}})
        self.assertEqual(runner.variables["sparge_cycle"], 1)

        drain_state = procedure["states"]["drain_mash_to_boil"]
        runner.variables["sparge_cycle"] = 4
        runner.set_sensor("mash_pump_diagnostic", 2)
        self.assertEqual(runner._evaluate_transitions(drain_state), "settle_before_return")
        runner.variables["sparge_cycle"] = 5
        self.assertEqual(runner._evaluate_transitions(drain_state), "finish_sparging")

        return_state = procedure["states"]["return_twenty_liters_to_mash"]
        runner.set_sensor("water_volume", globals_snapshot["sparge_boil_reserve_volume_L"])
        self.assertEqual(runner._evaluate_transitions(return_state), "settle_before_next_cycle")
        runner._execute_action({"increment_counter": {"name": "sparge_cycle"}})
        self.assertEqual(runner.variables["sparge_cycle"], 6)

    def test_hopping_releases_every_physical_cage_and_uses_recipe_intervals(self):
        recipe = yaml.safe_load((self.recipe_root / "development_test.yml").read_text())
        globals_snapshot = recipe_global_values(recipe)
        procedure = load_procedure("hopping.yml")
        self.assertNotIn("parameters", procedure)
        self.assertNotIn("finalize", procedure)

        stage_configs = []
        for cage in range(1, 5):
            actions = procedure["states"][f"hop_stage_{cage}"]["action"]
            stage_configs.extend(
                action["run_hop_stage"]
                for action in actions
                if "run_hop_stage" in action
            )
        self.assertEqual(
            [stage["open_cages"] for stage in stage_configs],
            [[1], [1, 2], [1, 2, 3], [1, 2, 3, 4]],
        )
        self.assertEqual(
            [stage["session"] for stage in stage_configs],
            ["start", "continue", "continue", "continue"],
        )

        runner = ProcedureRunner(procedure, global_values=globals_snapshot)
        runner.current_state = "hop_stage_1"
        runner.state_enter_time = time.time() - (30 * 60 + 1)
        runner._execute_action({"run_hop_stage": stage_configs[0]})
        self.assertEqual(
            runner._evaluate_transitions(procedure["states"]["hop_stage_1"]),
            "hop_stage_2",
        )
        runner.current_state = "hop_stage_4"
        runner.state_enter_time = time.time() - (5 * 60 + 1)
        runner._execute_action({"run_hop_stage": stage_configs[3]})
        self.assertEqual(
            runner._evaluate_transitions(procedure["states"]["hop_stage_4"]),
            "finish_hop_session",
        )

        finish_state = procedure["states"]["finish_hop_session"]
        runner.current_state = "finish_hop_session"
        runner.state_enter_time = time.time()
        runner._execute_action(finish_state["action"][0])
        self.assertFalse(runner.variables["avr_step_session_active"])
        self.assertEqual(runner._evaluate_transitions(finish_state), "next_phase")

    def test_transfer_requires_hose_confirmation_before_pump_actions(self):
        procedure = load_procedure("transfer_to_fermenter.yml")
        self.assertEqual(procedure["start_state"], "confirm_fermenter_hoses")
        confirm = procedure["states"]["confirm_fermenter_hoses"]
        self.assertFalse(any("set_pump" in action for action in confirm["action"]))

        runner = ProcedureRunner(procedure)
        self.assertIsNone(runner._evaluate_transitions(confirm))
        runner.set_user_input("fermenter_hoses", "connected")
        self.assertEqual(runner._evaluate_transitions(confirm), "empty_hop_cages")

    def test_procedure_files_do_not_use_hidden_finalize_blocks(self):
        for path in (_resolve_procedure_path(name) for name in (
            "boiling", "cooling", "fill_mash_water", "fill_sparge_water",
            "heat_mash_water", "heat_sparge_water", "hopping", "prepare_brew",
            "sedimentation", "sparging", "transfer_to_fermenter",
            "transfer_water_to_mash", "two_rest_mashing",
        )):
            assert path is not None
            if path.name == "beer_brewing.yml":
                continue
            with self.subTest(procedure=path.name):
                procedure = yaml.safe_load(path.read_text())
                self.assertNotIn("finalize", procedure)
                for state in procedure.get("states", {}).values():
                    targets = [next(iter(item.values())) for item in state.get("transition", [])]
                    self.assertNotIn("finalize", targets)

    def test_sedimentation_uses_recipe_duration(self):
        recipe = yaml.safe_load((self.recipe_root / "development_test.yml").read_text())
        globals_snapshot = recipe_global_values(recipe)
        procedure = load_procedure("sedimentation.yml")
        state = procedure["states"]["settle_cooled_wort"]
        runner = ProcedureRunner(procedure, global_values=globals_snapshot)
        runner.current_state = "settle_cooled_wort"
        runner.state_enter_time = time.time() - (20 * 60 + 1)
        self.assertEqual(runner._evaluate_transitions(state), "next_phase")


class OrderedWorkflowMutationTests(unittest.TestCase):
    def setUp(self):
        self.workflow = {
            "schema_version": 1,
            "name": "test_workflow",
            "steps": [
                {"id": "first", "procedure": "first"},
                {
                    "parallel": {
                        "id": "parallel_test",
                        "primary": "left",
                        "branches": [
                            {"id": "left", "procedure": "left"},
                            {"id": "right", "procedure": "right"},
                        ],
                        "join": {
                            "deadline": "left",
                            "require": {"right": "completed"},
                            "on_failure": "parallel_failed",
                        },
                    }
                },
                {"id": "last", "procedure": "last"},
            ],
        }

    def test_insert_after_parallel_branch_inserts_after_group(self):
        updated = _insert_workflow_step(
            self.workflow,
            {"id": "added", "procedure": "added"},
            after="left",
        )
        self.assertEqual(updated["steps"][2]["id"], "added")
        self.assertEqual(self.workflow["steps"][2]["id"], "last")

    def test_remove_preserves_source_and_rejects_parallel_branch(self):
        updated = _remove_workflow_step(self.workflow, "first")
        self.assertEqual(len(updated["steps"]), 2)
        self.assertEqual(len(self.workflow["steps"]), 3)
        with self.assertRaisesRegex(ValueError, "parallel group"):
            _remove_workflow_step(self.workflow, "left")

    def test_update_changes_metadata_without_changing_identity(self):
        updated = _update_workflow_step(
            self.workflow,
            "left",
            {"label": "Mash grains", "description": "Hold the mash at its rests."},
        )
        branch = updated["steps"][1]["parallel"]["branches"][0]
        self.assertEqual(branch["id"], "left")
        self.assertEqual(branch["procedure"], "left")
        self.assertEqual(branch["label"], "Mash grains")
        self.assertEqual(branch["description"], "Hold the mash at its rests.")
        self.assertNotIn("label", self.workflow["steps"][1]["parallel"]["branches"][0])


if __name__ == "__main__":
    unittest.main()
