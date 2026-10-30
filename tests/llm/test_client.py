import logging
from typing import Any

import anthropic
import httpx2
import pytest
from helpers import FAKE_KEY, MODEL, FakeSDK, make_settings, response

from ta_in.llm import BudgetExceeded, LLMClient, RetryPolicy, UnknownModelError


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.test/v1"))


def status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.test/v1")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


def make_client(script: list[Any], budget: float = 1.0) -> tuple[LLMClient, FakeSDK, list[float]]:
    sleeps: list[float] = []
    sdk = FakeSDK(script)
    client = LLMClient(make_settings(budget), sdk=sdk, sleep=sleeps.append)  # type: ignore[arg-type]
    return client, sdk, sleeps


def call(client: LLMClient) -> str:
    return client.complete(MODEL, "sys", [{"role": "user", "content": "hi"}])


def test_success_returns_text_and_records_tokens_and_cost() -> None:
    client, sdk, sleeps = make_client([response("hello", 1000, 500)])
    assert call(client) == "hello"
    assert sleeps == []
    (rec,) = client.records
    assert (rec.model, rec.input_tokens, rec.output_tokens, rec.attempts) == (MODEL, 1000, 500, 1)
    # 1000 * $2/M + 500 * $10/M
    assert rec.cost_usd == pytest.approx(0.002 + 0.005)
    assert client.spent_usd == pytest.approx(0.007)


def test_transient_error_then_success_is_retried_with_backoff() -> None:
    client, sdk, sleeps = make_client(
        [connection_error(), status_error(anthropic.RateLimitError, 429), response()]
    )
    assert call(client) == "ok"
    assert len(sdk.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert client.records[0].attempts == 3


def test_5xx_is_retried() -> None:
    client, sdk, sleeps = make_client(
        [status_error(anthropic.InternalServerError, 500), response()]
    )
    call(client)
    assert sleeps == [1.0]


def test_non_retryable_error_raises_immediately() -> None:
    client, sdk, sleeps = make_client([status_error(anthropic.AuthenticationError, 401)])
    with pytest.raises(anthropic.AuthenticationError):
        call(client)
    assert len(sdk.calls) == 1
    assert sleeps == []
    assert client.records == []


def test_retries_exhausted_raises_last_error() -> None:
    client, sdk, sleeps = make_client([connection_error() for _ in range(4)])
    with pytest.raises(anthropic.APIConnectionError):
        call(client)
    assert len(sdk.calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert client.records == []


def test_custom_retry_policy() -> None:
    sleeps: list[float] = []
    sdk = FakeSDK([connection_error(), connection_error(), response()])
    client = LLMClient(
        make_settings(),
        sdk=sdk,  # type: ignore[arg-type]
        retry=RetryPolicy(max_attempts=3, base_delay=0.5, factor=3.0),
        sleep=sleeps.append,
    )
    call(client)
    assert sleeps == [0.5, 1.5]


def test_budget_breach_raises_records_call_and_refuses_next() -> None:
    # 100k in + 100k out on sonnet = $0.20 + $1.00 = $1.20 > $1.00 budget
    client, sdk, _ = make_client([response("big", 100_000, 100_000), response()])
    with pytest.raises(BudgetExceeded):
        call(client)
    assert len(client.records) == 1  # the breaching call is still recorded
    assert client.spent_usd == pytest.approx(1.2)
    with pytest.raises(BudgetExceeded):
        call(client)
    assert len(sdk.calls) == 1  # second call never reached the API


def test_spend_accumulates_across_calls() -> None:
    client, _, _ = make_client([response(), response()])
    call(client)
    call(client)
    assert len(client.records) == 2
    assert client.spent_usd == pytest.approx(0.014)


def test_unknown_model_fails_clearly_before_calling_api() -> None:
    client, sdk, _ = make_client([response()])
    with pytest.raises(UnknownModelError, match="no-such-model"):
        client.complete("no-such-model", "sys", [{"role": "user", "content": "hi"}])
    assert sdk.calls == []


def test_request_has_no_sampling_params_and_uses_given_fields() -> None:
    client, sdk, _ = make_client([response()])
    client.complete(MODEL, "be brief", [{"role": "user", "content": "hi"}], max_tokens=123)
    sent = sdk.calls[0]
    assert sent["model"] == MODEL
    assert sent["system"] == "be brief"
    assert sent["max_tokens"] == 123
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in sent


def test_logs_and_records_never_contain_key_or_prompt(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = make_client([response()])
    with caplog.at_level(logging.DEBUG):
        client.complete(MODEL, "sys", [{"role": "user", "content": "SECRET-PROMPT-TEXT"}])
    assert caplog.records, "expected one log line per call"
    text = caplog.text + repr(client.records)
    assert FAKE_KEY not in text
    assert "SECRET-PROMPT-TEXT" not in text
