import unittest

from update_bingx_invite_links import NEW_CODE, NEW_URL, replace_legacy_invite_references


class UpdateBingxInviteLinksTests(unittest.TestCase):
    def test_replaces_every_supported_legacy_reference(self):
        source = (
            'https://bingxdao.com/invite/XXCCJX/ '
            'https://bingxdao.com/invite/XXCCJX '
            'https://bingx.com/en-us/account/register/?ref=XXCCJX '
            '招待コード：XXCCJX'
        )
        updated, count = replace_legacy_invite_references(source)

        self.assertEqual(4, count)
        self.assertEqual(NEW_URL, updated.split()[0])
        self.assertEqual(NEW_URL, updated.split()[1])
        self.assertIn(NEW_CODE, updated)
        self.assertNotIn("XXCCJX", updated)

    def test_is_idempotent(self):
        source = f'招待コード：{NEW_CODE} <a href="{NEW_URL}">登録</a>'
        self.assertEqual((source, 0), replace_legacy_invite_references(source))


if __name__ == "__main__":
    unittest.main()
