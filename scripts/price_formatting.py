"""helloBTCの記事・X投稿で使うドル建て価格表記を統一する。"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP


# ``6.46万ドル`` / ``6万4,600ドル`` / ``64,600ドル`` を同じ金額として扱う。
_MAN_AND_REMAINDER = re.compile(
    r"(?<![\d.])(?P<man>\d+)万(?P<remainder>\d[\d,]*)ドル"
)
_DECIMAL_MAN = re.compile(r"(?<![\d.])(?P<man>\d+(?:\.\d+)?)万ドル")
_DOLLAR_AMOUNT = re.compile(r"(?<![万\d,])(?P<amount>\d{1,3}(?:,\d{3})+|\d{4,})ドル")


def _to_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _render_dollars(amount: int, *, for_title: bool) -> str:
    if for_title and amount >= 10_000:
        man, remainder = divmod(amount, 10_000)
        if remainder:
            return f"{man}万{remainder:,}ドル"
        return f"{man}万ドル"
    return f"{amount:,}ドル"


def format_usd_prices(text: str, *, for_title: bool) -> str:
    """ドル建て価格を表示場所ごとの表記ルールへ変換する。

    タイトルは ``6万4,600ドル``、本文・要約・X要点は ``64,600ドル`` にする。
    すでに正しい表記の場合も、同じ文字列を返すため繰り返し適用できる。
    """
    if not text:
        return text

    def replace_man_and_remainder(match: re.Match[str]) -> str:
        remainder = int(match.group("remainder").replace(",", ""))
        amount = int(match.group("man")) * 10_000 + remainder
        return _render_dollars(amount, for_title=for_title)

    def replace_decimal_man(match: re.Match[str]) -> str:
        amount = _to_int(Decimal(match.group("man")) * Decimal("10000"))
        return _render_dollars(amount, for_title=for_title)

    def replace_dollar_amount(match: re.Match[str]) -> str:
        amount = int(match.group("amount").replace(",", ""))
        return _render_dollars(amount, for_title=for_title)

    formatted = _MAN_AND_REMAINDER.sub(replace_man_and_remainder, text)
    formatted = _DECIMAL_MAN.sub(replace_decimal_man, formatted)
    return _DOLLAR_AMOUNT.sub(replace_dollar_amount, formatted)
