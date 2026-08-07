from types import SimpleNamespace
import unittest
from unittest.mock import patch

import x_poster


class XPosterSocialActionsTests(unittest.TestCase):
    @patch("x_poster._secrets_available", return_value=True)
    @patch("x_poster._get_client")
    def test_follow_uses_authenticated_x_endpoint(self, get_client, _secrets):
        get_client.return_value.follow_user.return_value = SimpleNamespace(data={"following": True, "pending_follow": False})
        self.assertTrue(x_poster.follow_user("123"))
        get_client.return_value.follow_user.assert_called_once_with("123", user_auth=True)

    @patch("x_poster._get_client")
    def test_invalid_follow_id_never_calls_x(self, get_client):
        self.assertFalse(x_poster.follow_user("not-an-id"))
        get_client.assert_not_called()

    @patch("x_poster._secrets_available", return_value=True)
    @patch("x_poster._get_client")
    def test_unfollow_requires_an_explicit_success_response(self, get_client, _secrets):
        get_client.return_value.unfollow_user.return_value = SimpleNamespace(data={"following": False, "pending_follow": False})
        self.assertTrue(x_poster.unfollow_user("123"))
        get_client.return_value.unfollow_user.return_value = SimpleNamespace(data={})
        self.assertFalse(x_poster.unfollow_user("123"))


if __name__ == "__main__":
    unittest.main()
