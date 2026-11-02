import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "raspberry_pi" / "automaten_server.py"
SPEC = importlib.util.spec_from_file_location("automaten_server_under_test", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class FakeSerial:
    def __init__(self):
        self.lines = []
        self.closed = False

    def write(self, data):
        self.lines.append(data.decode("utf-8").strip())

    def flush(self):
        pass

    def close(self):
        self.closed = True


class AutomatenServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.originals = {
            name: getattr(server, name)
            for name in (
                "state", "STATE_FILE", "ser", "mega_ready", "vend_in_progress",
                "service_mode", "selected_slot", "selected_quantity", "pending_dispenses",
                "active_dispense_slot", "selection_changed_at", "last_key_on",
                "last_pong_at", "persistence_error", "startup_animation_finished",
            )
        }
        server.state = copy.deepcopy(server.DEFAULT_STATE)
        server.STATE_FILE = os.path.join(self.temp_dir.name, "automaten_state.json")
        server.ser = FakeSerial()
        server.mega_ready = True
        server.vend_in_progress = False
        server.service_mode = False
        server.selected_slot = None
        server.selected_quantity = 0
        server.pending_dispenses = 0
        server.active_dispense_slot = None
        server.selection_changed_at = 0.0
        server.last_pong_at = 0.0
        server.persistence_error = ""
        server.last_key_on = False
        server.startup_animation_finished = False
        server.state["slots"][0].update(stock=3, price=5)
        server.state["users"] = [{
            "name": "admin",
            "password": server.password_hash("password123"),
            "role": "admin",
        }]
        server.state["admin_pin_hash"] = server.password_hash("1234")
        server.app.config.update(TESTING=True)
        self.client = server.app.test_client()
        with self.client.session_transaction() as session:
            session["user"] = "admin"

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.temp_dir.cleanup()

    def test_paid_vend_updates_stock_and_revenue_once(self):
        server.handle_line("BUTTON 1")
        server.handle_line("COIN 5")

        self.assertEqual(server.state["slots"][0]["stock"], 2)
        self.assertEqual(server.state["revenue"], 5)
        self.assertTrue(server.vend_in_progress)
        self.assertIn("DISPENSE 1", server.ser.lines)

        server.handle_line("OK 1")

        self.assertFalse(server.vend_in_progress)
        self.assertIsNone(server.active_dispense_slot)
        self.assertEqual(server.state["slots"][0]["stock"], 2)

    def test_multi_item_vend_continues_if_key_changes_mid_dispense(self):
        server.state["slots"][0].update(stock=2, price=5)
        server.handle_line("BUTTON 1")
        server.handle_line("BUTTON 1")
        server.ser.lines.clear()
        server.handle_line("COIN 5")
        server.handle_line("COIN 5")

        self.assertEqual(server.pending_dispenses, 2)
        server.handle_line("KEY ON")
        server.handle_line("OK 1")

        self.assertTrue(server.vend_in_progress)
        self.assertEqual(server.pending_dispenses, 1)
        self.assertIn("DISPENSE 1", server.ser.lines)
        server.handle_line("OK 1")
        self.assertFalse(server.vend_in_progress)
        self.assertTrue(server.service_mode)

    def test_unexpected_dispense_acknowledgement_is_ignored(self):
        server.vend_in_progress = True
        server.pending_dispenses = 2
        server.active_dispense_slot = 0

        server.handle_line("OK 2")
        server.handle_line("OK invalid")

        self.assertEqual(server.pending_dispenses, 2)
        self.assertTrue(server.vend_in_progress)

    def test_free_vend_is_blocked_while_sales_are_enabled(self):
        response = self.client.post("/tech/free-vend/1", data={"pin": "1234"})

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("DISPENSE 1", server.ser.lines)

    def test_free_vend_requires_maintenance_mode(self):
        server.service_mode = True

        response = self.client.post("/tech/free-vend/1", data={"pin": "1234"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(server.vend_in_progress)
        self.assertEqual(server.pending_dispenses, 1)
        self.assertEqual(server.active_dispense_slot, 0)
        self.assertIn("DISPENSE 1", server.ser.lines)

    def test_controls_cannot_cancel_an_active_vend(self):
        server.vend_in_progress = True
        server.pending_dispenses = 2
        server.active_dispense_slot = 0

        self.client.post("/toggle")
        self.client.post("/reset-credit")

        self.assertTrue(server.state["enabled"])
        self.assertTrue(server.vend_in_progress)
        self.assertEqual(server.pending_dispenses, 2)

    def test_ready_restores_saved_servo_settings(self):
        server.state["servo"][0] = {"home": 10, "output": 120, "out_ms": 600, "back_ms": 700}

        server.handle_line("READY")

        self.assertIn("SERVO 1 10 120 600 700", server.ser.lines)
        self.assertTrue(server.mega_ready)

    def test_heartbeat_response_updates_connection_watchdog(self):
        before = server.last_pong_at

        server.handle_line("PONG")

        self.assertGreater(server.last_pong_at, before)

    def test_state_save_is_atomic_and_does_not_restore_credit(self):
        server.state["credit"] = 15
        server.state["servo"][0]["output"] = 110
        server.save_state()

        saved = json.loads(Path(server.STATE_FILE).read_text(encoding="utf-8"))
        self.assertNotIn("credit", saved)
        server.state = copy.deepcopy(server.DEFAULT_STATE)
        server.load_state()

        self.assertEqual(server.state["credit"], 0)
        self.assertEqual(server.state["servo"][0]["output"], 110)
        self.assertEqual(list(Path(self.temp_dir.name).glob("*.tmp")), [])

    def test_corrupt_state_fails_loudly_without_overwriting_file(self):
        Path(server.STATE_FILE).write_text("{", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            server.load_state()

        self.assertEqual(Path(server.STATE_FILE).read_text(encoding="utf-8"), "{")

    def test_incomplete_state_is_not_treated_as_fresh_install(self):
        Path(server.STATE_FILE).write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Unvollständige Zustandsdatei"):
            server.load_state()

        self.assertEqual(Path(server.STATE_FILE).read_text(encoding="utf-8"), "{}")

    def test_storage_failure_preserves_state_and_blocks_sales(self):
        server.save_state()
        original = Path(server.STATE_FILE).read_text(encoding="utf-8")
        server.state["slots"][0]["stock"] = 4
        server.selected_slot = 0
        server.selected_quantity = 1

        with patch.object(server.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                server.save_state()

        self.assertEqual(Path(server.STATE_FILE).read_text(encoding="utf-8"), original)
        self.assertTrue(server.persistence_error)
        server.ser.lines.clear()
        server.handle_line("COIN 5")

        self.assertEqual(server.state["revenue"], 0)
        self.assertEqual(server.state["slots"][0]["stock"], 4)
        self.assertNotIn("DISPENSE 1", server.ser.lines)

    def test_storage_error_can_be_cleared_after_disk_recovery(self):
        server.persistence_error = "Speicherfehler"
        server.service_mode = True

        response = self.client.post("/tech/acknowledge-hardware", data={"pin": "1234"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(server.persistence_error, "")

    def test_setting_stock_below_selected_quantity_cancels_selection(self):
        server.selected_slot = 0
        server.selected_quantity = 2
        server.state["credit"] = 5

        self.client.post("/slot/1", data={"name": "Fach 1", "stock": "1", "price": "5"})

        self.assertIsNone(server.selected_slot)
        self.assertEqual(server.selected_quantity, 0)
        self.assertEqual(server.state["credit"], 0)

    def test_hardware_disconnect_during_vend_requires_manual_reconciliation(self):
        connection = server.ser
        server.vend_in_progress = True
        server.pending_dispenses = 2
        server.active_dispense_slot = 0

        server.serial_disconnected(connection, OSError("USB removed"))

        self.assertTrue(connection.closed)
        self.assertFalse(server.mega_ready)
        self.assertFalse(server.vend_in_progress)
        self.assertIn("Bestand prüfen", server.state["hardware_error"])
        self.assertFalse(server.is_operational())
        self.assertTrue(Path(server.STATE_FILE).exists())

    def test_disconnect_storage_failure_keeps_hardware_error_active(self):
        connection = server.ser
        server.vend_in_progress = True
        server.pending_dispenses = 1
        server.active_dispense_slot = 0

        with patch.object(server.os, "replace", side_effect=OSError("disk full")):
            server.serial_disconnected(connection, OSError("USB removed"))

        self.assertIsNone(server.ser)
        self.assertFalse(server.vend_in_progress)
        self.assertIn("Bestand prüfen", server.state["hardware_error"])
        self.assertTrue(server.persistence_error)

    def test_admin_can_acknowledge_hardware_error_only_after_reconnect(self):
        server.state["hardware_error"] = "Prüfung erforderlich"
        server.service_mode = True
        server.ser = None
        server.mega_ready = False

        rejected = self.client.post("/tech/acknowledge-hardware", data={"pin": "1234"})
        self.assertEqual(rejected.status_code, 302)
        self.assertTrue(server.state["hardware_error"])

        server.ser = FakeSerial()
        server.mega_ready = True
        accepted = self.client.post("/tech/acknowledge-hardware", data={"pin": "1234"})

        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(server.state["hardware_error"], "")
        self.assertIn("Hardware-Fehler nach Prüfung bestätigt", [
            item["action"] for item in server.state["audit"]
        ])

    def test_default_runtime_state_does_not_share_mutable_defaults(self):
        server.state["slots"][0]["stock"] = 99

        self.assertEqual(server.DEFAULT_STATE["slots"][0]["stock"], 0)

    def test_offline_hardware_is_not_reported_ready(self):
        server.mega_ready = False

        self.assertFalse(server.is_operational())
        self.assertEqual(self.client.get("/api/status").json["mega"], "nicht verbunden")

    def test_missing_mega_does_not_prevent_server_startup(self):
        server.ser = None
        server.mega_ready = False

        with patch.object(server.serial, "Serial", side_effect=OSError("port missing")):
            self.assertFalse(server.connect_serial())

        self.assertIsNone(server.ser)
        self.assertFalse(server.mega_ready)

    def test_system_actions_require_admin_pin_and_use_noninteractive_sudo(self):
        with patch.object(server.subprocess, "run") as run:
            response = self.client.post("/system/reboot", data={"pin": "bad"})
            self.assertEqual(response.status_code, 302)
            run.assert_not_called()

            response = self.client.post("/system/reboot", data={"pin": "1234"})

        self.assertEqual(response.status_code, 302)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["sudo", "-n", "/usr/local/sbin/maom-system-action", "reboot"],
        )

    def test_unconfigured_system_action_returns_clear_error(self):
        with patch.object(server.subprocess, "run", side_effect=FileNotFoundError("missing")):
            response = self.client.post("/system/shutdown", data={"pin": "1234"})

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"docs/BUILD.md", response.data)

    def test_service_restart_route_reports_that_it_is_not_implemented(self):
        response = self.client.post("/system/restart-service", data={"pin": "1234"})

        self.assertEqual(response.status_code, 501)
        self.assertIn(b"nicht eingerichtet", response.data)

    def test_dashboard_pages_render(self):
        for page in ("home", "products", "service", "tech", "admin"):
            with self.subTest(page=page):
                self.assertEqual(self.client.get("/?page=" + page).status_code, 200)

    def test_first_admin_setup_and_login_flow(self):
        server.state["users"] = []
        server.state["admin_pin_hash"] = ""
        with self.client.session_transaction() as session:
            session.clear()

        self.assertEqual(self.client.get("/").status_code, 302)
        response = self.client.post("/setup", data={
            "name": "newadmin",
            "password": "securepass123",
            "pin": "5678",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(server.state["users"][0]["role"], "admin")
        self.assertTrue(server.password_matches("5678", server.state["admin_pin_hash"]))
        self.client.post("/logout")
        response = self.client.post("/login", data={
            "name": "newadmin",
            "password": "securepass123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/status").status_code, 200)


if __name__ == "__main__":
    unittest.main()
