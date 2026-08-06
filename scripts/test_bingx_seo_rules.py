"""BingX自動投稿の重複防止・表現ガードの軽量テスト。

外部SDKを読み込まず、対象関数だけをASTから取り出して検証する。
"""
import ast
import json
import re
import tempfile
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
        "json": json,
        "Path": Path,
        "logger": _Logger(),
        "INVITE_URL": "https://bingxdao.com/invite/FIKYOA/",
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

    def test_first_successful_image_becomes_featured_when_img1_failed(self):
        functions = _load_functions("select_featured_media")
        image_map = {"img2": (202, "https://hellobtc.jp/img2.jpg")}
        self.assertEqual(
            (202, "https://hellobtc.jp/img2.jpg"),
            functions["select_featured_media"](image_map, []),
        )

    def test_screenshot_still_has_featured_image_priority(self):
        functions = _load_functions("select_featured_media")
        image_map = {
            "img1": (101, "https://hellobtc.jp/img1.jpg"),
            "futures": (303, "https://hellobtc.jp/futures.jpg"),
        }
        self.assertEqual(
            (303, "https://hellobtc.jp/futures.jpg"),
            functions["select_featured_media"](
                image_map, [{"key": "futures"}]
            ),
        )

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

    def test_related_reply_uses_the_requested_text_and_is_unique_to_each_article(self):
        functions = _load_functions("build_related_reply_text")
        first = functions["build_related_reply_text"]("BingX 口座開設の手順")
        second = functions["build_related_reply_text"]("BingX 二段階認証の設定")
        self.assertTrue(first.startswith("関連情報｜BingXの登録はこちら"))
        self.assertNotIn("【広告・PR】", first)
        self.assertIn("https://bingxdao.com/invite/FIKYOA/", first)
        self.assertIn("利用条件・対象地域・本人確認（KYC）", first)
        self.assertNotEqual(first, second)

    def test_only_topics_with_affiliate_cta_opt_in_to_the_related_reply(self):
        self.assertIn(
            'related_reply_text = (\n        build_related_reply_text(article["title"])\n'
            '        if topic.get("include_affiliate_cta", True)',
            SOURCE,
        )
        self.assertIn("related_reply_text=related_reply_text", SOURCE)
        self.assertIn("retry_pending_x_replies()", SOURCE)

    def test_pending_reply_is_recovered_without_creating_another_main_post(self):
        functions = _load_functions(
            "load_pending_x_replies",
            "save_pending_x_replies",
            "retry_pending_x_replies",
        )
        with tempfile.TemporaryDirectory() as directory:
            pending_file = Path(directory) / "pending.json"
            pending_file.write_text(
                json.dumps([{"tweet_id": "tweet-123", "text": "関連投稿"}]),
                encoding="utf-8",
            )
            sent = []
            functions["PENDING_X_REPLIES_FILE"] = pending_file
            functions["post_related_reply"] = lambda tweet_id, text: sent.append((tweet_id, text)) or True

            functions["retry_pending_x_replies"]()

            self.assertEqual([("tweet-123", "関連投稿")], sent)
            self.assertEqual([], json.loads(pending_file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
