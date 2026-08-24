import unittest

from hardware_registry import HardwareRegistry, HardwareRegistryError


class HardwareRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = HardwareRegistry()

    def test_resolves_legacy_cooling_aliases_to_distinct_valves(self):
        cooling_water = self.registry.resolve("cool_out_valve", "valve")
        wort_path = self.registry.resolve("cooling_valve", "valve")

        self.assertEqual(cooling_water["id"], "cooling_water_inlet_valve")
        self.assertEqual(cooling_water["control"]["open_command"], "P128")
        self.assertEqual(wort_path["id"], "wort_cooling_valve")
        self.assertEqual(wort_path["control"]["open_command"], "P130")

    def test_rejects_kind_mismatch(self):
        with self.assertRaises(HardwareRegistryError):
            self.registry.resolve("boil_heater", "valve")

    def test_all_actuators_declare_safe_state(self):
        for device_id, device in self.registry.devices.items():
            if device["kind"] != "sensor":
                self.assertIn("safe_state", device, device_id)

    def test_compiles_cumulative_hop_stage_to_p103_arguments(self):
        compiled = self.registry.compile_operation(
            "run_hop_stage",
            {
                "open_cages": [1, 2],
                "start_minutes_remaining": 30,
                "end_minutes_remaining": 10,
                "session": "continue",
            },
            runtime_step_id=27,
        )

        self.assertEqual(compiled["command"], "P103")
        self.assertIsNone(compiled["before_command"])
        self.assertEqual(compiled["arguments"][0], 27)
        self.assertEqual(compiled["arguments"][6:10], [1, 1, 0, 0])
        self.assertEqual(compiled["arguments"][14], 255)
        self.assertEqual(compiled["arguments"][16], 1200)
        self.assertEqual(compiled["arguments"][17:21], [6, 3, 0, 1])

    def test_hop_stage_session_lifecycle_uses_safe_avr_commands(self):
        base = {
            "open_cages": [1],
            "start_minutes_remaining": 60,
            "end_minutes_remaining": 30,
        }
        first = self.registry.compile_operation(
            "run_hop_stage", {**base, "session": "start"}, runtime_step_id=25
        )
        finish = self.registry.compile_session_finish("run_hop_stage")
        cancel = self.registry.compile_session_cancel("run_hop_stage")

        self.assertEqual(first["before_command"], "P200")
        self.assertEqual(finish, {"command": "P201", "wait_for_ack": True})
        self.assertEqual(cancel, {"command": "P201", "wait_for_ack": True})


if __name__ == "__main__":
    unittest.main()
