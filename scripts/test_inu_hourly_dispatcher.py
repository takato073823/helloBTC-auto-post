"""毎時1投稿の選択・期限・重複防止をオフラインで検証する。"""

from __future__ import annotations

import datetime as dt
import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

import inu_hourly_dispatcher


NOW = dt.datetime(2026, 8, 4, 8, 0, tzinfo=dt.timezone.utc)


def candidate(item_id: str, topic_type: str, priority: int, *, expires: str = "2026-08-05T00:00:00Z") -> dict:
    return {
        "id": item_id,
        "topic_type": topic_type,
        "visual_route": "official_data_crop",
        "approval_status": "approved",
        "approved_at": "2026-08-04T07:00:00Z",
        "expires_at": expires,
        "priority": priority,
    }


class INUHourlyDispatcherTests(unittest.TestCase):
    def test_workflow_is_hourly_and_disabled_until_repo_variable_is_enabled(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "inu_x_hourly.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('- cron: "17 * * * *"', workflow)
        self.assertIn('- cron: "37 * * * *"', workflow)
        self.assertNotIn('- cron: "47 * * * *"', workflow)
        self.assertIn("INU_AUTOMATION_ENABLED", workflow)

    def test_highest_priority_valid_item_wins(self):
        posts = [
            candidate("flow", "etf_flow", 70),
            candidate("breaking", "market_microstructure", 90),
        ]
        state = {"posted_slots": [], "posted_ids": []}
        with patch.object(inu_hourly_dispatcher, "validate_test_item"):
            selected = inu_hourly_dispatcher.select_item(posts, state, NOW)
        self.assertEqual("breaking", selected["id"])

    def test_expired_and_already_posted_items_are_skipped(self):
        posts = [
            candidate("expired", "etf_flow", 100, expires="2026-08-04T07:59:00Z"),
            candidate("used", "etf_flow", 90),
            candidate("fresh", "etf_flow", 80),
        ]
        state = {"posted_slots": [], "posted_ids": ["used"]}
        with patch.object(inu_hourly_dispatcher, "validate_test_item"):
            selected = inu_hourly_dispatcher.select_item(posts, state, NOW)
        self.assertEqual("fresh", selected["id"])

    def test_manual_only_type_is_not_dispatched(self):
        posts = [candidate("quote", "translation_quote", 100)]
        posts[0]["visual_route"] = "manual_quote_with_source_media"
        state = {"posted_slots": [], "posted_ids": []}
        with patch.object(inu_hourly_dispatcher, "validate_test_item"):
            self.assertIsNone(inu_hourly_dispatcher.select_item(posts, state, NOW))

    def test_marked_slot_and_id_are_persisted(self):
        state = {"posted_slots": [], "posted_ids": []}
        item = candidate("flow", "etf_flow", 80)
        updated = inu_hourly_dispatcher.mark_posted(state, item, "2026-08-04-17", "123")
        self.assertEqual(["flow"], updated["posted_ids"])
        self.assertEqual("2026-08-04-17", updated["posted_slots"][0]["slot"])

    def test_live_rerun_is_rejected_before_publish(self):
        args = argparse.Namespace(
            live=True,
            slot="2026-08-04-17",
            catalog="unused-catalog.json",
            state="unused-state.json",
        )
        item = candidate("flow", "etf_flow", 80)
        empty_state = {"posted_slots": [], "posted_ids": []}
        with (
            patch.object(inu_hourly_dispatcher, "load_state", return_value=empty_state),
            patch.object(inu_hourly_dispatcher, "load_catalog", return_value=[item]),
            patch.object(inu_hourly_dispatcher, "select_item", return_value=item),
            patch.object(inu_hourly_dispatcher, "publish_test_item") as publish,
            patch.dict("os.environ", {"GITHUB_RUN_ATTEMPT": "2"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "再実行"):
                inu_hourly_dispatcher.run(args)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
