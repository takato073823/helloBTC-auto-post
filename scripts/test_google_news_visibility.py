"""Google News透明性修正の軽量テスト。"""

import unittest

from repair_google_news_visibility import (
    AUTHOR_DESCRIPTION,
    EDITORIAL_POLICY_CONTENT,
    EDITORIAL_POLICY_SLUG,
    apply_visibility_repairs,
)


class RecordingWordPress:
    base_url = "https://hellobtc.jp"

    def __init__(self):
        self.page_call = None
        self.profile_call = None

    def upsert_page(self, **fields):
        self.page_call = fields
        return {"link": "https://hellobtc.jp/about-hellobtc-editorial-policy/"}

    def update_current_user_profile(self, **fields):
        self.profile_call = fields
        return {"id": 1}


class GoogleNewsVisibilityTests(unittest.TestCase):
    def test_publishes_editorial_policy_and_links_author_profile(self):
        wp = RecordingWordPress()
        result = apply_visibility_repairs(wp)

        self.assertEqual(EDITORIAL_POLICY_SLUG, wp.page_call["slug"])
        self.assertIn("AIを使用する場合があります", EDITORIAL_POLICY_CONTENT)
        self.assertIn("訂正・更新方針", EDITORIAL_POLICY_CONTENT)
        self.assertIn("広告・アフィリエイト", EDITORIAL_POLICY_CONTENT)
        self.assertEqual(result["page_url"], wp.profile_call["url"])
        self.assertEqual(AUTHOR_DESCRIPTION, wp.profile_call["description"])


if __name__ == "__main__":
    unittest.main()
