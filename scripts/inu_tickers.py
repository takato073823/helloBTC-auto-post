"""INU投稿で暗号資産ティッカーをX向けに表記統一する。"""

from __future__ import annotations

import re
from collections.abc import Iterable


# 時価総額上位・主要チェーン・ETF/オンチェーンで頻出する通貨を対象にする。
# 英単語と衝突しやすい ``AI`` や ``ON`` などは含めず、誤変換を避ける。
COMMON_CRYPTO_TICKERS = frozenset({
    "AAVE", "ADA", "ALGO", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "BONK",
    "BTC", "CRO", "CRV", "DAI", "DOGE", "DOT", "DYDX", "ENA", "EOS", "ETC",
    "ETH", "FET", "FIL", "FLOKI", "GRT", "HBAR", "HYPE", "ICP", "IMX", "INJ",
    "JTO", "JUP", "KAS", "LDO", "LINK", "LTC", "MKR", "MNT", "NEAR", "OKB",
    "ONDO", "OP", "PENDLE", "PEPE", "PYUSD", "QNT", "RENDER", "RUNE", "SEI", "SHIB",
    "SNX", "SOL", "STX", "SUI", "TAO", "TIA", "TON", "TRX", "USDC", "USDD",
    "USDE", "USDT", "VET", "WIF", "WLD", "XLM", "XMR", "XRP", "XTZ", "ZEC",
})

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def _normalized_symbols(additional_symbols: Iterable[str] | None) -> tuple[str, ...]:
    symbols = set(COMMON_CRYPTO_TICKERS)
    for value in additional_symbols or ():
        symbol = str(value or "").upper().strip().lstrip("$")
        if re.fullmatch(r"[A-Z0-9]{2,15}", symbol):
            symbols.add(symbol)
    return tuple(sorted(symbols, key=lambda symbol: (-len(symbol), symbol)))


def format_crypto_tickers(value: object, *, additional_symbols: Iterable[str] | None = None) -> str:
    """通貨単体の表記だけを ``$BTC`` 形式にする。

    取引ペア（``BTC-USD``）、ハッシュタグ、メンション、既に ``$`` が付いた表記、
    URLは変更しない。日本語に続く ``BTC価格`` のような自然な書き方は変換する。
    """
    text = str(value or "")
    symbols = _normalized_symbols(additional_symbols)
    if not text or not symbols:
        return text
    pattern = re.compile(
        r"(?<![$#@A-Za-z0-9_])(" + "|".join(map(re.escape, symbols)) + r")(?![A-Za-z0-9_-])"
    )

    def replace_plain(segment: str) -> str:
        return pattern.sub(lambda match: f"${match.group(1)}", segment)

    # URLを先に区切ることで、本文に万一URLが混入しても内容を壊さない。
    chunks: list[str] = []
    cursor = 0
    for match in _URL_RE.finditer(text):
        chunks.append(replace_plain(text[cursor:match.start()]))
        chunks.append(match.group(0))
        cursor = match.end()
    chunks.append(replace_plain(text[cursor:]))
    return "".join(chunks)
