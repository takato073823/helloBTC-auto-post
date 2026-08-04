"""INUの口調・画像・予算ルールをオフラインで検証する。"""

from __future__ import annotations

import unittest

from inu_budget import estimate_monthly_cost_yen
from inu_persona import lint_voice
from inu_post import compose_post, validate_post
from inu_visual import build_gpt_image_prompt, select_visual_route


class INUContentSystemTests(unittest.TestCase):
    def test_voice_is_consistent(self):
        good = "この数字は資金の偏りを示しています。僕は、過熱感には注意が必要だと見ています。"
        self.assertEqual([], lint_voice(good))
        self.assertTrue(lint_voice("俺は爆上げ確定だと思うワン"))

    def test_no_basic_knowledge_route(self):
        self.assertEqual("reject", select_visual_route("basic_knowledge").route)

    def test_evidence_routes_are_not_synthetic(self):
        self.assertEqual("live_chart", select_visual_route("crypto_market").route)
        self.assertEqual("source_data", select_visual_route("onchain").route)
        self.assertEqual("native_quote", select_visual_route("x_reaction").route)
        self.assertFalse(select_visual_route("etf_flow").gpt_image_allowed)

    def test_gpt_prompt_requires_original_visual(self):
        prompt = build_gpt_image_prompt(
            visual_type="gpt_timeline",
            headline="取引所障害の時系列",
            key_points=["公式が障害を発表", "出金を一時停止", "復旧を確認"],
        )
        self.assertIn("original composition", prompt)
        self.assertIn("Do not copy", prompt)
        self.assertIn("no invented prices", prompt)
        self.assertNotIn("Create it in SOU style", prompt)

    def test_post_uses_natural_paragraphs_and_inu_opinion(self):
        text = compose_post(
            hook="⚡️ 米国市場で半導体株に資金が集中",
            facts=["複数のETFで過去最大級の流入が確認されました。"],
            opinion="僕は、上昇余地よりも過熱感を見る局面だと見ています。",
            source_label="発行体公表データ",
            tags=["米国株"],
        )
        validate_post(text)
        self.assertNotIn("・", text)

    def test_hourly_medium_images_stay_under_budget_estimate(self):
        self.assertLessEqual(estimate_monthly_cost_yen(24), 10000)


if __name__ == "__main__":
    unittest.main()

