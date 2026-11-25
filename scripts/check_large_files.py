"""Pre-commit hook: fail when a staged file is larger than a size limit (default 500 KB).

Replaces pre-commit-hooks' `check-added-large-files`, whose pre-commit-built executable is
blocked by Windows Application Control on the dev machine (WinError 4551). Same rule, runs
through the repo's own Python like the mypy hook.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MAX_KB = 500


def find_large(paths: list[str], max_kb: int = DEFAULT_MAX_KB) -> list[tuple[str, int]]:
    """Return (path, size in KB) for every existing file above the limit."""
    large: list[tuple[str, int]] = []
    for name in paths:
        path = Path(name)
        if path.is_file() and path.stat().st_size > max_kb * 1024:
            large.append((name, path.stat().st_size // 1024))
    return large


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filenames", nargs="*")
    parser.add_argument("--maxkb", type=int, default=DEFAULT_MAX_KB)
    args = parser.parse_args(argv)
    large = find_large(args.filenames, args.maxkb)
    for name, kb in large:
        print(f"{name} ({kb} KB) exceeds {args.maxkb} KB", file=sys.stderr)
    return 1 if large else 0


if __name__ == "__main__":
    sys.exit(main())
