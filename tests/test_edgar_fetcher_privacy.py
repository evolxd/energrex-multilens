import os
import unittest
from unittest.mock import patch

from scoring.edgar_fetcher import TICKER_CIK, _sec_headers


class EdgarFetcherPrivacyTests(unittest.TestCase):
    def test_live_fetch_requires_runtime_user_agent(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SEC_USER_AGENT is required"):
                _sec_headers()

    def test_user_agent_is_loaded_from_runtime_without_rewriting_it(self):
        expected = "ENERGREX Research monitored-contact@example.com"
        with patch.dict(os.environ, {"SEC_USER_AGENT": expected}, clear=True):
            self.assertEqual(_sec_headers(), {"User-Agent": expected})

    def test_real_company_cik_mapping_is_stable(self):
        self.assertEqual(TICKER_CIK["DVA"], "0000927066")
        self.assertEqual(TICKER_CIK["ADBE"], "0000796343")
        self.assertEqual(TICKER_CIK["FUTU"], "0001754581")


if __name__ == "__main__":
    unittest.main()
