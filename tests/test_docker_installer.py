import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("configure_docker", ROOT / "deploy" / "configure_docker.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class DockerInstallerConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sosove-installer-test-")
        self.path = Path(self.temp.name)
        (self.path / ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        self.env = {
            "SHOPLINE_API_BASE_URL": "https://example.myshopline.com/",
            "SHOPLINE_ACCESS_TOKEN": "ci-only-shopline-token",
            "DASHBOARD_ACCESS_TOKEN": "ci-only-password-$-literal",
            "DASHBOARD_PORT": "18001",
            "DASHBOARD_BIND_IP": "127.0.0.1",
        }

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            values = config.configure(self.path, noninteractive=True)
        for key in ("SHOPLINE_ACCESS_TOKEN", "DASHBOARD_ACCESS_TOKEN"):
            self.assertNotIn(self.env[key], output.getvalue())
        return values

    def test_new_configuration_is_quoted_and_ga4_is_optional(self):
        values = self.install()
        self.assertEqual(values["SHOPLINE_API_BASE_URL"], "https://example.myshopline.com")
        self.assertEqual(values["DASHBOARD_ACCESS_TOKEN"], self.env["DASHBOARD_ACCESS_TOKEN"])
        self.assertEqual(values["DASHBOARD_BIND_IP"], "127.0.0.1")
        self.assertEqual(values["GA4_PROPERTY_ID"], "")
        self.assertEqual(values["GA4_SERVICE_ACCOUNT_FILE"], "")
        if os.name != "nt":
            self.assertEqual((self.path / ".env").stat().st_mode & 0o777, 0o600)

    def test_second_run_preserves_existing_configuration_and_password(self):
        self.install()
        path = self.path / ".env"
        with path.open("a", encoding="utf-8") as stream:
            stream.write("# keep customized settings\nCUSTOM_VALUE=something\n")
        original = path.read_bytes()
        self.env["DASHBOARD_ACCESS_TOKEN"] = "different-password-must-not-replace"
        self.install()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.path.glob(".env.backup-*")))

    def test_partial_existing_configuration_is_backed_up_before_completion(self):
        original = "# existing secrets\nSHOPLINE_ACCESS_TOKEN='original-api-token'\nGA4_PROPERTY_ID=123456789\nGA4_SERVICE_ACCOUNT_FILE=/app/secrets/ga.json\n"
        (self.path / ".env").write_text(original, encoding="utf-8")
        values = self.install()
        self.assertEqual(values["SHOPLINE_ACCESS_TOKEN"], "original-api-token")
        self.assertEqual(values["GA4_PROPERTY_ID"], "123456789")
        self.assertEqual(values["GA4_SERVICE_ACCOUNT_FILE"], "/app/secrets/ga.json")
        backup = list(self.path.glob(".env.backup-*"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_text(encoding="utf-8"), original)

    def test_missing_credentials_fail_without_creating_env(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            config.configure(self.path, noninteractive=True)
        self.assertFalse((self.path / ".env").exists())

    def test_interactive_secrets_use_hidden_prompts_and_do_not_print(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.input", side_effect=["example.myshopline.com", "", "0.0.0.0"]), patch.object(
            config.getpass, "getpass", side_effect=["hidden-api-token", "hidden-panel-password"]
        ) as hidden, contextlib.redirect_stdout(io.StringIO()) as output:
            values = config.configure(self.path)
        self.assertEqual(hidden.call_count, 2)
        self.assertEqual(values["DASHBOARD_BIND_IP"], "0.0.0.0")
        self.assertNotIn("hidden-api-token", output.getvalue())
        self.assertNotIn("hidden-panel-password", output.getvalue())

    def test_validation_rejects_unsafe_domains_and_multiline_secrets(self):
        for domain in ("http://example.myshopline.com", "evil.example", "https://user:password@example.myshopline.com", "example.myshopline.com/path", "example.myshopline.com?token=bad"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                config.store_domain(domain)
        for value in ("token\nOTHER_KEY=x", "a'b", 'a"b', "a\\b"):
            with self.assertRaises(ValueError):
                config.credential(value)
        for port in ("0", "80", "65536", "8000; echo fail"):
            with self.assertRaises(ValueError):
                config.port_value(port)


class DockerInstallerShellTests(unittest.TestCase):
    def setUp(self):
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        self.bash = str(git_bash) if os.name == "nt" and git_bash.exists() else shutil.which("bash")
        if not self.bash:
            self.skipTest("bash is required")

    def test_shell_syntax(self):
        result = subprocess.run([self.bash, "-n", "deploy/install_docker.sh"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def shell(self, script):
        return subprocess.run([self.bash, "-c", "source deploy/install_docker.sh; " + script], cwd=ROOT, capture_output=True, text=True)

    def test_debian_bookworm_and_ubuntu_use_their_own_docker_repository(self):
        for system, version, codename in (("debian", "12", "bookworm"), ("debian", "13", "trixie"), ("ubuntu", "24.04", "noble")):
            with self.subTest(system=system, version=version):
                result = self.shell(f"ID={system}; VERSION_ID={version}; VERSION_CODENAME={codename}; select_distribution; printf '%s' \"$DOCKER_DISTRO\"")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, system)

    def test_ready_nas_docker_never_calls_package_manager_or_service_manager(self):
        result = self.shell('''
SOSOVE_REUSE_DOCKER=1
docker() { case "$*" in 'compose version'|info) return 0;; *) return 99;; esac; }
apt-get() { echo 'unexpected package installation' >&2; return 99; }
systemctl() { echo 'unexpected service restart' >&2; return 99; }
ensure_docker
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unexpected", result.stderr)

    def test_nas_mode_fails_without_restarting_an_unavailable_daemon(self):
        result = self.shell('''
SOSOVE_REUSE_DOCKER=1
docker() { [[ "$*" == 'compose version' ]]; }
systemctl() { echo 'unexpected service restart' >&2; return 99; }
ensure_docker
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("unexpected", result.stderr)
        self.assertIn("NAS", result.stderr)

    def test_native_build_platform_is_derived_from_docker_server(self):
        for architecture, expected in (("aarch64", "linux/arm64"), ("arm64", "linux/arm64"), ("x86_64", "linux/amd64")):
            with self.subTest(architecture=architecture):
                result = self.shell(f"docker() {{ printf '{architecture}'; }}; select_build_platform; printf '%s' \"$DOCKER_DEFAULT_PLATFORM\"")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.endswith(expected))

    def test_other_operating_systems_still_fail_closed(self):
        result = self.shell('ID=fedora; VERSION_ID=40; VERSION_CODENAME=unknown; select_distribution')
        self.assertNotEqual(result.returncode, 0)

    def test_nas_data_volume_root_is_rejected(self):
        result = self.shell('INSTALL_DIR=/vol1; validate_directory')
        self.assertNotEqual(result.returncode, 0)

    def test_broad_directory_is_rejected_without_mutation(self):
        result = subprocess.run([self.bash, "-c", "source deploy/install_docker.sh; INSTALL_DIR=/opt; validate_directory"], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing a broad system directory", result.stderr)

    def test_nonempty_directory_is_never_removed_or_cloned_over(self):
        with tempfile.TemporaryDirectory(prefix="sosove-shell-test-") as directory:
            marker = Path(directory) / "existing.txt"
            marker.write_text("preserve", encoding="utf-8")
            shell_path = Path(directory).as_posix()
            if os.name == "nt":
                shell_path = "/" + shell_path[0].lower() + shell_path[2:]
            environment = dict(os.environ, SOSOVE_TEST_DIRECTORY=shell_path)
            result = subprocess.run([self.bash, "-c", 'source deploy/install_docker.sh; INSTALL_DIR="$SOSOVE_TEST_DIRECTORY"; validate_directory; sync_repository'], cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Destination is not empty", result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
