"""CI-only, synthetic credentials. Never reads the published encrypted bundle."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from deploy.ga4_transfer import COMPOSE, FORMAT, seal
from deploy.configure_docker import parse_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0, "Use sudo with the CI Python interpreter for file ownership checks"
    repo = Path(__file__).resolve().parents[1]
    payload = {"format": FORMAT, "property_id": "123456789", "key_event_name": "purchase",
               "conversion_metric": "userKeyEventRate", "credentials": {
                   "type": "service_account", "private_key": "CI-only-not-a-real-key",
                   "client_email": "ci@example.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token"}}
    encrypted, code = seal(payload)
    with tempfile.TemporaryDirectory(prefix="sosove-ga4-ci-") as directory:
        project = Path(directory)
        shutil.copy2(repo / "docker-compose.yml", project / "docker-compose.yml")
        # JSON is a valid YAML subset and avoids passing shell interpolation through an image name.
        (project / "compose.build.yml").write_text(json.dumps({"services": {"dashboard": {"image": args.image}}}))
        (project / "secrets").mkdir()
        env = b"DASHBOARD_ACCESS_TOKEN=ci-ga4-import\nDASHBOARD_PORT=18004\nDASHBOARD_BIND_IP=127.0.0.1\nSHOPLINE_API_BASE_URL=\nSHOPLINE_ACCESS_TOKEN=\n"
        (project / ".env").write_bytes(env)
        bundle = project / "synthetic.fernet"
        bundle.write_bytes(encrypted)
        command = [sys.executable, "-m", "deploy.ga4_transfer", "import", "--project-dir", str(project), "--bundle", str(bundle), "--code-stdin"]
        try:
            subprocess.run(COMPOSE + ["up", "-d", "--pull", "never"], cwd=project, check=True)
            failed = subprocess.run(command, cwd=repo, input=b"incorrect-code\n", capture_output=True, timeout=60)
            assert failed.returncode != 0
            assert (project / ".env").read_bytes() == env
            assert not (project / "secrets/ga.json").exists()
            success = subprocess.run(command, cwd=repo, input=(code + "\n").encode(), capture_output=True, timeout=120)
            assert success.returncode == 0, "Synthetic import did not complete"
            assert code.encode() not in success.stdout + success.stderr
            assert payload["credentials"]["private_key"].encode() not in success.stdout + success.stderr
            values = parse_values((project / ".env").read_text())
            assert values["DASHBOARD_PORT"] == "18004"
            assert values["GA4_PROPERTY_ID"] == "123456789"
            assert values["GA4_SERVICE_ACCOUNT_FILE"] == "/app/secrets/ga.json"
            key_path = project / "secrets/ga.json"
            assert key_path.stat().st_gid == 10001
            assert key_path.stat().st_mode & 0o777 == 0o640
            assert json.loads(key_path.read_text()) == payload["credentials"]
            print("Synthetic encrypted GA4 import passed: wrong-code rejection, Docker decryption, permissions, config preservation and restart.")
        finally:
            subprocess.run(COMPOSE + ["down", "-v"], cwd=project, check=False)


if __name__ == "__main__":
    main()
