"""既存文字化け画像の一括修復ルールテスト。"""

import unittest

from repair_garbled_featured_images import normalize_repair_prompt, select_shard


class RepairGarbledFeaturedImagesTests(unittest.TestCase):
    def test_normalized_prompt_always_enables_strict_text_free_mode(self):
        prompt = normalize_repair_prompt('Image prompt: "Smooth blue blocks on a dark surface."')
        self.assertTrue(prompt.startswith("Smooth blue blocks on a dark surface"))
        self.assertIn("no text, print, letters, numbers", prompt)
        self.assertIn("no text", prompt)
        self.assertIn("documents, coins", prompt)

    def test_shards_are_disjoint_and_complete(self):
        items = list(range(10))
        shards = [select_shard(items, index, 3) for index in range(3)]
        self.assertEqual(sorted(item for shard in shards for item in shard), items)
        self.assertFalse(set(shards[0]) & set(shards[1]))


if __name__ == "__main__":
    unittest.main()
