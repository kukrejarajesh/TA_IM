"""Anthropic Messages API wrapper: retry with backoff, token/cost records, budget stop, cache."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict

from ta_in.config import Settings
from ta_in.llm.cache import CacheEntry, CacheMiss, CacheMode, ResponseCache, cache_key
from ta_in.llm.pricing import cost_usd

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
    ) -> None:
        self._settings = settings
        self._budget_usd = settings.budget_usd
        self._sdk = sdk  # built lazily, so replay never constructs a network client
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._cache = cache or ResponseCache(settings.cache_dir)
        self._cache_mode = cache_mode if cache_mode is not None else CacheMode(settings.cache_mode)
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
    ) -> str:
        key = cache_key(model, system, messages, tools, max_tokens)
        if self._cache_mode is CacheMode.REPLAY:
            return self._replay(key, model)
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
        response, attempts = self._call_with_retry(**request)
        text = "".join(b.text for b in response.content if b.type == "text")
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        record = CallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens),
            attempts=attempts,
        )
        self.records.append(record)
        if self._cache_mode is CacheMode.RECORD:
            self._cache.put(key, CacheEntry(model, text, input_tokens, output_tokens))
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
        return text

    def _replay(self, key: str, model: str) -> str:
        entry = self._cache.get(key)
        if entry is None:
            raise CacheMiss(f"no recorded response for model={model} key={key}")
        self.records.append(
            CallRecord(
                model=entry.model,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                cost_usd=0.0,
                attempts=0,
                cached=True,
            )
        )
        log.info("llm replay model=%s key=%s", model, key[:12])
        return entry.text

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
