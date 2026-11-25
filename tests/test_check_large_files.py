"""Tests for scripts/check_large_files.py."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_large_files.py"
spec = importlib.util.spec_from_file_location("check_large_files", SCRIPT)
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
sys.modules["check_large_files"] = check
spec.loader.exec_module(check)


def test_small_file_passes(tmp_path: Path) -> None:
    small = tmp_path / "a.txt"
    small.write_bytes(b"x" * 1024)
    assert check.main([str(small)]) == 0


def test_large_file_fails(tmp_path: Path) -> None:
    big = tmp_path / "b.bin"
    big.write_bytes(b"x" * (501 * 1024))
    assert check.main([str(big)]) == 1
    assert check.find_large([str(big)]) == [(str(big), 501)]


def test_limit_is_configurable(tmp_path: Path) -> None:
    f = tmp_path / "c.bin"
    f.write_bytes(b"x" * (10 * 1024))
    assert check.main([str(f), "--maxkb", "5"]) == 1
    assert check.main([str(f), "--maxkb", "50"]) == 0


def test_missing_file_is_ignored(tmp_path: Path) -> None:
    assert check.main([str(tmp_path / "deleted.txt")]) == 0
