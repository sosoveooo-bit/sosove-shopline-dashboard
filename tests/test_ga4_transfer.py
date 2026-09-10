import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy import ga4_transfer as transfer
from deploy.configure_docker import parse_values


def fixture_payload():
    return {"format": transfer.FORMAT, "property_id": "123456789", "key_event_name": "purchase",
            "conversion_metric": "userKeyEventRate", "credentials": {
                "type": "service_account", "private_key": "CI-only-not-a-real-key", "client_email": "ci@example.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token"}}


class Ga4TransferTests(unittest.TestCase):
    def test_authenticated_round_trip_and_random_codes(self):
        payload = fixture_payload()
        token, code = transfer.seal(payload)
        self.assertEqual(transfer.open_bundle(token, code), payload)
        self.assertNotIn(payload["credentials"]["client_email"].encode(), token)
        self.assertNotIn(payload["credentials"]["private_key"].encode(), token)
        self.assertNotEqual(transfer.seal(payload)[1], code)

    def test_wrong_code_and_tampering_are_rejected(self):
        token, code = transfer.seal(fixture_payload())
        with self.assertRaises(ValueError):
            transfer.open_bundle(token, transfer.seal(fixture_payload())[1])
        altered = bytearray(token)
        altered[50] = ord("A") if altered[50] != ord("A") else ord("B")
        with self.assertRaises(ValueError):
            transfer.open_bundle(bytes(altered), code)

    def test_docker_helper_has_no_network_logs_mounts_or_secret_arguments(self):
        token, code = transfer.seal(fixture_payload())
        result = subprocess.CompletedProcess([], 0, json.dumps(fixture_payload()).encode(), b"")
        with patch.object(transfer.subprocess, "run", return_value=result) as run:
            self.assertEqual(transfer.decrypt_with_docker(token, code, "a" * 64), fixture_payload())
        command = run.call_args.args[0]
        self.assertNotIn(code, " ".join(command))
        self.assertNotIn(token.decode(), " ".join(command))
        self.assertIn("--read-only", command)
        self.assertNotIn("--mount", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--log-driver") + 1], "none")
        self.assertNotIn("env", run.call_args.kwargs)

    def test_invalid_payload_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = fixture_payload()
            invalid["credentials"]["token_uri"] = "https://untrusted.example/token"
            with self.assertRaises(ValueError):
                transfer.install_payload(Path(directory), invalid)
            self.assertFalse((Path(directory) / "secrets").exists())

    def test_install_preserves_other_settings_and_backs_up_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(transfer.os, "chown", create=True):
            project = Path(directory)
            original_env = "SHOPLINE_ACCESS_TOKEN=keep-existing\nDASHBOARD_PORT=8001\nGA4_SERVICE_ACCOUNT_JSON=old-inline-value\n"
            (project / ".env").write_text(original_env, encoding="utf-8")
            (project / "secrets").mkdir()
            (project / "secrets/ga.json").write_text("old-key", encoding="utf-8")
            transfer.install_payload(project, fixture_payload())
            values = parse_values((project / ".env").read_text(encoding="utf-8"))
            self.assertEqual(values["SHOPLINE_ACCESS_TOKEN"], "keep-existing")
            self.assertEqual(values["DASHBOARD_PORT"], "8001")
            self.assertEqual(values["GA4_PROPERTY_ID"], "123456789")
            self.assertEqual(values["GA4_SERVICE_ACCOUNT_FILE"], "/app/secrets/ga.json")
            self.assertEqual(values["GA4_SERVICE_ACCOUNT_JSON"], "")
            self.assertEqual(json.loads((project / "secrets/ga.json").read_text()), fixture_payload()["credentials"])
            self.assertEqual(next((project / "secrets").glob("ga.json.backup-*")).read_text(), "old-key")
            self.assertEqual(next(project.glob(".env.backup-*")).read_text(), original_env)
            if os.name == "posix":
                self.assertEqual((project / ".env").stat().st_mode & 0o777, 0o600)
                self.assertEqual((project / "secrets/ga.json").stat().st_mode & 0o777, 0o640)

    def test_permissions_are_applied_after_nas_chown(self):
        events = []
        fake_os = Mock(wraps=os)
        fake_os.name = "posix"
        fake_os.chown = lambda _path, uid, gid: events.append(("chown", uid, gid))
        path = Mock()
        path.chmod.side_effect = lambda mode: events.append(("chmod", mode))
        with patch.object(transfer, "os", fake_os):
            transfer.set_owner_and_mode(path, 0o640)
        self.assertEqual(events, [("chown", 0, 10001), ("chmod", 0o640)])

    def test_config_failure_rolls_back_previous_key_and_env(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(transfer.os, "chown", create=True):
            project = Path(directory)
            (project / ".env").write_bytes(b"EXISTING=keep\n")
            (project / "secrets").mkdir()
            (project / "secrets/ga.json").write_bytes(b"old-key")
            with patch.object(transfer, "write_values", side_effect=OSError("simulated write failure")), self.assertRaises(OSError):
                transfer.install_payload(project, fixture_payload())
            self.assertEqual((project / ".env").read_bytes(), b"EXISTING=keep\n")
            self.assertEqual((project / "secrets/ga.json").read_bytes(), b"old-key")
