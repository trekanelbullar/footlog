"""モデル別の単価表（設計書 §6.1）。

100万トークンあたりの入力・出力の USD。``call_llm`` がこの表から呼び出し前の
最大コストと、呼び出し後の実際のコストを見積もる。表に無いモデルは呼び出さずに
:class:`UnknownModelPricingError` を送出する（コストを数えられない呼び出しで
上限をすり抜けないため、設計書 §6.1）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """100万トークンあたりの USD 単価。"""

    input_per_million_usd: float
    output_per_million_usd: float


class UnknownModelPricingError(RuntimeError):
    """単価表に無いモデルを呼ぼうとしたことを表す。"""


# 仮の値。実際の単価に合わせて更新（モデル提供元の公開単価がまだ確定していないため）。
PRICING: dict[str, ModelPrice] = {
    "qwen/qwen3.7-flash": ModelPrice(input_per_million_usd=0.20, output_per_million_usd=0.80),
    "openai/gpt-4.1-mini": ModelPrice(input_per_million_usd=0.40, output_per_million_usd=1.60),
}


def get_price(model: str) -> ModelPrice:
    """モデル名から単価を引く。表に無ければ :class:`UnknownModelPricingError`。"""

    price = PRICING.get(model)
    if price is None:
        raise UnknownModelPricingError(
            f"model {model!r} has no entry in pricing.PRICING; refusing to call it"
        )
    return price
