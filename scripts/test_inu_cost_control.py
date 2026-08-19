import datetime as dt
import unittest
from unittest.mock import patch

from inu_cost_control import claim_api_call, usage_snapshot


NOW = dt.datetime(2026, 8, 19, 4, 0, tzinfo=dt.timezone.utc)


class INUCostControlTests(unittest.TestCase):
    def test_daily_limit_blocks_the_next_paid_call(self):
        state = {}
        with patch.dict("os.environ", {"INU_MAX_GROK_X_SEARCHES_PER_DAY": "2"}, clear=False):
            self.assertTrue(claim_api_call(state, "grok_x_search", NOW))
            self.assertTrue(claim_api_call(state, "grok_x_search", NOW))
            self.assertFalse(claim_api_call(state, "grok_x_search", NOW))
            self.assertEqual(2, usage_snapshot(state, NOW)["grok_x_search"]["used"])

    def test_jst_date_change_resets_counts(self):
        state = {}
        with patch.dict("os.environ", {"INU_MAX_OPENAI_WEB_SEARCHES_PER_DAY": "1"}, clear=False):
            self.assertTrue(claim_api_call(state, "openai_web_search", NOW))
            self.assertFalse(claim_api_call(state, "openai_web_search", NOW))
            self.assertTrue(claim_api_call(state, "openai_web_search", NOW + dt.timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
