from __future__ import annotations

import datetime as dt
import unittest

from inu_pipeline_contracts import (
    content_fingerprint,
    event_fingerprint,
    is_semantic_event_duplicate,
    is_active_reservation,
    normalize_source_url,
    prune_stale_reservations,
    release_reservation,
)


UTC = dt.timezone.utc


class INUPipelineContractsTests(unittest.TestCase):
    def test_tracking_parameters_do_not_change_event_identity(self):
        left = {
            "topic_type": "onchain",
            "source_url": "https://example.com/release?utm_source=x&id=7",
            "hook": "ETFの純流入が10億ドルを突破",
        }
        right = {
            **left,
            "source_url": "https://EXAMPLE.com/release?id=7&utm_medium=social#top",
        }
        self.assertEqual(event_fingerprint(left), event_fingerprint(right))
        self.assertEqual("https://example.com/release?id=7", normalize_source_url(left["source_url"]))

    def test_content_fingerprint_ignores_spacing_and_punctuation(self):
        self.assertEqual(content_fingerprint("$BTC、上昇。"), content_fingerprint("$BTC 上昇"))

    def test_same_event_with_different_urls_is_detected(self):
        candidate = {
            "topic_type": "etf_flow",
            "source_url": "https://issuer.example/flow",
            "hook": "📈 $BTC現物ETF、純流入が10億ドルを突破",
        }
        existing = {
            "topic_type": "etf_flow",
            "source_url": "https://data.example/etf",
            "hook": "$BTC現物ETFの純流入、10億ドルを突破",
        }
        self.assertTrue(is_semantic_event_duplicate(candidate, existing))

    def test_different_categories_are_not_semantic_duplicates(self):
        self.assertFalse(
            is_semantic_event_duplicate(
                {"topic_type": "etf_flow", "hook": "$BTC現物ETF、10億ドル流入"},
                {"topic_type": "onchain", "hook": "$BTC現物ETF、10億ドル流入"},
            )
        )

    def test_stale_reservations_are_released_and_audited(self):
        now = dt.datetime(2026, 8, 19, 0, 40, tzinfo=UTC)
        state = {
            "reservations": [
                {
                    "slot": "old",
                    "post_id": "p-old",
                    "reserved_at": "2026-08-19T00:00:00+00:00",
                    "lease_expires_at": "2026-08-19T00:30:00+00:00",
                },
                {
                    "slot": "new",
                    "post_id": "p-new",
                    "lease_expires_at": "2026-08-19T01:00:00+00:00",
                },
            ]
        }
        updated, expired = prune_stale_reservations(state, now)
        self.assertEqual(["p-old"], [row["post_id"] for row in expired])
        self.assertEqual(["p-new"], [row["post_id"] for row in updated["reservations"]])
        self.assertEqual("reservation_lease_expired", updated["delivery_failures"][-1]["reason"])

    def test_publish_failure_releases_exact_lease(self):
        now = dt.datetime(2026, 8, 19, 1, 0, tzinfo=UTC)
        state = {
            "reservations": [
                {"slot": "a", "post_id": "p1"},
                {"slot": "b", "post_id": "p2"},
            ]
        }
        updated = release_reservation(state, slot="a", post_id="p1", now=now, reason="403")
        self.assertEqual(["p2"], [row["post_id"] for row in updated["reservations"]])
        self.assertEqual("403", updated["delivery_failures"][-1]["reason"])

    def test_missing_legacy_expiry_is_active_only_for_thirty_minutes(self):
        row = {"reserved_at": "2026-08-19T00:00:00+00:00"}
        self.assertTrue(is_active_reservation(row, dt.datetime(2026, 8, 19, 0, 29, tzinfo=UTC)))
        self.assertFalse(is_active_reservation(row, dt.datetime(2026, 8, 19, 0, 31, tzinfo=UTC)))


if __name__ == "__main__":
    unittest.main()
