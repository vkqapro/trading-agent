from __future__ import annotations

from unittest import TestCase

from dashboard_react.server import _parse_stock_symbol_upload


class BulkSymbolUploadTests(TestCase):
    def test_parse_stock_symbol_upload_skips_headers_and_dedupes(self) -> None:
        parsed, invalid = _parse_stock_symbol_upload(
            {"csv_text": "symbol,AAPL,msft\nTSLA,AAPL,BAD-DASH\n"}
        )

        self.assertEqual(parsed, ["AAPL", "MSFT", "TSLA"])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["value"], "BAD-DASH")
