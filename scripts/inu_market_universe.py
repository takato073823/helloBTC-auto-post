"""INUの価格投稿で使う、暗号資産・株式の探索対象を決める。"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Iterable

import requests


COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"
YAHOO_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/{region}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_JP_GAINERS_URL = "https://finance.yahoo.co.jp/stocks/ranking/up"
USER_AGENT = "Mozilla/5.0 (compatible; helloBTC-INU-market-universe/1.0)"
TOP_CRYPTO_MARKET_CAP_LIMIT = 30
MAX_TRENDING_CRYPTO = 10
MAX_TRENDING_STOCKS_PER_MARKET = 10
MAX_CRYPTO_SNAPSHOT_CANDIDATES = 12
MAX_STOCK_SNAPSHOT_CANDIDATES = 14

# 法定通貨ペッグの通常変動を、値動きニュースとして投稿しない。
STABLECOIN_IDS = {
    "tether",
    "usd-coin",
    "dai",
    "first-digital-usd",
    "ethena-usde",
    "paypal-usd",
    "usds",
    "usdd",
    "true-usd",
    "frax",
}
STABLECOIN_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "USDE", "PYUSD", "USDS", "USDD", "TUSD", "FRAX"}
PRODUCT_PATTERN = re.compile(r"^[A-Z0-9]{2,15}-USD$")
US_STOCK_PATTERN = re.compile(r"^[A-Z][A-Z.-]{0,9}$")
JP_STOCK_PATTERN = re.compile(r"^\d{4}\.T$")


@dataclass(frozen=True)
class CryptoAsset:
    product: str
    symbol: str
    name: str
    market_cap_rank: int
    is_trending: bool
    coingecko_change_24h: float


@dataclass(frozen=True)
class StockAsset:
    yahoo_symbol: str
    label: str
    market: str
    tradingview_symbol: str | None
    is_trending: bool = False


# CoinGeckoが一時的に応答しない場合も「主要銘柄だけ」に縮退して探索を止めない。
# 実際に利用するのはCoinbaseのUSD建て市場が存在する銘柄に限る。
CRYPTO_CORE_FALLBACK = (
    ("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("XRP", "XRP"),
    ("SOL", "Solana"), ("BNB", "BNB"), ("TRX", "TRON"),
    ("DOGE", "Dogecoin"), ("ADA", "Cardano"), ("HYPE", "Hyperliquid"),
    ("LINK", "Chainlink"), ("XLM", "Stellar"), ("SUI", "Sui"),
    ("AVAX", "Avalanche"), ("LTC", "Litecoin"), ("BCH", "Bitcoin Cash"),
    ("SHIB", "Shiba Inu"), ("UNI", "Uniswap"), ("AAVE", "Aave"),
    ("HBAR", "Hedera"), ("TAO", "Bittensor"), ("DOT", "Polkadot"),
    ("NEAR", "NEAR Protocol"), ("PEPE", "Pepe"), ("APT", "Aptos"),
    ("ICP", "Internet Computer"), ("ETC", "Ethereum Classic"),
    ("ONDO", "Ondo"), ("FIL", "Filecoin"), ("ARB", "Arbitrum"),
    ("OP", "Optimism"),
)

# 個別株は米国・日本の市場を代表する大型株と、暗号資産に波及しやすい関連株を混在させる。
# トレンド銘柄はYahoo Financeのトレンド一覧から別途追加する。
US_CORE_STOCKS = (
    ("^GSPC", "S&P 500", "FOREXCOM:SPXUSD"),
    ("^NDX", "NASDAQ 100", "FOREXCOM:NSXUSD"),
    ("^DJI", "NYダウ", "FOREXCOM:DJI"),
    ("NVDA", "NVIDIA", "NASDAQ:NVDA"), ("MSFT", "Microsoft", "NASDAQ:MSFT"),
    ("AAPL", "Apple", "NASDAQ:AAPL"), ("AMZN", "Amazon", "NASDAQ:AMZN"),
    ("GOOGL", "Alphabet", "NASDAQ:GOOGL"), ("META", "Meta", "NASDAQ:META"),
    ("TSLA", "Tesla", "NASDAQ:TSLA"), ("AVGO", "Broadcom", "NASDAQ:AVGO"),
    ("AMD", "AMD", "NASDAQ:AMD"), ("PLTR", "Palantir", "NASDAQ:PLTR"),
    ("COIN", "Coinbase", "NASDAQ:COIN"), ("MSTR", "Strategy", "NASDAQ:MSTR"),
    ("MARA", "MARA Holdings", "NASDAQ:MARA"), ("RIOT", "Riot Platforms", "NASDAQ:RIOT"),
)
JP_CORE_STOCKS = (
    ("^N225", "日経平均", "FOREXCOM:JPXJPY"),
    ("^TOPX", "TOPIX", "TVC:TOPIX"),
    ("7203.T", "トヨタ", "TSE:7203"), ("6758.T", "ソニー", "TSE:6758"),
    ("8306.T", "三菱UFJ", "TSE:8306"), ("9984.T", "ソフトバンクG", "TSE:9984"),
    ("6857.T", "アドバンテスト", "TSE:6857"), ("8035.T", "東京エレクトロン", "TSE:8035"),
    ("4063.T", "信越化学", "TSE:4063"), ("5803.T", "フジクラ", "TSE:5803"),
    ("7735.T", "SCREEN", "TSE:7735"), ("7011.T", "三菱重工", "TSE:7011"),
    ("9432.T", "NTT", "TSE:9432"), ("8058.T", "三菱商事", "TSE:8058"),
)


def _get_json(url: str, *, params: dict[str, object] | None = None) -> object:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _finite(value: object, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _is_stablecoin(row: dict[str, object]) -> bool:
    return (
        str(row.get("id", "")).lower() in STABLECOIN_IDS
        or str(row.get("symbol", "")).upper() in STABLECOIN_SYMBOLS
    )


def fetch_coinbase_usd_products() -> set[str]:
    payload = _get_json(COINBASE_PRODUCTS_URL)
    if not isinstance(payload, list):
        raise ValueError("Coinbaseの商品一覧が配列ではありません")
    return {
        str(row.get("id", "")).upper()
        for row in payload
        if isinstance(row, dict)
        and PRODUCT_PATTERN.fullmatch(str(row.get("id", "")).upper())
        and str(row.get("quote_currency", "")).upper() == "USD"
        and str(row.get("status", "online")).lower() == "online"
        and not bool(row.get("trading_disabled", False))
    }


def _coingecko_market_rows() -> list[dict[str, object]]:
    payload = _get_json(
        COINGECKO_MARKETS_URL,
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": TOP_CRYPTO_MARKET_CAP_LIMIT,
            "page": 1,
            "sparkline": "false",
        },
    )
    if not isinstance(payload, list):
        raise ValueError("CoinGeckoの時価総額一覧が配列ではありません")
    return [row for row in payload if isinstance(row, dict)]


def _coingecko_trending_rows() -> list[dict[str, object]]:
    payload = _get_json(COINGECKO_TRENDING_URL)
    if not isinstance(payload, dict):
        raise ValueError("CoinGeckoのトレンド一覧が不正です")
    rows: list[dict[str, object]] = []
    for row in payload.get("coins", [])[:MAX_TRENDING_CRYPTO]:
        item = row.get("item") if isinstance(row, dict) else None
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        rows.append(
            {
                "id": item.get("id"),
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "market_cap_rank": item.get("market_cap_rank") or 9_999,
                "price_change_percentage_24h": data.get("price_change_percentage_24h", {}).get("usd", 0.0),
                "is_trending": True,
            }
        )
    return rows


def discover_crypto_assets() -> list[CryptoAsset]:
    """時価総額上位30銘柄とCoinGeckoの話題銘柄をCoinbase取扱銘柄へ絞る。"""
    try:
        market_rows = _coingecko_market_rows()
    except (requests.RequestException, ValueError):
        market_rows = [
            {"symbol": symbol, "name": name, "market_cap_rank": index + 1}
            for index, (symbol, name) in enumerate(CRYPTO_CORE_FALLBACK)
        ]
    try:
        trend_rows = _coingecko_trending_rows()
    except (requests.RequestException, ValueError):
        trend_rows = []
    available = fetch_coinbase_usd_products()

    combined: dict[str, dict[str, object]] = {}
    for row in [*market_rows, *trend_rows]:
        if _is_stablecoin(row):
            continue
        symbol = str(row.get("symbol", "")).upper().strip()
        product = f"{symbol}-USD"
        if not symbol or product not in available:
            continue
        current = combined.setdefault(
            product,
            {
                "symbol": symbol,
                "name": str(row.get("name") or symbol),
                "market_cap_rank": int(_finite(row.get("market_cap_rank"), default=9_999)),
                "is_trending": False,
                "price_change_percentage_24h": 0.0,
            },
        )
        current["market_cap_rank"] = min(
            int(current["market_cap_rank"]), int(_finite(row.get("market_cap_rank"), default=9_999))
        )
        current["is_trending"] = bool(current["is_trending"]) or bool(row.get("is_trending"))
        change = _finite(row.get("price_change_percentage_24h"))
        if abs(change) > abs(_finite(current.get("price_change_percentage_24h"))):
            current["price_change_percentage_24h"] = change

    return [
        CryptoAsset(
            product=product,
            symbol=str(row["symbol"]),
            name=str(row["name"]),
            market_cap_rank=int(row["market_cap_rank"]),
            is_trending=bool(row["is_trending"]),
            coingecko_change_24h=_finite(row["price_change_percentage_24h"]),
        )
        for product, row in sorted(
            combined.items(),
            key=lambda pair: (
                0 if pair[0] == "BTC-USD" else 1,
                int(pair[1]["market_cap_rank"]),
                pair[0],
            ),
        )
    ]


def prioritize_crypto_assets(assets: Iterable[CryptoAsset]) -> list[CryptoAsset]:
    """上位30全体を比較したうえで、検証用のCoinbase足を取得する順に並べる。"""
    return sorted(
        assets,
        key=lambda asset: (
            # BTCが市場全体を先導する大幅変動なら、アルトの相対的な変動より先に検証する。
            0 if asset.product == "BTC-USD" and abs(asset.coingecko_change_24h) >= 3.0 else 1,
            0 if asset.is_trending else 1,
            -abs(asset.coingecko_change_24h),
            asset.market_cap_rank,
        ),
    )[:MAX_CRYPTO_SNAPSHOT_CANDIDATES]


def _core_stock_assets() -> list[StockAsset]:
    return [
        *[
            StockAsset(symbol, label, "us", tradingview_symbol)
            for symbol, label, tradingview_symbol in US_CORE_STOCKS
        ],
        *[
            StockAsset(symbol, label, "jp", tradingview_symbol)
            for symbol, label, tradingview_symbol in JP_CORE_STOCKS
        ],
    ]


def fetch_yahoo_trending_symbols(region: str) -> list[str]:
    if region not in {"US", "JP"}:
        raise ValueError("Yahoo Financeの地域が不正です")
    payload = _get_json(YAHOO_TRENDING_URL.format(region=region))
    if not isinstance(payload, dict):
        raise ValueError("Yahoo Financeのトレンド一覧が不正です")
    result = payload.get("finance", {}).get("result", [])
    quotes = result[0].get("quotes", []) if isinstance(result, list) and result and isinstance(result[0], dict) else []
    symbols = [
        str(row.get("symbol", "")).upper()
        for row in quotes
        if isinstance(row, dict) and str(row.get("symbol", "")).strip()
    ][:MAX_TRENDING_STOCKS_PER_MARKET]
    if symbols or region != "JP":
        return symbols

    # Yahoo FinanceのJPトレンドAPIは空配列を返す時間帯があるため、同社の値上がり
    # ランキングを補助探索先にする。日本株の話題候補を固定銘柄だけにしないための経路。
    response = requests.get(
        YAHOO_JP_GAINERS_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return list(dict.fromkeys(re.findall(r"/quote/(\d{4}\.T)", response.text)))[:MAX_TRENDING_STOCKS_PER_MARKET]


def _guess_tradingview_symbol(symbol: str, market: str) -> str | None:
    if symbol.startswith("^"):
        return None
    if market == "jp" and JP_STOCK_PATTERN.fullmatch(symbol):
        return f"TSE:{symbol.removesuffix('.T')}"
    if market == "us" and US_STOCK_PATTERN.fullmatch(symbol) and "." not in symbol:
        # YahooのUSトレンド一覧の多くはNASDAQ上場。実画面の照合に失敗した銘柄は投稿候補から外す。
        return f"NASDAQ:{symbol}"
    return None


def discover_stock_assets() -> list[StockAsset]:
    """日米の主要株とYahoo Financeの話題銘柄を、チャート取得可能な候補として返す。"""
    assets = {asset.yahoo_symbol: asset for asset in _core_stock_assets()}
    for region, market in (("US", "us"), ("JP", "jp")):
        try:
            symbols = fetch_yahoo_trending_symbols(region)
        except (requests.RequestException, ValueError):
            symbols = []
        for symbol in symbols:
            if symbol in assets or symbol.endswith("-USD"):
                continue
            if market == "us" and not US_STOCK_PATTERN.fullmatch(symbol):
                continue
            if market == "jp" and not JP_STOCK_PATTERN.fullmatch(symbol):
                continue
            tradingview_symbol = _guess_tradingview_symbol(symbol, market)
            if not tradingview_symbol:
                continue
            assets[symbol] = StockAsset(
                yahoo_symbol=symbol,
                label=symbol,
                market=market,
                tradingview_symbol=tradingview_symbol,
                is_trending=True,
            )
    return list(assets.values())


def prioritize_stock_assets(assets: Iterable[StockAsset]) -> list[StockAsset]:
    """米国・日本のどちらかだけに偏らないよう、話題銘柄を先に交互に照合する。"""
    grouped = {
        market: sorted(
            [asset for asset in assets if asset.market == market],
            key=lambda asset: (0 if asset.is_trending else 1, asset.yahoo_symbol),
        )
        for market in ("us", "jp")
    }
    ordered: list[StockAsset] = []
    while len(ordered) < MAX_STOCK_SNAPSHOT_CANDIDATES and any(grouped.values()):
        for market in ("us", "jp"):
            if grouped[market] and len(ordered) < MAX_STOCK_SNAPSHOT_CANDIDATES:
                ordered.append(grouped[market].pop(0))
    return ordered
