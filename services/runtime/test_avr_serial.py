import os
import pty
import threading
import time
import unittest
from unittest.mock import patch

from avr_serial import AvrSerialBridge, encode_command, parse_status_record
from editor_backend import app


class RecordingBridge(AvrSerialBridge):
    def __init__(self):
        super().__init__(enabled=False, safe_start=False)
        self.payloads = []

    def send_payload(self, payload, timeout=0.7, attempts=2):
        self.payloads.append(payload)
        return len(self.payloads)


class ConnectedRecordingBridge(RecordingBridge):
    def status(self):
        status = super().status()
        status["connected"] = True
        return status


class AvrSerialProtocolTests(unittest.TestCase):
    calibration = {
        "toLiter": 18996.080294,
        "toLiterNull": 0.0,
        "mashTemperatureDelta": 0.81854,
        "boilTemperatureDelta": 1.70194,
        "boilingPoint": 100.0,
        "path": "/test/machine.json",
    }

    def test_command_frame_has_packet_id_length_trailing_separator_and_terminator(self):
        self.assertEqual(
            encode_command("P150 675", 7),
            b"$\x07\x08P150 675 *\r\n",
        )

    def test_status_parser_extracts_tank_pump_and_heater_fields(self):
        fields = [
            "-1", "42", "", "", "V15", "1234", "18.5", "64.2", "71.3",
            "642", "713", "200", "20", "1", "187", "2", "21", "1", "0", "33",
            "", "0", "0", "100", "24", "0", "0",
        ]
        parsed = parse_status_record("\t".join(fields) + "\r\n")
        self.assertEqual(parsed["water_volume_l"], 18.5)
        self.assertEqual(parsed["mash_temperature_c"], 64.2)
        self.assertEqual(parsed["boil_temperature_c"], 71.3)
        self.assertEqual(parsed["mash_pump_tacho"], 200)
        self.assertEqual(parsed["boil_pump_tacho"], 20)
        self.assertEqual(parsed["mash_pump_diagnostic"], 1)
        self.assertEqual(parsed["boil_pump_diagnostic"], 2)
        self.assertTrue(parsed["mash_heater_output"])
        self.assertFalse(parsed["boil_heater_output"])

    def test_registry_commands_and_heater_deferred_state(self):
        bridge = RecordingBridge()
        bridge.set_device("boil_pump", "on")
        bridge.set_device("boil_return_valve", "open")
        bridge.set_heater_target("boil_heater", 67)
        self.assertEqual(bridge.payloads, ["P126", "P136", "P151 670"])

        fields = [
            "-1", "0", "", "", "V15", "0", "0", "20", "20", "200",
            "200", "0", "0", "0", "0", "0", "0", "1", "0", "30", "", "0",
            "0", "100", "20", "0", "0",
        ]
        bridge._process_record("\t".join(fields).encode())
        status = bridge.status()
        self.assertEqual(status["heaters"]["mash_heater"]["state"], "on")
        self.assertEqual(status["heaters"]["boil_heater"]["state"], "deferred")
        self.assertEqual(status["heaters"]["boil_heater"]["targetC"], 67)

    def test_close_all_uses_p999_and_clears_outputs(self):
        bridge = RecordingBridge()
        bridge.set_device("mash_pump", "on")
        bridge.set_heater_target("mash_heater", 65)
        bridge.close_all()
        self.assertEqual(bridge.payloads[-1], "P999")
        status = bridge.status()
        self.assertFalse(status["pumps"]["mash_pump"])
        self.assertEqual(status["heaters"]["mash_heater"]["state"], "off")

    def test_safe_start_issues_p999_once_and_unblocks_connected_state(self):
        bridge = RecordingBridge()
        bridge.safe_start = True
        bridge._safe_start_complete = False
        bridge._initialization_complete = False
        bridge._perform_safe_start()
        self.assertEqual(bridge.payloads, ["P999"])
        self.assertTrue(bridge.status()["safeStartComplete"])
        self.assertFalse(bridge.status()["initializationComplete"])

    def test_safe_start_initializes_calibrated_avr_after_reset(self):
        bridge = RecordingBridge()
        bridge.safe_start = True
        bridge.calibration = self.calibration
        bridge._safe_start_complete = False
        bridge._initialization_complete = False
        bridge._perform_safe_start()
        self.assertEqual(
            bridge.payloads,
            ["P999", "P80 18996.080294 0.000000 0.81854 1.70194 100.00"],
        )
        self.assertTrue(bridge.status()["initializationComplete"])

    def test_safe_start_round_trip_over_tty(self):
        master_fd, slave_fd = pty.openpty()
        device = os.ttyname(slave_fd)
        received = []
        status_fields = [
            "-1", "0", "", "", "V15", "0", "0", "20", "20", "200",
            "200", "0", "0", "0", "0", "0", "0", "0", "0", "30", "", "0",
            "0", "100", "20", "0", "0",
        ]

        def fake_avr():
            try:
                for _ in range(2):
                    frame = os.read(master_fd, 256)
                    packet_id = frame[1]
                    length = frame[2]
                    received.append(frame[3:3 + length].decode("ascii"))
                    os.write(master_fd, b"$\x01" + bytes((packet_id,)) + b"*\r\n")
                    os.write(master_fd, ("\t".join(status_fields) + "\r\n").encode("ascii"))
                    time.sleep(0.05)
                    os.write(master_fd, ("\t".join(status_fields) + "\r\n").encode("ascii"))
            except OSError:
                pass

        responder = threading.Thread(target=fake_avr, daemon=True)
        responder.start()
        bridge = AvrSerialBridge(
            device=device,
            enabled=True,
            safe_start=True,
            calibration=self.calibration,
        )
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not bridge.status()["connected"]:
                time.sleep(0.02)
            self.assertEqual(received, [
                "P999",
                "P80 18996.080294 0.000000 0.81854 1.70194 100.00",
            ])
            self.assertTrue(bridge.status()["safeStartComplete"])
            self.assertTrue(bridge.status()["connected"])
        finally:
            bridge.stop()
            os.close(slave_fd)
            os.close(master_fd)

    def test_machine_api_accepts_semantic_commands_only(self):
        bridge = ConnectedRecordingBridge()
        with patch("editor_backend.AVR_BRIDGE", bridge):
            response = app.test_client().post(
                "/api/machine/command",
                json={"device": "mash_heater", "target_C": 68},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(bridge.payloads, ["P150 680"])
            self.assertEqual(response.get_json()["data"]["heaters"]["mash_heater"]["targetC"], 68)

            response = app.test_client().post(
                "/api/machine/command",
                json={"device": "not_a_device", "action": "on"},
            )
            self.assertEqual(response.status_code, 502)

            response = app.test_client().post(
                "/api/machine/command",
                json={"command": "close_all"},
                headers={"Origin": "http://untrusted.example"},
            )
            self.assertEqual(response.status_code, 403)

    def test_machine_mutations_accept_localhost_loopback_alias(self):
        bridge = ConnectedRecordingBridge()
        with patch("editor_backend.AVR_BRIDGE", bridge):
            response = app.test_client().post(
                "/api/machine/command",
                base_url="http://127.0.0.1:8081",
                json={"command": "close_all"},
                headers={"Origin": "http://localhost:5173"},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(bridge.payloads, ["P999"])


if __name__ == "__main__":
    unittest.main()
