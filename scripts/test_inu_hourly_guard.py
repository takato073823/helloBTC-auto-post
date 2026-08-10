from __future__ import annotations

import datetime as dt
import os
import re
import unittest
from unittest.mock import patch
from pathlib import Path

import inu_hourly_guard as guard


class INUHourlyGuardTests(unittest.TestCase):
    def test_current_slot_uses_jst_hour(self):
        now = dt.datetime(2026, 8, 7, 3, 4, tzinfo=dt.timezone.utc)
        self.assertEqual("2026-08-07-12-a", guard.current_slot(now))

    def test_two_hour_schedule_targets_odd_jst_hours(self):
        with patch.dict(
            os.environ,
            {"INU_POST_INTERVAL_HOURS": "2", "INU_POST_START_HOUR_JST": "1"},
            clear=False,
        ):
            self.assertTrue(guard.is_scheduled_post_hour(dt.datetime(2026, 8, 7, 8, 4, tzinfo=dt.timezone.utc)))
            self.assertFalse(guard.is_scheduled_post_hour(dt.datetime(2026, 8, 7, 9, 4, tzinfo=dt.timezone.utc)))

    def test_invalid_schedule_interval_falls_back_to_hourly(self):
        with patch.dict(os.environ, {"INU_POST_INTERVAL_HOURS": "5"}, clear=False):
            self.assertEqual(1, guard.post_interval_hours())

    def test_primary_schedule_and_run_kind_use_the_same_cron(self):
        workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/inu_x_hourly.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        primary_cron = re.search(r'^    - cron: "([^"]+)"$', workflow, flags=re.MULTILINE)
        self.assertIsNotNone(primary_cron)
        self.assertIn(
            f"github.event.schedule == '{primary_cron.group(1)}'",
            workflow,
        )

    def test_posted_slot_prevents_recovery(self):
        state = {"history": [{"slot": "2026-08-07-12-a"}]}
        self.assertTrue(guard.has_hourly_activity(state, "2026-08-07-12-a"))

    def test_reservation_prevents_recovery(self):
        state = {"reservations": [{"slot": "2026-08-07-12-a"}]}
        self.assertTrue(guard.has_hourly_activity(state, "2026-08-07-12-a"))

    def test_breaking_post_in_same_hour_prevents_recovery(self):
        state = {
            "history": [
                {"slot": "breaking-event", "posted_at": "2026-08-07T03:25:00+00:00"}
            ]
        }
        self.assertTrue(guard.has_hourly_activity(state, "2026-08-07-12-a"))

    def test_empty_state_needs_recovery(self):
        self.assertFalse(guard.has_hourly_activity({}, "2026-08-07-12-a"))
