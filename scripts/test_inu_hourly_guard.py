from __future__ import annotations

import datetime as dt
import unittest

import inu_hourly_guard as guard


class INUHourlyGuardTests(unittest.TestCase):
    def test_current_slot_uses_jst_hour(self):
        now = dt.datetime(2026, 8, 7, 3, 4, tzinfo=dt.timezone.utc)
        self.assertEqual("2026-08-07-12-a", guard.current_slot(now))

    def test_posted_slot_prevents_recovery(self):
        state = {"history": [{"slot": "2026-08-07-12-a"}]}
        self.assertTrue(guard.has_hourly_activity(state, "2026-08-07-12-a"))

    def test_reservation_prevents_recovery(self):
        state = {"reservations": [{"slot": "2026-08-07-12-a"}]}
        self.assertTrue(guard.has_hourly_activity(state, "2026-08-07-12-a"))

    def test_empty_state_needs_recovery(self):
        self.assertFalse(guard.has_hourly_activity({}, "2026-08-07-12-a"))
