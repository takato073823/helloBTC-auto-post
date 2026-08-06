"""時価総額・話題銘柄・日米株の探索対象を検証する。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import inu_market_universe as universe


class INUMarketUniverseTests(unittest.TestCase):
    def test_top30_and_trending_assets_are_merged_after_coinbase_check(self):
        def fake_get_json(url, *, params=None):
            if url == universe.COINBASE_PRODUCTS_URL:
                return [
                    {"id": "BTC-USD", "quote_currency": "USD", "status": "online", "trading_disabled": False},
                    {"id": "ETH-USD", "quote_currency": "USD", "status": "online", "trading_disabled": False},
                    {"id": "HYPE-USD", "quote_currency": "USD", "status": "online", "trading_disabled": False},
                ]
            if url == universe.COINGECKO_MARKETS_URL:
                return [
                    {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1, "price_change_percentage_24h": 3.4},
                    {"id": "tether", "symbol": "usdt", "name": "Tether", "market_cap_rank": 3, "price_change_percentage_24h": 0.0},
                    {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "market_cap_rank": 2, "price_change_percentage_24h": 1.2},
                ]
            if url == universe.COINGECKO_TRENDING_URL:
                return {"coins": [{"item": {"id": "hyperliquid", "symbol": "HYPE", "name": "Hyperliquid", "market_cap_rank": 11, "data": {"price_change_percentage_24h": {"usd": 8.5}}}}]}
            raise AssertionError(url)

        with patch.object(universe, "_get_json", side_effect=fake_get_json):
            assets = universe.discover_crypto_assets()
        self.assertEqual(["BTC-USD", "ETH-USD", "HYPE-USD"], [asset.product for asset in assets])
        self.assertTrue(assets[-1].is_trending)
        self.assertNotIn("USDT-USD", {asset.product for asset in assets})

    def test_btc_leads_snapshot_order_only_for_market_wide_move(self):
        assets = [
            universe.CryptoAsset("BTC-USD", "BTC", "Bitcoin", 1, False, 3.1),
            universe.CryptoAsset("HYPE-USD", "HYPE", "Hyperliquid", 11, True, 12.0),
        ]
        prioritized = universe.prioritize_crypto_assets(assets)
        self.assertEqual("BTC-USD", prioritized[0].product)

    def test_stock_universe_contains_us_and_jp_cores_and_valid_trends(self):
        with patch.object(
            universe,
            "fetch_yahoo_trending_symbols",
            side_effect=[["SOUN", "BTC-USD"], ["7203.T"]],
        ):
            assets = universe.discover_stock_assets()
        by_symbol = {asset.yahoo_symbol: asset for asset in assets}
        self.assertEqual("us", by_symbol["NVDA"].market)
        self.assertEqual("jp", by_symbol["7203.T"].market)
        self.assertTrue(by_symbol["SOUN"].is_trending)
        self.assertNotIn("BTC-USD", by_symbol)

    def test_stock_snapshot_priority_keeps_both_us_and_jp_in_scope(self):
        assets = [
            universe.StockAsset(f"US{index}", f"US{index}", "us", f"NASDAQ:US{index}")
            for index in range(20)
        ] + [
            universe.StockAsset(f"{7200 + index}.T", f"JP{index}", "jp", f"TSE:{7200 + index}")
            for index in range(20)
        ]
        prioritized = universe.prioritize_stock_assets(assets)
        self.assertEqual(universe.MAX_STOCK_SNAPSHOT_CANDIDATES, len(prioritized))
        self.assertEqual({"us", "jp"}, {asset.market for asset in prioritized})

    def test_jp_gainers_are_used_when_yahoo_trending_api_is_empty(self):
        class Response:
            text = '<a href="/quote/7203.T">トヨタ</a><a href="/quote/6758.T">ソニー</a>'

            @staticmethod
            def raise_for_status():
                return None

        with patch.object(
            universe,
            "_get_json",
            return_value={"finance": {"result": []}},
        ), patch.object(universe.requests, "get", return_value=Response()):
            symbols = universe.fetch_yahoo_trending_symbols("JP")
        self.assertEqual(["7203.T", "6758.T"], symbols)


if __name__ == "__main__":
    unittest.main()
