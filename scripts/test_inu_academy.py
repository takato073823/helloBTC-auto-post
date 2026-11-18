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
    def setUp(self):
        from PIL import Image
        import inu_academy_poster as poster
        def fake_image(prompt, path, **kwargs):
            Image.new("RGB", (768, 960), "#153c70").save(path)
        for name, effect in (("generate_image", fake_image), ("check_pixels", lambda *a: None)):
            p = patch.object(poster, name, side_effect=effect)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)

    def test_window_balance(self):
        slots = list(planned())
        self.assertEqual(len({m["code"] for _, m in slots}), 28)
        for hour in ("09", "21"):
            self.assertEqual(sum(s.endswith(hour + "-a") and m["format"] == "図で検証" for s, m in slots), 7)
        for slot in ("2026-11-18-09-a", "2026-12-03-21-a", "2026-11-20-17-a", "manual"):
            self.assertIsNone(a.assignment(slot))

    def test_no_success_case_stops_before_content_or_research_api(self):
        for slot in ("2026-11-19-09-a","2026-11-19-13-a"):
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as tmp, \
                 patch.dict(os.environ, {"INU_ACADEMY_SCHEDULE":"true"}), \
                 patch.object(hourly,"_scheduled_slot_key", return_value=slot), \
                 patch.object(hourly.inu_case_review,"research", side_effect=ValueError("no match")), \
                 patch.object(hourly, "collect_direct_source_candidates") as collect, \
                 patch.object(hourly,"research_candidates_with_grok") as paid:
                args = hourly.build_parser().parse_args(["--prepare","--state",tmp+"/state.json"])
                self.assertEqual(hourly.prepare(args), 0)
                collect.assert_not_called()
                paid.assert_not_called()
                self.generate_image.assert_not_called()

    def test_success_evidence_and_ranking_match_goal(self):
        import inu_case_review as cases
        now = dt.datetime.now(dt.timezone.utc)
        trend = cases.research("2026-11-19-17-a", now, {})
        picks = cases.rank([{"hook":"未確認"},{"hook":"24時間で3%変化"}], trend)
        self.assertEqual(picks[0]["hook"], "24時間で3%変化")
        self.assertIn("103,820", str(trend["references"]))
        slot, meta = next(planned())
        past = {"history":[{"tweet_id":"42","academy":{"format":meta["format"],"code":"CA01"}}]}
        with patch.object(cases, "read", return_value={"CA01":2}):
            edu = cases.research(slot, now, past, meta)
        self.assertEqual(edu["references"][0]["result"], "確認済み参加 2人（自己申告帰属）")
        self.assertNotIn("103,820", str(edu))

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
                original = prepared.read_text()
                wrong = json.loads(original)
                wrong["item"]["text"] += "変更"
                prepared.write_text(json.dumps(wrong))
                with self.assertRaisesRegex(RuntimeError, "成功事例"):
                    hourly.publish(args)
                send.assert_not_called()
                prepared.write_text(original)
                self.assertEqual(hourly.publish(args), 0)
                entry = json.loads(state.read_text())["success_case_audit"][slot]
                self.assertEqual(entry["tweet_id"], "123")
                self.assertTrue(entry["actual_use"] and entry["references"][0]["url"])
                self.assertEqual(hourly.prepare(args), 0)
                self.assertEqual(json.loads(state.read_text())["history"][-1]["academy"], meta)
                send.assert_called_once()
                self.generate_image.assert_called_once()

    def test_failed_review_never_creates_publishable_lesson(self):
        self.check_pixels.side_effect = ValueError("review failed")
        slot, meta = next(planned())
        with tempfile.TemporaryDirectory(dir=a.ROOT) as path:
            with self.assertRaisesRegex(ValueError, "review failed"):
                asyncio.run(a.lesson(meta, slot, Path(path)))
            self.assertFalse(list(Path(path).glob("*.json")))

    def test_official_source_attaches_only_the_checked_poster(self):
        from PIL import Image
        from inu_live_post import validated_media_paths
        slot, meta = next(planned())
        with tempfile.TemporaryDirectory(dir=a.ROOT) as tmp:
            path = Path(tmp)
            cap = path / "source.png"
            Image.new("RGB", (640, 640)).save(cap)
            manifest = path / "source.json"
            manifest.write_text(json.dumps(dict(evidence_type="official_data_crop", is_primary_source=True,
                                                 source_url="https://example.org/report")))
            item = dict(id="onchain_1", text="ネットワークの利用状況を確認しました。", topic_type="onchain",
                        visual_route="official_data_crop", media_path=str(cap.relative_to(a.ROOT)),
                        source_manifest=str(manifest.relative_to(a.ROOT)))
            candidate = dict(topic_type="onchain", why_now="test", source_url="https://example.org/report")
            args = hourly.build_parser().parse_args(["--prepare","--dry-run","--state",str(path/"state.json")])
            with patch.dict(os.environ, {"INU_ACADEMY_SCHEDULE":"true"}), \
                    patch.object(hourly, "_scheduled_slot_key", return_value=slot), \
                    patch.object(hourly, "collect_direct_source_candidates", return_value=([candidate], [])), \
                    patch.object(hourly, "_build_item_from_candidate", return_value=(item,candidate)), \
                    patch.object(hourly, "ARTIFACT_DIR", path), patch.object(hourly,"PREPARED_PATH",path/"prepared.json"), \
                    patch.object(hourly, "RESEARCH_REVIEW_PATH", path/"review.json"):
                self.assertEqual(hourly.prepare(args), 0)
            actual = json.loads((path/"prepared.json").read_text())["item"]
            self.assertEqual(actual["topic_type"], "onchain")
            self.assertEqual(validated_media_paths(actual)[1], [path / (slot+"-"+meta["code"]+".png")])
            with self.assertRaises(ValueError):
                validated_media_paths(dict(actual, academy_poster={}))

    def test_all_lessons_pass_delivery_gates(self):
        async def simulate(directory):
            state = {}
            for slot, meta in planned():
                i, c = await a.lesson(meta, slot, directory)
                state = _reserve(state, i, c, slot, dt.datetime.now(dt.timezone.utc))
            self.assertEqual(len(state["reservations"]), 28)
        with tempfile.TemporaryDirectory(dir=a.ROOT) as path:
            asyncio.run(simulate(Path(path)))
