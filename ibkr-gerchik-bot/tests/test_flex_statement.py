from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace

from src.journal import flex_statement


class FlexStatementTests(TestCase):
    def test_parse_flex_trade_normalizes_execution_fields(self) -> None:
        xml = b'''<FlexQueryResponse><Trades><Trade symbol="AMH" buySell="SELL" quantity="17" tradePrice="34.06" tradeDate="2026-08-10T13:03:21-04:00" ibOrderID="720" ibExecID="amh-exec" ibCommission="-1.02" ibTax="0" /></Trades></FlexQueryResponse>'''
        self.assertEqual(
            flex_statement.parse_flex_trades(xml),
            [{
                "symbol": "AMH", "side": "SELL", "quantity": 17.0, "price": 34.06,
                "commission": -1.02, "fee": 0.0, "realized_pnl": None,
                "trade_time": "2026-08-10T13:03:21-04:00", "order_id": "720",
                "exec_id": "amh-exec", "account": "",
            }],
        )

    def test_parse_tws_trade_report_csv(self) -> None:
        raw = b"Fin Instrument,Symbol,Security Type,Action,Quantity,Price,Time,Date,Account\nXP,XP,STK,BOT,9,19.80,15:43:05,20260903,DU5454348\n"
        trades = flex_statement.parse_trade_report_csv(raw)
        self.assertEqual(trades[0]["symbol"], "XP")
        self.assertEqual(trades[0]["side"], "BOT")
        self.assertEqual(trades[0]["quantity"], 9.0)
        self.assertEqual(trades[0]["trade_time"], "20260903 15:43:05")
        self.assertTrue(trades[0]["exec_id"].startswith("tws-"))

    def test_discover_extensionless_tws_report(self) -> None:
        report = flex_statement.Path(__file__).parents[1] / "reports" / "trade_report"
        if not report.exists():
            self.skipTest("live TWS report is not present")
        path, trades = flex_statement.discover_trade_report()
        self.assertEqual(path, max(report.parent.iterdir(), key=lambda item: item.stat().st_mtime))
        self.assertGreaterEqual(len(trades), 1)

    def test_parse_ibkr_trade_confirmation_html(self) -> None:
        raw = b'''<table><tr><th>Acct ID</th><th>Symbol</th><th>Trade Date/Time</th><th>Type</th><th>Quantity</th><th>Price</th><th>Comm</th><th>Fee</th><th>Order Type</th></tr><tr><td>DU1</td><td>PLTR</td><td>2026-09-02, 09:30:06</td><td>SELL</td><td>-1</td><td>175.1400</td><td>-1.00</td><td>0.00</td><td>STP</td></tr></table>'''
        trades = flex_statement.parse_trade_report_html(raw)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "PLTR")
        self.assertEqual(trades[0]["trade_time"], "2026-09-02 09:30:06")

    def test_disabled_configuration_fails_closed(self) -> None:
        with patch.object(flex_statement, "SETTINGS", SimpleNamespace(flex_statement_enabled=False, flex_statement_token="", flex_statement_query_id="", flex_statement_lookback_days=7)):
            result = flex_statement.run_flex_catch_up()
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["trades"], [])

    def test_catch_up_deduplicates_execution_ids(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = flex_statement.Path(directory) / "state.json"
            archive_dir = flex_statement.Path(directory) / "archive"
            with (
                patch.object(flex_statement, "FLEX_STATE_PATH", state_path),
                patch.object(flex_statement, "FLEX_ARCHIVE_DIR", archive_dir),
                patch.object(flex_statement, "SETTINGS", SimpleNamespace(flex_statement_enabled=True, flex_statement_token="token", flex_statement_query_id="query", flex_statement_lookback_days=7)),
                patch.object(flex_statement, "fetch_flex_statement", return_value=b"<FlexQueryResponse />"),
                patch.object(flex_statement, "parse_flex_trades", return_value=[{"exec_id": "one", "symbol": "AMH"}]),
            ):
                first = flex_statement.run_flex_catch_up()
                second = flex_statement.run_flex_catch_up()
        self.assertEqual(len(first["trades"]), 1)
        self.assertEqual(second["trades"], [])
