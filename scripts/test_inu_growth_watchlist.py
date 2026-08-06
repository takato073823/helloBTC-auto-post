"""INU成長用ウォッチリストの安全性をネットワークなしで検証する。"""

from __future__ import annotations

import ast
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import inu_growth_watchlist as watchlist
import inu_growth_boost as boost
from x_list_client import XListClient


NOW = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


def profile(handle: str = "marketdata", user_id: str = "11", followers: int = 20_000, **extra):
    value = {
        "id": user_id,
        "username": handle,
        "description": "ビットコインETFとマクロ市場データを分析",
        "protected": False,
        "created_at": "2024-01-01T00:00:00Z",
        "public_metrics": {"followers_count": followers},
    }
    value.update(extra)
    return SimpleNamespace(**value)


def tweet(text: str = "ビットコインETFの資金フローとマクロ流動性を更新", **extra):
    value = {
        "id": "100",
        "text": text,
        "created_at": "2026-08-05T11:20:00Z",
        "lang": "ja",
        "public_metrics": {"like_count": 150, "reply_count": 10, "retweet_count": 20, "quote_count": 3, "impression_count": 25_000},
    }
    value.update(extra)
    return SimpleNamespace(**value)


def candidate(handle: str = "marketdata") -> dict:
    return {
        "handle": handle,
        "language": "ja",
        "role": "analyst",
        "focus": "ビットコインETFとマクロ流動性",
        "recent_post_url": f"https://x.com/{handle}/status/100",
        "why_relevant": "ETFと流動性のデータを市場文脈とともに扱う",
    }


class FakeListApi:
    def __init__(self):
        self.members: set[str] = set()
        self.created = 0
        self.added: list[str] = []
        self.removed: list[str] = []

    def get_me(self, **_kwargs):
        return SimpleNamespace(data=SimpleNamespace(id="self"))

    def get_owned_lists(self, *_args, **_kwargs):
        return SimpleNamespace(data=[])

    def get_list(self, *_args, **_kwargs):
        return SimpleNamespace(data=SimpleNamespace(id="list-1"))

    def create_list(self, **_kwargs):
        self.created += 1
        return SimpleNamespace(data=SimpleNamespace(id="list-1"))

    def get_list_members(self, *_args, **_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(id=item) for item in self.members], meta={})

    def get_list_tweets(self, *_args, **_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(
            id="post-1", author_id="1", text="Bitcoin ETF flow update",
            created_at="2026-08-05T11:50:00Z",
            public_metrics={"impression_count": 5_000, "like_count": 20},
        )], meta={})

    def add_list_member(self, _list_id, user_id, **_kwargs):
        self.members.add(str(user_id))
        self.added.append(str(user_id))

    def remove_list_member(self, _list_id, user_id, **_kwargs):
        self.members.discard(str(user_id))
        self.removed.append(str(user_id))

    def get_users(self, *, usernames=None, ids=None, **_kwargs):
        if usernames is not None:
            return SimpleNamespace(data=[profile(name, str(index + 1)) for index, name in enumerate(usernames)])
        return SimpleNamespace(data=[profile(f"legacy{user_id}", str(user_id)) for user_id in ids or []])

    def get_users_tweets(self, *_args, **_kwargs):
        return SimpleNamespace(data=[tweet(), tweet(created_at="2026-08-05T10:20:00Z")])


