"""GPT Imageの月間予算を1万円以内に制御する。"""

from __future__ import annotations

import os


MONTHLY_BUDGET_YEN = int(os.environ.get("INU_MONTHLY_BUDGET_YEN", "10000"))
RESERVED_TEXT_AND_MISC_YEN = int(os.environ.get("INU_RESERVED_TEXT_YEN", "2500"))
# 2026-08-04時点のGPT Image 2 mediumの公式価格に対する安全側の1枚単価。
ESTIMATED_IMAGE_USD = float(os.environ.get("INU_EST_IMAGE_USD", "0.053"))
USD_JPY = float(os.environ.get("INU_BUDGET_USD_JPY", "160"))


def estimate_monthly_cost_yen(images_per_day: int, *, days: int = 30) -> int:
    image_cost = images_per_day * days * ESTIMATED_IMAGE_USD * USD_JPY
    return round(image_cost + RESERVED_TEXT_AND_MISC_YEN)


def assert_within_budget(images_per_day: int) -> None:
    estimate = estimate_monthly_cost_yen(images_per_day)
    if estimate > MONTHLY_BUDGET_YEN:
        raise ValueError(f"月間予算を超えます: 約{estimate:,}円")

