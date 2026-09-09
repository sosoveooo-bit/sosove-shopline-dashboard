from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(__file__).resolve().parent / "runtime_logs"


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    output_path = LOG_DIR / f"scheduled-{stamp}.out.log"
    error_path = LOG_DIR / f"scheduled-{stamp}.err.log"

    os.chdir(PROJECT_DIR)
    with output_path.open("a", encoding="utf-8", buffering=1) as output, error_path.open(
        "a", encoding="utf-8", buffering=1
    ) as error:
        sys.stdout = output
        sys.stderr = error
        print(f"[{datetime.now().isoformat()}] Starting scheduled Shopline dashboard")
        from shopline_monitor.server import run

        run("127.0.0.1", 8787)


if __name__ == "__main__":
    main()
