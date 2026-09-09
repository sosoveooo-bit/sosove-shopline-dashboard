"""Run inside the CI image as its default user; never needs real credentials."""
import os
import stat
from pathlib import Path


assert os.geteuid() == 10001, "The runtime must remain non-root"
root = Path("/app")
package = root / "shopline_monitor"
for directory in [root, package, *[path for path in package.rglob("*") if path.is_dir()]]:
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755, str(directory)
for path in [root / "app.py", *[path for path in package.rglob("*") if path.is_file()]]:
    assert stat.S_IMODE(path.stat().st_mode) == 0o644, str(path)
    assert path.stat().st_uid == 0, str(path)
    assert not os.access(path, os.W_OK), str(path)
    with path.open("rb") as stream:
        stream.read(1)
assert not (root / ".env").exists()
assert not (root / "secrets" / "ga.json").exists()
import app  # noqa: E402

assert app.app is not None
print("Restricted-source permission test passed: UID 10001 reads code, cannot write it, and imports the app.")
