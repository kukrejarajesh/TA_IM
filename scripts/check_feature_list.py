"""Guard feature_list.json: against HEAD, only false->true changes of "passes" are allowed."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PATH = "feature_list.json"


def strip_passes(data: dict[str, Any]) -> dict[str, Any]:
    stripped = copy.deepcopy(data)
    for feature in stripped.get("features", []):
        feature.pop("passes", None)
    return stripped


def passes_by_id(data: dict[str, Any]) -> dict[str, Any]:
    return {f["id"]: f.get("passes") for f in data.get("features", [])}


def check(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if strip_passes(old) != strip_passes(new):
        errors.append('something other than a "passes" value changed')
    old_passes, new_passes = passes_by_id(old), passes_by_id(new)
    for fid, was in old_passes.items():
        if was is True and new_passes.get(fid) is not True:
            errors.append(f'{fid}: "passes" changed from true to false')
    return errors


def head_version() -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{PATH}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        return None  # not in HEAD yet (first commit of the file)
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def main() -> int:
    old = head_version()
    if old is None:
        print(f"{PATH} not in HEAD; nothing to compare")
        return 0
    new: dict[str, Any] = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
    errors = check(old, new)
    for e in errors:
        print(f"{PATH}: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
