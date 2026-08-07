"""INUの暗号資産ティッカー表記を検証する。"""

from __future__ import annotations

import unittest

from inu_post import compose_post
from inu_tickers import format_crypto_tickers


class INUTickerTests(unittest.TestCase):
    def test_crypto_symbols_use_dollar_prefix_without_touching_pairs_or_urls(self):
        text = format_crypto_tickers(
            "BTC価格は上昇。ETH/BTCも確認。#BTC @BTC $SOL BTC-USD "
            "https://example.com/BTC"
        )
        self.assertIn("$BTC価格", text)
        self.assertIn("$ETH/$BTC", text)
        self.assertIn("#BTC", text)
        self.assertIn("@BTC", text)
        self.assertIn("$SOL", text)
        self.assertIn("BTC-USD", text)
        self.assertIn("https://example.com/BTC", text)
        self.assertNotIn("$$", text)

    def test_compose_post_formats_crypto_symbols_in_auto_copy(self):
        text = compose_post(
            hook="BTC、ETF資金フローを確認",
            facts=["ETHとSOLの資金流入額が更新されました。"],
            tags=["仮想通貨"],
        )
        self.assertIn("$BTC、", text)
        self.assertIn("$ETHと$SOL", text)


if __name__ == "__main__":
    unittest.main()
