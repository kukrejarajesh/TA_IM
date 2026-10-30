"""Bootstrap the dev environment. Standard library only; safe to re-run."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)


def run(*cmd: str) -> None:
    print(f"==> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} required, found {sys.version.split()[0]}")
        return 1
    if shutil.which("uv") is None:
        print("uv not found; install it from https://docs.astral.sh/uv/")
        return 1

    run("uv", "sync")

    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists():
        print(".env already exists; leaving it untouched")
    else:
        shutil.copyfile(example, env)
        print("created .env from .env.example; add your key there")

    run("uv", "run", "pre-commit", "install")
    run("uv", "run", "python", "-c", "import ta_in; print('ta_in', ta_in.__version__)")
    run("uv", "run", "pytest", "tests/test_smoke.py", "-q")
    print("init: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
