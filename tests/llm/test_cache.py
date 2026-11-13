import hashlib
import inspect
import logging
from pathlib import Path
from typing import Any

import anthropic
import pytest
from helpers import FAKE_KEY, MODEL, FakeSDK, make_settings, response

from ta_in.llm import (
    CacheError,
    CacheMiss,
    CacheMode,
    LLMClient,
    ResponseCache,
    cache_key,
)

MSGS = [{"role": "user", "content": "hi"}]
TOOLS = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]


def make_client(
    tmp_path: Path, mode: CacheMode, script: list[Any] | None = None
) -> tuple[LLMClient, FakeSDK]:
    sdk = FakeSDK(script or [])
    client = LLMClient(
        make_settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
    )
    return client, sdk


def dir_digest(path: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.glob("*"))}


# ---- key ----------------------------------------------------------------


def test_key_is_stable_and_order_insensitive() -> None:
    a = cache_key(MODEL, "sys", [{"role": "user", "content": "hi"}], TOOLS, 100)
    b = cache_key(MODEL, "sys", [{"content": "hi", "role": "user"}], TOOLS, 100)
    assert a == b
    assert len(a) == 64


@pytest.mark.parametrize(
    "changed",
    [
        {"model": "claude-haiku-5-5"},
        {"system": "other"},
        {"messages": [{"role": "user", "content": "bye"}]},
        {"tools": None},
        {"max_tokens": 101},
    ],
)
def test_key_changes_with_every_component(changed: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "model": MODEL,
        "system": "sys",
        "messages": MSGS,
        "tools": TOOLS,
        "max_tokens": 100,
    }
    assert cache_key(**base) != cache_key(**{**base, **changed})


def test_key_inputs_are_only_the_request_not_credentials() -> None:
    params = set(inspect.signature(cache_key).parameters)
    assert params == {"model", "system", "messages", "tools", "max_tokens", "tool_choice"}


def test_key_without_tool_choice_is_unchanged_so_old_recordings_still_replay() -> None:
    # Hashes computed before tool_choice existed (S4.2): they must never move.
    assert (
        cache_key(MODEL, "sys", MSGS, TOOLS, 100)
        == "f953eab18f188eda44ae2becf80ee5158bee809c48d47de3c54fae5f54a1c54e"
    )
    assert (
        cache_key(MODEL, "sys", MSGS, None, 16000)
        == "1402c303d2bfb33d964d8604c2fc192b6d23c5493cb3eee523a00b4467b98a99"
    )


def test_key_changes_with_tool_choice() -> None:
    forced = {"type": "tool", "name": "t"}
    plain = cache_key(MODEL, "sys", MSGS, TOOLS, 100)
    assert cache_key(MODEL, "sys", MSGS, TOOLS, 100, forced) != plain
    assert cache_key(MODEL, "sys", MSGS, TOOLS, 100, forced) != cache_key(
        MODEL, "sys", MSGS, TOOLS, 100, {"type": "tool", "name": "u"}
    )


# ---- record / replay ----------------------------------------------------


def test_record_calls_sdk_and_writes_one_file_per_key(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, CacheMode.RECORD, [response("one"), response("two")])
    assert client.complete(MODEL, "sys", MSGS) == "one"
    assert client.complete(MODEL, "sys", [{"role": "user", "content": "other"}]) == "two"
    assert len(sdk.calls) == 2
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


def test_replay_hit_makes_zero_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, _ = make_client(tmp_path, CacheMode.RECORD, [response("recorded")])
    rec.complete(MODEL, "sys", MSGS)

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network/SDK touched during replay")

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    client = LLMClient(
        make_settings(),
        sdk=None,  # replay must not even build a real SDK client
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=CacheMode.REPLAY,
    )
    assert client.complete(MODEL, "sys", MSGS) == "recorded"


