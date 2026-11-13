"""Anthropic Messages API wrapper: retry with backoff, token/cost records, budget stop, cache."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict

from ta_in.config import Settings
from ta_in.llm.cache import CacheEntry, CacheMiss, CacheMode, ResponseCache, cache_key
from ta_in.llm.pricing import cost_usd
from ta_in.llm.trace import TraceWriter
from ta_in.llm.turn import LLMTurn

log = logging.getLogger(__name__)

RETRYABLE = (
    anthropic.APIConnectionError,  # includes APITimeoutError
    anthropic.RateLimitError,
    anthropic.InternalServerError,  # 5xx
)


class BudgetExceeded(Exception):
    """Spend has reached or passed the configured budget."""


class CallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    attempts: int
    cached: bool = False


class _Outcome:
    """What one call produced, kept per call so parallel calls cannot mix up their trace lines."""

    def __init__(self) -> None:
        self.turn: LLMTurn | None = None
        self.record: CallRecord | None = None


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int = 4
    base_delay: float = 1.0
    factor: float = 2.0

    def delay(self, failed_attempts: int) -> float:
        return self.base_delay * self.factor ** (failed_attempts - 1)


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        sdk: Any = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache: ResponseCache | None = None,
        cache_mode: CacheMode | None = None,
        trace: TraceWriter | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._settings = settings
        self._budget_usd = settings.budget_usd
        self._sdk = sdk  # built lazily, so replay never constructs a network client
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._cache = cache or ResponseCache(settings.cache_dir)
        self._cache_mode = cache_mode if cache_mode is not None else CacheMode(settings.cache_mode)
        self._trace = trace
        self._clock = clock
        self._lock = threading.Lock()  # analysts share one client from parallel threads
        self.records: list[CallRecord] = []

    @property
    def spent_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
    ) -> str:
        """One call, returning only the text. Use `complete_turn` when tools are involved."""
        return self.complete_turn(model, system, messages, max_tokens, tools, agent).text

    def complete_turn(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMTurn:
        """One call, returning the assistant's text and tool_use blocks and why it stopped.

        Safe to call from several threads at once: everything a call needs for its own trace line
        lives in `outcome`, a per-call object, never on the client. The budget check happens
        before a call and is re-checked after it, so parallel calls may overshoot the budget by
        the calls that were already in flight.
        """
        outcome = _Outcome()
        error: str | None = None
        started = self._clock()
        try:
            return self._complete(model, system, messages, max_tokens, tools, tool_choice, outcome)
        except Exception as err:
            error = type(err).__name__
            raise
        finally:
            if self._trace is not None:
                rec = outcome.record
                turn = outcome.turn
                self._trace.log_call(
                    agent=agent,
                    model=model,
                    system=system,
                    messages=messages,
                    response=turn.text if turn else None,
                    tool_calls=[u.model_dump() for u in turn.tool_uses] if turn else [],
                    input_tokens=rec.input_tokens if rec else 0,
                    output_tokens=rec.output_tokens if rec else 0,
                    cost_usd=rec.cost_usd if rec else 0.0,
                    latency_s=self._clock() - started,
                    cached=rec.cached if rec else False,
                    attempts=rec.attempts if rec else 0,
                    error=error,
                )

    def _complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        outcome: _Outcome,
    ) -> LLMTurn:
        key = cache_key(model, system, messages, tools, max_tokens, tool_choice)
        if self._cache_mode is CacheMode.REPLAY:
            return self._replay(key, model, outcome)
        if self.spent_usd >= self._budget_usd:
            raise BudgetExceeded(
                f"budget ${self._budget_usd:.2f} already used (spent ${self.spent_usd:.4f})"
            )
        cost_usd(model, 0, 0)  # unknown model fails before any API call

        # No temperature/top_p/top_k: the API rejects sampling parameters on current models.
        request: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice
        response, attempts = self._call_with_retry(**request)
        turn = LLMTurn.from_sdk(response)
        outcome.turn = turn
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        record = CallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens),
            attempts=attempts,
        )
        outcome.record = record
        with self._lock:
            self.records.append(record)
        if self._cache_mode is CacheMode.RECORD:
            self._cache.put(
                key,
                CacheEntry(
                    model,
                    turn.text,
                    input_tokens,
                    output_tokens,
                    content=turn.content,
                    stop_reason=turn.stop_reason,
                ),
            )
        log.info(
            "llm call model=%s in=%d out=%d cost_usd=%.6f attempts=%d total_usd=%.6f",
            model,
            input_tokens,
            output_tokens,
            record.cost_usd,
            attempts,
            self.spent_usd,
        )
        if self.spent_usd > self._budget_usd:
            raise BudgetExceeded(
                f"budget ${self._budget_usd:.2f} exceeded (spent ${self.spent_usd:.4f})"
            )
        return turn

    def _replay(self, key: str, model: str, outcome: _Outcome) -> LLMTurn:
        entry = self._cache.get(key)
        if entry is None:
            raise CacheMiss(f"no recorded response for model={model} key={key}")
        record = CallRecord(
            model=entry.model,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cost_usd=0.0,
            attempts=0,
            cached=True,
        )
        outcome.record = record
        with self._lock:
            self.records.append(record)
        turn = (
            LLMTurn(content=entry.content, stop_reason=entry.stop_reason or "end_turn")
            if entry.content is not None
            else LLMTurn.from_text(entry.text)
        )
        outcome.turn = turn
        log.info("llm replay model=%s key=%s", model, key[:12])
        return turn

    def _get_sdk(self) -> Any:
        if self._sdk is None:
            # max_retries=0: all retrying happens here so attempts are counted and logged once.
            self._sdk = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(), max_retries=0
            )
        return self._sdk

    def _call_with_retry(self, **request: Any) -> tuple[Any, int]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._get_sdk().messages.create(**request), attempt
            except RETRYABLE as err:
                if attempt >= self._retry.max_attempts:
                    raise
                delay = self._retry.delay(attempt)
                log.warning(
                    "llm transient error %s; retry %d/%d in %.1fs",
                    type(err).__name__,
                    attempt,
                    self._retry.max_attempts - 1,
                    delay,
                )
                self._sleep(delay)
