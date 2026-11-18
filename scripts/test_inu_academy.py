import asyncio
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import inu_academy as a
from inu_academy_report import capture, planned, render
from inu_auto_hourly import _reserve
import inu_auto_hourly as hourly


class PilotTests(unittest.TestCase):
    def test_window_balance(self):
        slots = list(planned())
        self.assertEqual(len({m["code"] for _, m in slots}), 28)
        for hour in ("09", "21"):
            self.assertEqual(sum(s.endswith(hour + "-a") and m["format"] == "図で検証" for s, m in slots), 7)
        for slot in ("2026-11-18-09-a", "2026-12-03-21-a", "2026-11-20-17-a", "manual"):
            self.assertIsNone(a.assignment(slot))

    def test_reporting_and_dedup(self):
        slot, meta = next(planned())
        now = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
        row = dict(slot=slot, academy=meta, tweet_id="123", posted_at=(now-dt.timedelta(days=1)).isoformat())
        snapshots = {}
        fetch = Mock(return_value={"123": {"reply_count": 2, "bookmark_count": 4}})
        capture([row], snapshots, now, fetch)
        capture([row], snapshots, now, fetch)
        fetch.assert_called_once_with(["123"])
        self.assertIn("|—|4|2|未計測|", render([{}, row], snapshots, {}))
        self.assertIn("|—|4|2|1|", render([row], snapshots, {"CA01": 1}))
        with self.assertRaises(ValueError):
            render([], {}, {"CA01": 2})

    @unittest.skipUnless(os.getenv("ACADEMY_RENDER_TEST"), "requires installed Chromium")
    def test_scheduled_pipeline_records_only_confirmed_publication(self):
        slot, meta = list(planned())[1]
        with tempfile.TemporaryDirectory(dir=a.ROOT) as path:
            path = Path(path)
            state, prepared = path / "state.json", path / "prepared.json"
            args = hourly.build_parser().parse_args(["--prepare", "--prepared", str(prepared), "--state", str(state)])
            with patch.dict(os.environ, {"INU_ACADEMY_SCHEDULE": "true", "INU_SCHEDULE_RUN_KIND": "primary"}), \
                 patch.object(hourly, "_scheduled_slot_key", return_value=slot), \
                 patch.object(hourly, "ARTIFACT_DIR", path), patch.object(hourly, "PREPARED_PATH", prepared), \
                 patch.object(hourly, "RESEARCH_REVIEW_PATH", path / "review.json"), \
                 patch.object(hourly, "publish_test_item", return_value="123") as send:
                self.assertEqual(hourly.prepare(args), 0)
                self.assertEqual(hourly.prepare(args), 0)
                self.assertFalse(json.loads(state.read_text()).get("history"))
                self.assertEqual(hourly.publish(args), 0)
                self.assertEqual(hourly.prepare(args), 0)
                self.assertEqual(json.loads(state.read_text())["history"][-1]["academy"], meta)
                send.assert_called_once()

    @unittest.skipUnless(os.getenv("ACADEMY_RENDER_TEST"), "requires installed Chromium")
    def test_all_lessons_pass_delivery_gates(self):
        async def simulate(directory):
            state = {}
            for slot, meta in planned():
                i, c = await a.lesson(meta, slot, directory)
                state = _reserve(state, i, c, slot, dt.datetime.now(dt.timezone.utc))
            self.assertEqual(len(state["reservations"]), 28)
        with tempfile.TemporaryDirectory(dir=a.ROOT) as path:
            asyncio.run(simulate(Path(path)))
