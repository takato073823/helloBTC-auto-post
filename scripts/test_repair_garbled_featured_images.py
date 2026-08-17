"""既存文字化け画像の一括修復ルールテスト。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from repair_garbled_featured_images import (
    is_repair_candidate,
    load_slug_allowlist,
    normalize_repair_prompt,
    select_shard,
)


class RepairGarbledFeaturedImagesTests(unittest.TestCase):
    def test_normalized_prompt_preserves_photojournalism_and_text_quality(self):
        prompt = normalize_repair_prompt('Image prompt: "A regulatory document on a desk."')
        self.assertTrue(prompt.startswith("A regulatory document on a desk"))
        self.assertIn("photorealistic Reuters-style editorial news photography", prompt)
        self.assertIn("prefer subjects without typographic surfaces", prompt)
        self.assertIn("pseudo-text", prompt)
        self.assertNotIn("minimalist", prompt.lower())
        self.assertNotIn("no text", prompt.lower())
        self.assertNotIn("no writing", prompt.lower())

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

    def test_loads_only_first_tsv_column_as_slug_allowlist(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "targets.tsv"
            path.write_text("first\thttps://example.com/1.jpg\nsecond\thttps://example.com/2.jpg\n")
            with patch("repair_garbled_featured_images.__file__", str(Path(directory) / "module.py")):
                self.assertEqual({"first", "second"}, load_slug_allowlist("targets.tsv"))


if __name__ == "__main__":
    unittest.main()