def test_replay_miss_raises_cache_miss_and_never_calls_sdk(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, CacheMode.REPLAY, [response()])
    with pytest.raises(CacheMiss) as exc:
        client.complete(MODEL, "sys", MSGS)
    assert MODEL in str(exc.value)
    assert cache_key(MODEL, "sys", MSGS, None, 16000) in str(exc.value)
    assert sdk.calls == []


def test_replayed_run_is_byte_identical_to_recorded_run(tmp_path: Path) -> None:
    prompts = ["alpha", "βeta ₹ 日本語", "line1\nline2\r\nline3"]
    rec, _ = make_client(tmp_path, CacheMode.RECORD, [response(f"out:{p}", 11, 7) for p in prompts])
    recorded = [rec.complete(MODEL, "sys", [{"role": "user", "content": p}]) for p in prompts]
    before = dir_digest(tmp_path / "cache")

    rep, sdk = make_client(tmp_path, CacheMode.REPLAY)
    replayed = [rep.complete(MODEL, "sys", [{"role": "user", "content": p}]) for p in prompts]

    assert [r.encode("utf-8") for r in replayed] == [r.encode("utf-8") for r in recorded]
    assert sdk.calls == []
    assert dir_digest(tmp_path / "cache") == before  # replay never rewrites the cache


def test_cache_files_use_lf_line_endings(tmp_path: Path) -> None:
    rec, _ = make_client(tmp_path, CacheMode.RECORD, [response("x")])
    rec.complete(MODEL, "sys", MSGS)
    for f in (tmp_path / "cache").glob("*.json"):
        assert b"\r" not in f.read_bytes()


def test_off_mode_calls_sdk_and_touches_no_disk(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, CacheMode.OFF, [response("a"), response("b")])
    assert client.complete(MODEL, "sys", MSGS) == "a"
    assert client.complete(MODEL, "sys", MSGS) == "b"
    assert len(sdk.calls) == 2
    assert not (tmp_path / "cache").exists()


def test_tools_are_forwarded_to_sdk_and_part_of_the_key(tmp_path: Path) -> None:
    client, sdk = make_client(tmp_path, CacheMode.RECORD, [response("a"), response("b")])
    client.complete(MODEL, "sys", MSGS, tools=TOOLS)
    client.complete(MODEL, "sys", MSGS)
    assert sdk.calls[0]["tools"] == TOOLS
    assert "tools" not in sdk.calls[1]
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


# ---- records, errors, secrets ------------------------------------------


def test_replayed_calls_are_recorded_as_cached_and_free(tmp_path: Path) -> None:
    rec, _ = make_client(tmp_path, CacheMode.RECORD, [response("x", 1000, 500)])
    rec.complete(MODEL, "sys", MSGS)
    assert rec.records[0].cached is False
    assert rec.spent_usd > 0

    rep, _ = make_client(tmp_path, CacheMode.REPLAY)
    rep.complete(MODEL, "sys", MSGS)
    (r,) = rep.records
    assert r.cached is True
    assert r.cost_usd == 0.0
    assert (r.input_tokens, r.output_tokens) == (1000, 500)
    assert rep.spent_usd == 0.0


@pytest.mark.parametrize("content", ["{not json", '{"version": 1}', ""])
def test_corrupt_cache_file_raises_clear_cache_error(tmp_path: Path, content: str) -> None:
    key = cache_key(MODEL, "sys", MSGS, None, 16000)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / f"{key}.json").write_text(content, encoding="utf-8")
    client, _ = make_client(tmp_path, CacheMode.REPLAY)
    with pytest.raises(CacheError) as exc:
        client.complete(MODEL, "sys", MSGS)
    assert key in str(exc.value)


def test_cache_files_and_logs_never_contain_the_api_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = make_client(tmp_path, CacheMode.RECORD, [response("x")])
    with caplog.at_level(logging.DEBUG):
        client.complete(MODEL, "sys", MSGS)
    blob = b"".join(p.read_bytes() for p in (tmp_path / "cache").glob("*.json"))
    assert FAKE_KEY.encode() not in blob
    assert FAKE_KEY not in caplog.text
