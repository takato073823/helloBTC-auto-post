"""BingX自動投稿の重複防止・表現ガードの軽量テスト。

外部SDKを読み込まず、対象関数だけをASTから取り出して検証する。
"""
import ast
import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("bingx_seo.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
EXCHANGE_GUIDE_SOURCE = Path(__file__).with_name("exchange_guide.py").read_text(encoding="utf-8")


class _Logger:
    def info(self, *_args, **_kwargs):
        pass


def _load_functions(*names):
    tree = ast.parse(SOURCE)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {
        "re": re,
        "logger": _Logger(),
        "JAPAN_RESIDENT_NOTICE": '<p class="hellobtc-japan-resident-notice">notice</p>',
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class BingxSeoRulesTests(unittest.TestCase):
    def test_required_notice_is_inserted_once_after_lead(self):
        functions = _load_functions("insert_required_notice")
        result = functions["insert_required_notice"]("<h2>見出し</h2><p>リード</p><h3>本文</h3>")
        self.assertIn('class="hellobtc-japan-resident-notice"', result)
        self.assertLess(result.index("リード</p>"), result.index("hellobtc-japan-resident-notice"))
        self.assertEqual(result, functions["insert_required_notice"](result))

    def test_existing_slug_is_skipped_without_resetting_rotation(self):
        functions = _load_functions("pick_next_topic")
        functions["TOPICS"] = [
            {"id": "already-there", "slug": "existing"},
            {"id": "next", "slug": "new"},
        ]
        saved = []
        functions["load_posted_ids"] = lambda: []
        functions["save_posted_ids"] = lambda ids: saved.append(list(ids))

        self.assertEqual(
            {"id": "next", "slug": "new"},
            functions["pick_next_topic"]({"existing"}),
        )
        self.assertEqual([["already-there"]], saved)

        functions["load_posted_ids"] = lambda: ["already-there", "next"]
        self.assertIsNone(functions["pick_next_topic"]())

    def test_priority_topic_is_selected_before_older_unposted_topics(self):
        functions = _load_functions("pick_next_topic")
        functions["TOPICS"] = [
            {"id": "older", "slug": "older"},
            {"id": "priority", "slug": "priority", "priority": True},
        ]
        functions["load_posted_ids"] = lambda: []
        functions["save_posted_ids"] = lambda _ids: None
        self.assertEqual(
            "priority",
            functions["pick_next_topic"]()["id"],
        )

    def test_safe_social_copy_does_not_reuse_unverified_topic_claims(self):
        functions = _load_functions("safe_tweet_bullets")
        bullets = functions["safe_tweet_bullets"](
            {"id": "fees-guide", "tweet_bullets": ["最安", "必ずお得"]}
        )
        self.assertNotIn("最安", bullets)
        self.assertIn("公式情報で確認", bullets[0])

    def test_prompt_contains_migration_and_registration_guardrails(self):
        self.assertIn("Bitget利用者がBingXへ移行", SOURCE)
        self.assertIn("金融庁登録済み", SOURCE)
        self.assertIn("特定の取引所を推奨する結論にしない", SOURCE)
        self.assertIn("広告・PR", SOURCE)
        self.assertNotIn("BingXを推奨する結論にする", SOURCE)
        self.assertNotIn("ルールを理解して使えば問題ない", SOURCE)

    def test_bitget_topics_use_official_source_box_without_cta(self):
        tree = ast.parse(SOURCE)
        topics_node = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "TOPICS" for target in node.targets)
        )
        bitget_topics = []
        for item in topics_node.value.elts:
            values = {
                key.value: value
                for key, value in zip(item.keys, item.values)
                if isinstance(key, ast.Constant)
            }
            topic_id = values.get("id")
            if isinstance(topic_id, ast.Constant) and topic_id.value.startswith("bitget-"):
                bitget_topics.append(values)
        self.assertEqual(2, len(bitget_topics))
        self.assertTrue(
            all(isinstance(topic["include_affiliate_cta"], ast.Constant) and not topic["include_affiliate_cta"].value
                for topic in bitget_topics)
        )
        self.assertTrue(
            all(isinstance(topic["source_box"], ast.Name) and topic["source_box"].id == "BITGET_MIGRATION_SOURCE_BOX"
                for topic in bitget_topics)
        )

    def test_bingx_invite_code_and_url_are_consistent(self):
        expected_url = "https://bingxdao.com/invite/FIKYOA/"
        self.assertIn('INVITE_CODE = "FIKYOA"', SOURCE)
        self.assertIn(f'INVITE_URL  = "{expected_url}"', SOURCE)
        self.assertIn(expected_url, EXCHANGE_GUIDE_SOURCE)
        self.assertIn("招待コードFIKYOA", EXCHANGE_GUIDE_SOURCE)
        self.assertNotIn("XXCCJX", SOURCE + EXCHANGE_GUIDE_SOURCE)


if __name__ == "__main__":
    unittest.main()
