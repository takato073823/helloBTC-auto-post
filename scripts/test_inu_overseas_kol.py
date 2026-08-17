"""海外KOLリストの厳格な選定・入替・視覚投稿抽出をオフラインで検証する。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import inu_overseas_kol as kol


NOW = dt.datetime(2026, 8, 6, 3, 0, tzinfo=dt.timezone.utc)


def profile(handle: str = "marketalpha", user_id: str = "1", followers: int = 10_001):
    return SimpleNamespace(
        id=user_id,
        username=handle,
        description="Bitcoin ETF, macro liquidity, and US equity market data",
        protected=False,
        public_metrics={"followers_count": followers},
    )


def post(index: int = 1, *, impressions: int = 10_001, created_at: str = "2026-08-06T02:20:00Z", media: bool = True):
    return {
        "post_id": str(2086000000000000000 + index),
        "post_url": f"https://x.com/marketalpha/status/{2086000000000000000 + index}",
        "posted_at": created_at,
        "text": "Bitcoin ETF flow and macro liquidity chart update",
        "lang": "en",
        "impression_count": impressions,
        "like_count": 20,
        "reply_count": 2,
        "repost_count": 3,
        "quote_count": 1,
        "media_types": ["video"] if media else [],
        "has_video": media,
        "has_image": False,
    }


def candidate(handle: str = "marketalpha") -> dict:
    return {
        "handle": handle,
        "language": "en",
        "focus": "Bitcoin ETF and macro liquidity",
        "recent_post_url": f"https://x.com/{handle}/status/2086000000000000001",
        "why_relevant": "Original market data and visual context",
    }


class FakeApi:
    def __init__(self, members=None, profiles=None, timelines=None):
        self.members = set(members or [])
        self.profiles = profiles or {}
        self.timelines = timelines or {}
        self.added = []
        self.removed = []
        self.created = 0

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
        return SimpleNamespace(data=[SimpleNamespace(id=user_id) for user_id in self.members], meta={})

    def add_list_member(self, _list_id, user_id, **_kwargs):
        self.members.add(str(user_id))
        self.added.append(str(user_id))

    def remove_list_member(self, _list_id, user_id, **_kwargs):
        self.members.discard(str(user_id))
        self.removed.append(str(user_id))

    def get_users(self, *, usernames=None, ids=None, **_kwargs):
        values = usernames if usernames is not None else ids or []
        response = []
        for value in values:
            key = str(value).lower()
            if usernames is None:
                response.extend([row for row in self.profiles.values() if str(row.id) == key])
            elif key in self.profiles:
                response.append(self.profiles[key])
        return SimpleNamespace(data=response)

    def get_users_tweets(self, user_id, **_kwargs):
        rows = self.timelines.get(str(user_id), [])
        media = []
        tweets = []
        for row in rows:
            key = f"media-{row['post_id']}"
            payload = {
                "id": row["post_id"], "created_at": row["posted_at"], "text": row["text"],
                "lang": row["lang"], "public_metrics": {
                    "impression_count": row["impression_count"], "like_count": row["like_count"],
                    "reply_count": row["reply_count"], "retweet_count": row["repost_count"], "quote_count": row["quote_count"],
                },
            }
            if row["media_types"]:
                payload["attachments"] = {"media_keys": [key]}
                media.append({"media_key": key, "type": row["media_types"][0]})
            tweets.append(payload)
        return SimpleNamespace(data=tweets, includes={"media": media})

    def get_list_tweets(self, *_args, **_kwargs):
        row = post(99, impressions=25_000)
        return SimpleNamespace(
            data=[{
                "id": row["post_id"], "author_id": "1", "created_at": row["posted_at"], "text": row["text"],
                "lang": row["lang"], "attachments": {"media_keys": ["media-1"]},
                "public_metrics": {"impression_count": 25_000, "like_count": 100, "reply_count": 3, "retweet_count": 5, "quote_count": 2},
            }],
            includes={"media": [{"media_key": "media-1", "type": "video"}]},
            meta={},
        )


class OverseasKolTests(unittest.TestCase):
    def test_hard_gates_require_over_10000_followers_and_impressions(self):
        record, reason = kol.score_account(profile(followers=10_000), [post()], candidate(), NOW, "self")
        self.assertIsNone(record)
        self.assertEqual("followers_below_10000", reason)
        record, reason = kol.score_account(profile(), [post(impressions=10_000)], candidate(), NOW, "self")
        self.assertIsNone(record)
        self.assertEqual("latest10_has_no_10000_impression_post", reason)

    def test_inactive_over_three_days_is_removed_from_eligibility(self):
        record, reason = kol.score_account(
            profile(), [post(created_at="2026-08-02T02:00:00Z")], candidate(), NOW, "self"
        )
        self.assertIsNone(record)
        self.assertEqual("inactive_over_3_days", reason)

    def test_branded_competing_media_is_never_a_kol_member(self):
        record, reason = kol.score_account(
            profile(handle="cointelegraph"), [post()], candidate("cointelegraph"), NOW, "self"
        )
        self.assertIsNone(record)
        self.assertEqual("branded_media_excluded", reason)

    def test_rate_limited_candidates_are_carried_to_the_next_refresh(self):
        state = kol.default_state()
        state["members"] = {
            "pending": {"handle": "pending", "tier": "pending_add", "focus": "ETF flows"},
            "legacy": {"handle": "legacy", "tier": "excluded", "focus": "Macro"},
            "removed": {
                "handle": "removed",
                "tier": "excluded",
                "exclusion_reason": "removed_from_x_list",
            },
        }
        self.assertEqual({"pending", "legacy"}, {row["handle"] for row in kol._deferred_candidates(state)})

    def test_full_list_replaces_bottom_ten_by_last10_average_impressions(self):
        profiles = {}
        timelines = {}
        members = set()
        for index in range(1, 101):
            handle = f"old{index}"
            user_id = str(index)
            profiles[handle] = profile(handle, user_id, followers=20_000)
            timelines[user_id] = [post(index, impressions=10_001 + index)]
            members.add(user_id)
        new_candidates = []
        for index in range(1, 11):
            handle = f"new{index}"
            user_id = f"n{index}"
            profiles[handle] = profile(handle, user_id, followers=30_000)
            timelines[user_id] = [post(200 + index, impressions=50_000)]
            new_candidates.append(candidate(handle))
        api = FakeApi(members, profiles, timelines)
        with patch.object(kol, "discover_accounts", return_value=new_candidates):
            state = kol.default_state()
            result = kol.refresh_overseas_kol(state, api, NOW, allow_create=True, dry_run=False)
        self.assertEqual(10, result["churned"])
        self.assertEqual({str(value) for value in range(1, 11)}, set(api.removed))
        self.assertEqual({f"n{value}" for value in range(1, 11)}, set(api.added))
        self.assertEqual(100, len(api.members))

    def test_live_visual_posts_preserves_native_media_identity(self):
        state = kol.default_state()
        state["list_id"] = "list-1"
        state["members"] = {"marketalpha": {"tier": "member", "user_id": "1", "handle": "marketalpha", "followers": 20_000, "last10_average_impressions": 18_000}}
        api = FakeApi()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            kol.save_state(state, path)
            rows = kol.live_visual_posts(NOW, api, path)
        self.assertEqual(1, len(rows))
        self.assertEqual("marketalpha", rows[0]["handle"])
        self.assertTrue(rows[0]["has_video"])
        self.assertEqual("2086000000000000099", rows[0]["post_id"])
        self.assertEqual(
            "https://x.com/marketalpha/status/2086000000000000099",
            rows[0]["post_url"],
        )


if __name__ == "__main__":
    unittest.main()