class WatchlistTests(unittest.TestCase):
    def test_score_rejects_promotional_profile(self):
        bad = profile(description="Crypto airdrop giveaway 100x signals")
        record, reason = watchlist.score_account(bad, [tweet()], candidate(), NOW, "self", set())
        self.assertIsNone(record)
        self.assertEqual("blocked_bio", reason)

    def test_score_requires_real_activity_and_returns_high_quality_record(self):
        record, reason = watchlist.score_account(profile(), [tweet(), tweet(created_at="2026-08-05T10:20:00Z")], candidate(), NOW, "self", set())
        self.assertEqual("", reason)
        self.assertIsNotNone(record)
        self.assertGreaterEqual(record["score"], watchlist.ADMIT_SCORE)
        self.assertEqual("marketdata", record["handle"])

    def test_score_rejects_non_japanese_timeline(self):
        english = tweet(text="Bitcoin ETF flow update", lang="en")
        record, reason = watchlist.score_account(profile(), [english, english], candidate(), NOW, "self", set())
        self.assertIsNone(record)
        self.assertEqual("non_japanese_timeline", reason)

    @patch("inu_growth_watchlist.generate_x_json")
    def test_discovery_passes_each_track_to_x_search(self, generate_x_json):
        generate_x_json.return_value = ({"accounts": [candidate("freshsource")]}, None)
        found = watchlist.discover_accounts(set(), needed=20, now=NOW)
        self.assertEqual("freshsource", found[0]["handle"])
        prompt = generate_x_json.call_args.kwargs.get("schema")
        self.assertIs(watchlist.WATCHLIST_SCHEMA, prompt)
        self.assertIn("最大20件", generate_x_json.call_args.args[0])

    def test_existing_member_has_hysteresis_before_removal(self):
        state = watchlist.default_state()
        state["members"]["kept"] = {
            "handle": "kept", "user_id": "1", "tier": "member", "score": 45,
            "language": "ja",
            "last_seen_at": "2026-08-05T11:00:00Z", "added_at": "2026-07-01T00:00:00Z",
            "low_score_cycles": 0,
        }
        selected, _ = watchlist.select_members(state, NOW)
        self.assertEqual(["kept"], [row["handle"] for row in selected])
        selected, _ = watchlist.select_members(state, NOW)
        self.assertEqual([], selected)

    @patch("inu_growth_watchlist.discover_accounts", return_value=[])
    def test_dry_run_never_creates_or_mutates_a_list(self, _discovery):
        api = FakeListApi()
        state = watchlist.default_state()
        result = watchlist.refresh_watchlist(state, api, NOW, allow_create=True, dry_run=True)
        self.assertEqual(0, api.created)
        self.assertEqual([], api.added)
        self.assertEqual(0, result["added"])

    @patch("inu_growth_watchlist.discover_accounts", return_value=[candidate()])
    def test_sync_creates_one_named_list_and_is_idempotent(self, _discovery):
        api = FakeListApi()
        state = watchlist.default_state()
        first = watchlist.refresh_watchlist(state, api, NOW, allow_create=True, dry_run=False)
        self.assertEqual("list-1", first["list_id"])
        self.assertEqual(1, api.created)
        self.assertEqual({"1"}, api.members)
        added_before = len(api.added)
        second = watchlist.refresh_watchlist(state, api, NOW, allow_create=True, dry_run=False)
        self.assertEqual(0, second["added"])
        self.assertEqual(added_before, len(api.added))
        self.assertEqual(1, api.created)

    @patch("inu_growth_watchlist.discover_accounts", return_value=[])
    def test_existing_selected_probation_is_promoted_for_boost_discovery(self, _discovery):
        api = FakeListApi()
        api.members = {"1"}
        state = watchlist.default_state()
        state.update({"list_id": "list-1", "managed_initialized": True})
        state["members"]["marketdata"] = {
            "handle": "marketdata", "user_id": "1", "tier": "probation", "language": "ja",
            "score": 80, "followers": 20_000, "last_seen_at": "2026-08-05T11:30:00Z",
            "added_at": "", "low_score_cycles": 0,
        }
        watchlist.refresh_watchlist(state, api, NOW, allow_create=True, dry_run=False)
        self.assertEqual("member", state["members"]["marketdata"]["tier"])

    def test_existing_same_name_list_is_reused(self):
        class Existing(FakeListApi):
            def get_owned_lists(self, *_args, **_kwargs):
                return SimpleNamespace(data=[SimpleNamespace(id="existing", name="いいね")])
        api = Existing()
        self.assertEqual("existing", XListClient(api).resolve_list(None, "いいね", allow_create=True))
        self.assertEqual(0, api.created)

    @patch("inu_growth_watchlist.discover_accounts", return_value=[])
    def test_first_sync_evaluates_existing_named_list_before_any_removal(self, _discovery):
        class Existing(FakeListApi):
            def __init__(self):
                super().__init__()
                self.members = {"99"}

            def get_owned_lists(self, *_args, **_kwargs):
                return SimpleNamespace(data=[SimpleNamespace(id="existing", name="いいね")])
        api = Existing()
        state = watchlist.default_state()
        watchlist.refresh_watchlist(state, api, NOW, allow_create=True, dry_run=False)
        self.assertEqual([], api.removed)
        self.assertIn("legacy99", state["members"])
        self.assertTrue(state["managed_initialized"])

    @patch("inu_growth_boost.load_watchlist_state")
    def test_boost_b_reads_one_merged_list_timeline(self, load_watchlist_state):
        load_watchlist_state.return_value = {
            "list_id": "list-1",
            "members": {"marketdata": {"tier": "member", "language": "ja", "user_id": "1", "handle": "marketdata"}},
        }
        rows = boost.recent_watchlist_posts(FakeListApi())
        self.assertEqual("https://x.com/marketdata/status/post-1", rows[0]["post_url"])
        found = boost.discover_boost_b(NOW, {"actions": []}, target_posts=rows)
        self.assertEqual("marketdata", found["target_handle"])

    def test_state_serialization_is_deterministic(self):
        state = watchlist.default_state()
        state["members"] = {"z": {"handle": "z"}, "a": {"handle": "a"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            watchlist.save_state(state, path)
            first = path.read_bytes()
            watchlist.save_state(watchlist.load_state(path), path)
            self.assertEqual(first, path.read_bytes())

    def test_watchlist_has_no_engagement_or_post_api_calls(self):
        banned = {"create_tweet", "like", "follow", "unfollow", "create_direct_message", "destroy_list"}
        for module in (watchlist, __import__("x_list_client")):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            names = {
                node.func.attr for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertFalse(banned & names, f"禁止APIが含まれています: {banned & names}")


if __name__ == "__main__":
    unittest.main()
