"""直接一次情報の候補化を、外部通信なしで検証する。"""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

import inu_direct_sources


NOW = dt.datetime(2026, 8, 17, 10, 0, tzinfo=dt.timezone.utc)


class DirectSourceTests(unittest.TestCase):
    def test_mempool_candidate_requires_a_material_change(self):
        state: dict = {}
        first = [{"count": 80_000}, {"fastestFee": 4}]
        with patch.object(inu_direct_sources, "_json", side_effect=first):
            candidate, _ = inu_direct_sources._mempool_candidate(NOW, state)
        self.assertIsNone(candidate)

        second = [{"count": 170_000}, {"fastestFee": 12}]
        with patch.object(inu_direct_sources, "_json", side_effect=second):
            candidate, _ = inu_direct_sources._mempool_candidate(
                NOW + dt.timedelta(hours=2), state
            )
        self.assertIsNotNone(candidate)
        self.assertEqual("onchain", candidate["topic_type"])
        self.assertEqual("official_data_crop", candidate["visual_route"])
        self.assertIn("sat/vB", " ".join(candidate["facts"]))

    def test_coinbase_status_uses_only_fresh_unresolved_official_incidents(self):
        payload = {
            "incidents": [
                {
                    "id": "incident-1",
                    "name": "Degraded Performance - Withdrawals",
                    "status": "investigating",
                    "updated_at": "2026-08-17T09:00:00+00:00",
                    "components": [{"name": "Withdrawals"}],
                },
                {
                    "id": "incident-2",
                    "name": "Old incident",
                    "status": "monitoring",
                    "updated_at": "2026-08-16T01:00:00+00:00",
                    "components": [],
                },
            ]
        }
        with patch.object(inu_direct_sources, "_json", return_value=payload):
            candidates = inu_direct_sources._coinbase_status_candidates(NOW)
        self.assertEqual(1, len(candidates))
        self.assertEqual("developing_story", candidates[0]["topic_type"])
        self.assertTrue(candidates[0]["source_url"].endswith("incident-1"))
        self.assertIn("調査中", candidates[0]["facts"][0])

