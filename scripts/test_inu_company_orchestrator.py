from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inu_company_orchestrator as company
from x_poster import XCreditsDepletedError


class INUCompanyOrchestratorTests(unittest.TestCase):
    def test_prepare_records_approved_department_handoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared.json"
            company_state = root / "company.json"
            args = argparse.Namespace(
                prepared=str(prepared),
                company_state=str(company_state),
                state=str(root / "state.json"),
            )

            def fake_prepare(_args):
                prepared.write_text(
                    json.dumps(
                        {
                            "slot": "slot-a",
                            "item": {"id": "post-a", "text": "本文"},
                            "candidate": {
                                "topic_type": "onchain",
                                "source_url": "https://example.com/official",
                                "hook": "オンチェーンの数値更新",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return 0

            with patch.object(company.inu_auto_hourly, "prepare", side_effect=fake_prepare), patch.object(
                company, "_quality_handoff", return_value={"slot": "slot-a", "post_id": "post-a"}
            ):
                self.assertEqual(0, company.prepare(args))
            audit = json.loads(company_state.read_text(encoding="utf-8"))["runs"][-1]
            self.assertEqual("approved_outbox", audit["status"])
            self.assertEqual("approved", audit["departments"]["quality"])

    def test_publish_failure_releases_lease_and_records_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared.json"
            state_path = root / "state.json"
            company_state = root / "company.json"
            prepared.write_text(json.dumps({"item": {}, "candidate": {}, "slot": "slot-a"}), encoding="utf-8")
            state_path.write_text(
                json.dumps({"reservations": [{"slot": "slot-a", "post_id": "post-a"}]}),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                prepared=str(prepared),
                state=str(state_path),
                company_state=str(company_state),
            )
            handoff = {"slot": "slot-a", "post_id": "post-a", "topic_type": "onchain"}
            with patch.object(company, "_quality_handoff", return_value=handoff), patch.object(
                company.inu_auto_hourly, "publish", side_effect=RuntimeError("X 403")
            ):
                with self.assertRaisesRegex(RuntimeError, "403"):
                    company.publish(args)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([], state["reservations"])
            self.assertEqual("publish", state["delivery_failures"][-1]["stage"])
            audit = json.loads(company_state.read_text(encoding="utf-8"))["runs"][-1]
            self.assertEqual("retryable_delivery_failure", audit["status"])

    def test_x_credit_failure_blocks_paid_research_for_six_hours(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared.json"
            state_path = root / "state.json"
            company_state = root / "company.json"
            prepared.write_text(json.dumps({"item": {}, "candidate": {}, "slot": "slot-a"}), encoding="utf-8")
            state_path.write_text(
                json.dumps({"reservations": [{"slot": "slot-a", "post_id": "post-a"}]}),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                prepared=str(prepared),
                state=str(state_path),
                company_state=str(company_state),
            )
            handoff = {"slot": "slot-a", "post_id": "post-a", "topic_type": "onchain"}
            with patch.object(company, "_quality_handoff", return_value=handoff), patch.object(
                company.inu_auto_hourly,
                "publish",
                side_effect=XCreditsDepletedError("X_API_CREDITS_DEPLETED"),
            ):
                with self.assertRaises(XCreditsDepletedError):
                    company.publish(args)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("X_API_CREDITS_DEPLETED", state["external_blocks"]["x_api"]["reason"])
            audit = json.loads(company_state.read_text(encoding="utf-8"))["runs"][-1]
            self.assertEqual("blocked_external", audit["status"])

    def test_prepare_skips_paid_departments_during_x_credit_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "state.json"
            company_state = root / "company.json"
            retry_after = (
                company.dt.datetime.now(company.dt.timezone.utc)
                + company.dt.timedelta(hours=1)
            ).isoformat()
            state_path.write_text(
                json.dumps({"external_blocks": {"x_api": {"retry_after": retry_after}}}),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                state=str(state_path),
                company_state=str(company_state),
                prepared=str(root / "prepared.json"),
            )
            with patch.object(company.inu_auto_hourly, "prepare") as paid_prepare, patch.object(
                company.inu_auto_hourly, "_emit_output"
            ) as output:
                self.assertEqual(0, company.prepare(args))
            paid_prepare.assert_not_called()
            output.assert_called_once_with("ready", "false")
            audit = json.loads(company_state.read_text(encoding="utf-8"))["runs"][-1]
            self.assertEqual("blocked_external", audit["status"])


if __name__ == "__main__":
    unittest.main()
