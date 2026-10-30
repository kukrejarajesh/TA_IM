import json
from pathlib import Path
from typing import Any

import pytest

FEATURE_LIST = Path(__file__).resolve().parent.parent / "feature_list.json"
EXPECTED_COUNT = 43
REQUIRED_FIELDS = {
    "id",
    "epic",
    "sprint",
    "title",
    "acceptance",
    "requirements",
    "size",
    "test",
    "depends_on",
    "passes",
}


@pytest.fixture(scope="module")
def features() -> list[dict[str, Any]]:
    data = json.loads(FEATURE_LIST.read_text(encoding="utf-8"))
    features = data["features"]
    assert isinstance(features, list)
    return features


def test_feature_count(features: list[dict[str, Any]]) -> None:
    assert len(features) == EXPECTED_COUNT


def test_ids_unique(features: list[dict[str, Any]]) -> None:
    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids))


def test_required_fields(features: list[dict[str, Any]]) -> None:
    for f in features:
        missing = REQUIRED_FIELDS - f.keys()
        assert not missing, f"{f.get('id')}: missing {sorted(missing)}"


def test_depends_on_appears_earlier(features: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for f in features:
        for dep in f["depends_on"]:
            assert dep in seen, f"{f['id']} depends on {dep}, which is not earlier in the list"
        seen.add(f["id"])
