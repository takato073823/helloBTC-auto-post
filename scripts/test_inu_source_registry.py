from __future__ import annotations

import unittest

from inu_editorial_policy import AUTO_SELECTABLE_TOPIC_TYPES
from inu_source_registry import discovery_sources, discovery_x_handles, load_registry, topic_source_context, topic_sources


class INUSourceRegistryTests(unittest.TestCase):
    def test_every_auto_category_has_fixed_primary_sources(self):
        load_registry()
        for topic in AUTO_SELECTABLE_TOPIC_TYPES:
            with self.subTest(topic=topic):
                self.assertTrue(topic_sources(topic))

    def test_discovery_sources_are_https_and_x_handles_are_valid(self):
        self.assertGreaterEqual(len(discovery_sources()), 7)
        self.assertIn("WatcherGuru", discovery_x_handles())
        self.assertTrue(all(url.startswith("https://") for _, url in discovery_sources()))

    def test_context_can_be_narrowed_to_one_topic(self):
        context = topic_source_context("macro_event")
        self.assertIn("BLS Release Calendar", context)
        self.assertNotIn("Coinbase Status", context)

    def test_new_educational_topics_use_primary_discovery_sources(self):
        prediction = topic_source_context("prediction_market_shift")
        custody = topic_source_context("institutional_custody")
        regulation = topic_source_context("regulatory_rule_change")
        self.assertIn("Polymarket Markets", prediction)
        self.assertIn("Citi News", custody)
        self.assertIn("SEC Rules and Regulations", regulation)
        self.assertIn("Polymarket", discovery_x_handles())


if __name__ == "__main__":
    unittest.main()
