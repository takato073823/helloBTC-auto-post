"""既存文字化け画像の一括修復ルールテスト。"""

import unittest

from repair_garbled_featured_images import (
    is_repair_candidate,
    normalize_repair_prompt,
    select_shard,
)


class RepairGarbledFeaturedImagesTests(unittest.TestCase):
    def test_normalized_prompt_preserves_photojournalism_and_text_quality(self):
        prompt = normalize_repair_prompt('Image prompt: "A regulatory document on a desk."')
        self.assertTrue(prompt.startswith("A regulatory document on a desk"))
        self.assertIn("photorealistic Reuters-style editorial news photography", prompt)
        self.assertIn("visible writing on a relevant physical item is allowed", prompt)
        self.assertIn("never use pseudo-text", prompt)
        self.assertNotIn("minimalist", prompt.lower())

    def test_shards_are_disjoint_and_complete(self):
        items = list(range(10))
        shards = [select_shard(items, index, 3) for index in range(3)]
        self.assertEqual(sorted(item for shard in shards for item in shard), items)
        self.assertFalse(set(shards[0]) & set(shards[1]))

    def test_repaired_images_require_explicit_retry_mode(self):
        legacy = "https://example.com/featured-12345.jpg"
        repaired = "https://example.com/featured-repaired-example-12345.jpg"
        replaced = "https://example.com/featured-replaced-example-12345.jpg"
        self.assertTrue(is_repair_candidate(legacy, include_repaired=False))
        self.assertFalse(is_repair_candidate(repaired, include_repaired=False))
        self.assertTrue(is_repair_candidate(repaired, include_repaired=True))
        self.assertFalse(is_repair_candidate(replaced, include_repaired=True))


if __name__ == "__main__":
    unittest.main()
