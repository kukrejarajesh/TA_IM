"""On-disk response cache for record/replay of LLM calls."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

CACHE_VERSION = 1


class CacheMode(StrEnum):
    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class CacheMiss(Exception):
    """Replay mode found no recorded response for the request."""


class CacheError(Exception):
    """A cache file is unreadable or malformed."""


@dataclass(frozen=True)
class CacheEntry:
    """A recorded response. `content` and `stop_reason` are absent in entries written before
    tool use was cached; such an entry replays as one text block that ended the turn."""

    model: str
    text: str
    input_tokens: int
    output_tokens: int
    content: list[dict[str, Any]] | None = None
    stop_reason: str | None = None


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def cache_key(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    tool_choice: dict[str, Any] | None = None,
) -> str:
    """SHA-256 over the canonical JSON of everything that shapes the response.

    `tool_choice` is part of the key only when set, so every key made before it existed (and so
    every recording) stays valid.
    """
    payload: dict[str, Any] = {
        "v": CACHE_VERSION,
        "model": model,
        "system": system,
        "messages": messages,
        "tools": tools,
        "max_tokens": max_tokens,
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return hashlib.sha256(_canonical(payload)).hexdigest()


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["version"] != CACHE_VERSION or data["key"] != key:
                raise ValueError("version or key mismatch")
            content = data.get("content")
            if content is not None and not isinstance(content, list):
                raise ValueError("content must be a list")
            stop_reason = data.get("stop_reason")
            return CacheEntry(
                model=str(data["model"]),
                text=str(data["text"]),
                input_tokens=int(data["input_tokens"]),
                output_tokens=int(data["output_tokens"]),
                content=content,
                stop_reason=None if stop_reason is None else str(stop_reason),
            )
        except (ValueError, KeyError, TypeError, OSError) as err:
            raise CacheError(f"unreadable cache entry {key} ({path.name}): {err}") from None

    def put(self, key: str, entry: CacheEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {
            "version": CACHE_VERSION,
            "key": key,
            "model": entry.model,
            "text": entry.text,
            "input_tokens": entry.input_tokens,
            "output_tokens": entry.output_tokens,
        }
        if entry.content is not None:
            data["content"] = entry.content
        if entry.stop_reason is not None:
            data["stop_reason"] = entry.stop_reason
        body = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_bytes(body.encode("utf-8"))  # bytes: no CRLF translation on Windows
        os.replace(tmp, self._path(key))
