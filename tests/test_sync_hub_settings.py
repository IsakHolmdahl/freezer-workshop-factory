import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "sync_hub_settings", Path(__file__).resolve().parents[1] / "scripts" / "sync-hub-settings.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class SyncHubSettingsTests(unittest.TestCase):
    def run_sync(self, body, remote, *arguments, put_status=200):
        session = Mock()
        session.headers = {}
        session.get.return_value = Mock(status_code=200)
        session.get.return_value.json.return_value = remote
        session.put.return_value = Mock(status_code=put_status)
        session.put.return_value.json.return_value = {"status": "saved"}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.yaml"
            settings.write_text(body)
            with patch.object(sync.requests, "Session") as factory:
                factory.return_value.__enter__.return_value = session
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    status = sync.main(["--settings", str(settings), *arguments])
        return status, session, output.getvalue()

    def test_sync_sends_complete_selected_maps_only(self):
        status, session, output = self.run_sync(
            "profiles:\n  local:\n    runtime: docker\nserver:\n  auth:\n    token: PRIVATE\n",
            {"profiles": {"local": {"runtime": "old"}, "hub-only": {}}})
        self.assertEqual(status, 0)
        self.assertEqual(session.put.call_args.kwargs["json"], {
            "profiles": {"local": {"runtime": "docker"}}})
        self.assertIn("Remove Hub-only entries: hub-only", output)
        self.assertNotIn("PRIVATE", output)

    def test_dry_run_reports_changes_without_writing_or_exposing_values(self):
        status, session, output = self.run_sync(
            "harness_configs:\n  asf-opencode:\n    env:\n      TOKEN: PRIVATE\n", {}, "--dry-run")
        self.assertEqual(status, 0)
        session.put.assert_not_called()
        self.assertNotIn("PRIVATE", output)

    def test_identical_settings_skip_update(self):
        status, session, _ = self.run_sync("profiles: {local: {runtime: docker}}",
                                         {"profiles": {"local": {"runtime": "docker"}}})
        self.assertEqual(status, 0)
        session.put.assert_not_called()

    def test_harness_default_is_synced_to_hub_agent_defaults(self):
        status, session, _ = self.run_sync(
            "default_harness_config: asf-opencode\n", {})
        self.assertEqual(status, 0)
        self.assertEqual(session.put.call_args.kwargs["json"], {
            "default_harness_config": "asf-opencode"})

    def test_invalid_harness_defaults_never_contact_hub(self):
        for value in ("null", "{}", "[]", "42", "''"):
            with self.subTest(value=value):
                status, session, _ = self.run_sync(f"default_harness_config: {value}\n", {})
                self.assertEqual(status, 1)
                session.get.assert_not_called()

    def test_explicit_empty_map_clears_section_and_selection_limits_payload(self):
        status, session, _ = self.run_sync("profiles: {}\nruntimes: {docker: {type: docker}}",
                                         {"profiles": {"local": {}}}, "--sections", "profiles")
        self.assertEqual(status, 0)
        self.assertEqual(session.put.call_args.kwargs["json"], {"profiles": {}})

    def test_invalid_yaml_and_null_maps_never_contact_hub(self):
        for body in ("profiles: [", "profiles: null", "profiles: []", "- PRIVATE", "server: {}"):
            with self.subTest(body=body):
                status, session, _ = self.run_sync(body, {})
                self.assertEqual(status, 1)
                session.get.assert_not_called()
                session.put.assert_not_called()

    def test_failed_save_returns_failure_without_printing_response(self):
        status, session, output = self.run_sync("profiles: {}", {"profiles": {"local": {}}},
                                              put_status=422)
        self.assertEqual(status, 1)
        self.assertIn("HTTP 422", output)
        session.put.return_value.json.assert_not_called()

    def test_auth_token_is_sent_in_header(self):
        with patch.dict(sync.os.environ, {"SCION_HUB_TOKEN": "PRIVATE"}):
            status, session, output = self.run_sync("profiles: {}", {})
        self.assertEqual(status, 0)
        self.assertEqual(session.headers["Authorization"], "Bearer PRIVATE")
        self.assertNotIn("PRIVATE", output)

    def test_explicit_default_does_not_read_yaml_or_replace_other_settings(self):
        status, session, _ = self.run_sync(
            "not: [valid YAML", {"profiles": {"custom": {}}, "default_harness_config": "antigravity"},
            "--default-harness-config", "asf-opencode-python")
        self.assertEqual(status, 0)
        self.assertEqual(session.put.call_args.kwargs["json"], {"default_harness_config": "asf-opencode-python"})

    def test_local_dev_token_file_is_used_for_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "dev-token"
            token.write_text("scion_dev_PRIVATE\n")
            with patch.dict(sync.os.environ, {"SCION_HUB_TOKEN": "", "SCION_DEV_TOKEN": "",
                                               "SCION_DEV_TOKEN_FILE": str(token)}):
                status, session, output = self.run_sync("default_harness_config: asf-opencode\n", {})
        self.assertEqual(status, 0)
        self.assertEqual(session.headers["Authorization"], "Bearer scion_dev_PRIVATE")
        self.assertNotIn("PRIVATE", output)


if __name__ == "__main__":
    unittest.main()
