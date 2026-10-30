"""USD price per million tokens by model (Anthropic first-party API rates)."""

from __future__ import annotations

from dataclasses import dataclass


class UnknownModelError(Exception):
    """Raised for a model with no known price, so cost can never be silently zero."""


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    # Haiku 5.5 charges more once a prompt passes 100K tokens.
    long_prompt_threshold: int | None = None
    long_input_per_mtok: float = 0.0
    long_output_per_mtok: float = 0.0


PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(0.10, 0.50, 100_000, 0.50, 2.50),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    "claude-opus-5-5": ModelPrice(4.00, 20.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        raise UnknownModelError(f"no price known for model {model!r}; add it to PRICES")
    in_rate, out_rate = price.input_per_mtok, price.output_per_mtok
    if price.long_prompt_threshold is not None and input_tokens > price.long_prompt_threshold:
        in_rate, out_rate = price.long_input_per_mtok, price.long_output_per_mtok
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
