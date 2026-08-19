"""公式HTMLの証拠画像フォールバックを検証する。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import inu_source_capture


class INUSourceCaptureTests(unittest.TestCase):
    def test_official_html_fallback_keeps_exact_source_and_adds_base(self):
        response = Mock()
        response.text = (
            "<html><head><title>SEC</title></head>"
            "<body>Regulation Crypto Assets</body></html>"
        )
        response.headers = {"content-type": "text/html; charset=utf-8"}
        response.raise_for_status.return_value = None
        url = "https://www.sec.gov/newsroom/example?a=1&b=2"
        with patch.object(
            inu_source_capture.requests, "get", return_value=response
        ) as request:
            rendered = inu_source_capture._official_html_with_base(url)
        self.assertIn(
            '<base href="https://www.sec.gov/newsroom/example?a=1&amp;b=2">',
            rendered,
        )
        self.assertIn("Regulation Crypto Assets", rendered)
        request.assert_called_once()

    def test_official_html_fallback_rejects_non_html(self):
        response = Mock()
        response.text = "binary"
        response.headers = {"content-type": "application/pdf"}
        response.raise_for_status.return_value = None
        with patch.object(inu_source_capture.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "HTML"):
                inu_source_capture._official_html_with_base(
                    "https://www.sec.gov/file.pdf"
                )


if __name__ == "__main__":
    unittest.main()
