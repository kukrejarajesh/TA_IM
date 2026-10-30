"""Run the full verification suite, stopping at the first failure."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS: list[list[str]] = [
    [sys.executable, "-m", "ruff", "format", "--check"],
    [sys.executable, "-m", "ruff", "check"],
    [sys.executable, "-m", "mypy", "src"],
    [sys.executable, "-m", "pytest", "-m", "not live"],
]


def main() -> int:
    for cmd in STEPS:
        label = " ".join(cmd[2:])
        print(f"==> {label}", flush=True)
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print(f"FAILED: {label}", file=sys.stderr)
            return result.returncode
    print("verify: all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
