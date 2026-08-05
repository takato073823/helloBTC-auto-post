"""INU自動投稿の画像生成・Grok調査費を月1万円以内に制御する。"""

from __future__ import annotations

import os


MONTHLY_BUDGET_YEN = int(os.environ.get("INU_MONTHLY_BUDGET_YEN", "10000"))
RESERVED_TEXT_AND_MISC_YEN = int(os.environ.get("INU_RESERVED_TEXT_YEN", "2500"))
# 2026-08-04時点のGPT Image 2 mediumの公式価格に対する安全側の1枚単価。
ESTIMATED_IMAGE_USD = float(os.environ.get("INU_EST_IMAGE_USD", "0.053"))
USD_JPY = float(os.environ.get("INU_BUDGET_USD_JPY", "160"))
# Grok 4.3とX Searchの安全側概算。X検索は内部で最大2回使う前提にする。
GROK_INPUT_USD_PER_MILLION = float(os.environ.get("INU_GROK_INPUT_USD_PER_M", "1.25"))
GROK_OUTPUT_USD_PER_MILLION = float(os.environ.get("INU_GROK_OUTPUT_USD_PER_M", "2.50"))
GROK_X_SEARCH_USD = float(os.environ.get("INU_GROK_X_SEARCH_USD", "0.005"))
GROK_EST_INPUT_TOKENS = int(os.environ.get("INU_GROK_EST_INPUT_TOKENS", "2500"))
GROK_EST_OUTPUT_TOKENS = int(os.environ.get("INU_GROK_EST_OUTPUT_TOKENS", "1500"))


def estimate_monthly_cost_yen(images_per_day: int, *, days: int = 30) -> int:
    image_cost = images_per_day * days * ESTIMATED_IMAGE_USD * USD_JPY
    return round(image_cost + RESERVED_TEXT_AND_MISC_YEN)


def assert_within_budget(images_per_day: int) -> None:
    estimate = estimate_monthly_cost_yen(images_per_day)
    if estimate > MONTHLY_BUDGET_YEN:
        raise ValueError(f"月間予算を超えます: 約{estimate:,}円")


def estimate_grok_monthly_cost_yen(
    requests_per_day: int,
    *,
    days: int = 30,
    x_search_calls_per_request: int = 2,
) -> int:
    per_request_usd = (
        GROK_EST_INPUT_TOKENS / 1_000_000 * GROK_INPUT_USD_PER_MILLION
        + GROK_EST_OUTPUT_TOKENS / 1_000_000 * GROK_OUTPUT_USD_PER_MILLION
        + x_search_calls_per_request * GROK_X_SEARCH_USD
    )
    return round(requests_per_day * days * per_request_usd * USD_JPY)


def estimate_total_automation_cost_yen(
    *,
    paid_images_per_day: int = 18,
    grok_requests_per_day: int = 24,
    days: int = 30,
) -> int:
    """公式スクショを除き、有料画像は最大18件/日とした安全側の全体概算。"""
    return estimate_monthly_cost_yen(paid_images_per_day, days=days) + estimate_grok_monthly_cost_yen(
        grok_requests_per_day,
        days=days,
    )
