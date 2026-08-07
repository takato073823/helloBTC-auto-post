from __future__ import annotations

import datetime as dt
import unittest

import inu_growth_boost_guard as guard


class INUGrowthBoostGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 8, 7, 3, 40, tzinfo=dt.timezone.utc)

    def test_missing_state_needs_recovery(self):
        self.assertTrue(guard.needs_recovery({}, self.now))

    def test_fresh_check_does_not_recover(self):
        state = {"checked_at": "2026-08-07T03:30:00+00:00", "last_follower_count": 32}
        self.assertFalse(guard.needs_recovery(state, self.now))

    def test_stale_check_needs_recovery(self):
        state = {"checked_at": "2026-08-07T03:19:00+00:00", "last_follower_count": 32}
        self.assertTrue(guard.needs_recovery(state, self.now))

    def test_stopped_or_target_reached_never_recovers(self):
        self.assertFalse(guard.needs_recovery({"stopped": True}, self.now))
        self.assertFalse(guard.needs_recovery({"last_follower_count": 1000}, self.now))
