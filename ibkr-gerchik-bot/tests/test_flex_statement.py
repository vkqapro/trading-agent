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
        self.assertEqual(trades[0]["trade_time"], "2026-09-03T15:43:05-04:00")
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
        self.assertEqual(trades[0]["trade_time"], "2026-09-02T09:30:06-04:00")

    def test_disabled_configuration_fails_closed(self) -> None:
        with (
            patch.object(flex_statement, "SETTINGS", SimpleNamespace(flex_statement_enabled=False, flex_statement_token="", flex_statement_query_id="", flex_statement_lookback_days=7)),
            patch.object(flex_statement, "discover_trade_report", return_value=(None, [])),
        ):
            result = flex_statement.run_flex_catch_up()
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["trades"], [])

    def test_local_trade_report_can_run_without_flex_credentials(self) -> None:
        local_trades = [{
            "symbol": "XP",
            "side": "SLD",
            "quantity": 9.0,
            "price": 23.52,
            "trade_time": "20261005 08:00:01",
            "exec_id": "tws-xp-close",
        }]
        with (
            patch.object(flex_statement, "SETTINGS", SimpleNamespace(flex_statement_enabled=False, flex_statement_token="", flex_statement_query_id="", flex_statement_lookback_days=7)),
            patch.object(flex_statement, "discover_trade_report", return_value=(flex_statement.Path("reports/trade_report"), local_trades)),
        ):
            result = flex_statement.run_flex_catch_up(commit=False, allow_local_report=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "tws_trade_report")
        self.assertEqual(result["trades"], local_trades)

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

    def test_statement_close_merges_early_live_callback(self) -> None:
        with TemporaryDirectory() as directory:
            root = flex_statement.Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            requests_path = root / "order_requests.json"
            closes_path = runtime / "closed_positions.json"
            unmatched_path = runtime / "unmatched.json"
            requests_path.write_text(
                '{"requests":[{"id":"req-1","symbol":"AVGO","quantity":2,"entry":360.0,"result":{"limit_order_id":"target-1"}}]}',
                encoding="utf-8",
            )
            closes_path.write_text(
                '{"positions":[{"symbol":"AVGO","quantity":2,"entry":360.0,"closed_at":"2026-10-08T15:00:00-04:00","exit_price":367.34,"exit_quantity":2,"exit_order_id":"target-1","exit_execution_id":"live-exec","request_id":"req-1","broker_realized_pnl":0.0,"source":"IBKR"}]}',
                encoding="utf-8",
            )
            with (
                patch.object(flex_statement, "MEMORY_DIR", root),
                patch.object(flex_statement, "FLEX_UNMATCHED_PATH", unmatched_path),
            ):
                result = flex_statement.reconcile_statement_trades(
                    [{
                        "symbol": "AVGO", "side": "SELL", "quantity": 2.0,
                        "price": 367.34, "trade_time": "2026-10-08T15:04:00-04:00",
                        "order_id": "target-1", "exec_id": "flex-exec",
                        "commission": -0.10, "fee": 0.0, "realized_pnl": 7.58,
                    }],
                    return_details=True,
                )
            payload = __import__("json").loads(closes_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"added": 1, "unmatched": 0})
        self.assertEqual(len(payload["positions"]), 1)
        self.assertEqual(payload["positions"][0]["exit_execution_id"], "flex-exec")
        self.assertEqual(payload["positions"][0]["source"], "IBKR_FLEX_STATEMENT")
        self.assertAlmostEqual(payload["positions"][0]["broker_realized_pnl"], 7.58)

    def test_broker_only_statement_close_is_persisted_for_order_journal(self) -> None:
        with TemporaryDirectory() as directory:
            root = flex_statement.Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            requests_path = root / "order_requests.json"
            closes_path = runtime / "closed_positions.json"
            unmatched_path = runtime / "unmatched.json"
            requests_path.write_text('{"requests":[]}', encoding="utf-8")
            closes_path.write_text('{"positions":[]}', encoding="utf-8")
            with (
                patch.object(flex_statement, "MEMORY_DIR", root),
                patch.object(flex_statement, "FLEX_UNMATCHED_PATH", unmatched_path),
            ):
                result = flex_statement.reconcile_statement_trades(
                    [{
                        "symbol": "SNAP", "side": "SELL", "quantity": 77.0,
                        "price": 6.20, "trade_time": "2026-10-14T10:00:00-04:00",
                        "exec_id": "snap-flex", "realized_pnl": -24.34,
                        "commission": -1.0, "fee": 0.0,
                    }],
                    return_details=True,
                )
            payload = __import__("json").loads(closes_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"added": 1, "unmatched": 0})
        self.assertEqual(payload["positions"][0]["request_id"], "")
        self.assertEqual(payload["positions"][0]["source"], "IBKR_FLEX_STATEMENT")
        self.assertAlmostEqual(payload["positions"][0]["broker_realized_pnl"], -24.34)

    def test_existing_live_and_statement_duplicates_are_canonicalized(self) -> None:
        with TemporaryDirectory() as directory:
            root = flex_statement.Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (root / "order_requests.json").write_text('{"requests":[]}', encoding="utf-8")
            closes_path = runtime / "closed_positions.json"
            closes_path.write_text(
                '{"positions":['
                '{"symbol":"AVGO","quantity":1,"exit_quantity":1,"exit_price":378.23,"closed_at":"2026-10-06T16:51:00+00:00","exit_order_id":"758221299","exit_execution_id":"live","request_id":"req-1","broker_realized_pnl":0,"source":"IBKR"},'
                '{"symbol":"AVGO","quantity":1,"exit_quantity":1,"exit_price":378.23,"closed_at":"2026-10-06T16:51:00-04:00","exit_order_id":"758221299","exit_execution_id":"flex","request_id":"req-1","broker_realized_pnl":12.93,"source":"IBKR_FLEX_STATEMENT"}'
                ']}',
                encoding="utf-8",
            )
            with (
                patch.object(flex_statement, "MEMORY_DIR", root),
                patch.object(flex_statement, "FLEX_UNMATCHED_PATH", runtime / "unmatched.json"),
            ):
                result = flex_statement.reconcile_statement_trades([], return_details=True)
            payload = __import__("json").loads(closes_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"added": 0, "unmatched": 0})
        self.assertEqual(len(payload["positions"]), 1)
        self.assertEqual(payload["positions"][0]["source"], "IBKR_FLEX_STATEMENT")
        self.assertAlmostEqual(payload["positions"][0]["broker_realized_pnl"], 12.93)

    def test_account_trades_record_is_not_overwritten_by_older_tws_record(self) -> None:
        with TemporaryDirectory() as directory:
            root = flex_statement.Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (root / "order_requests.json").write_text('{"requests":[]}', encoding="utf-8")
            closes_path = runtime / "closed_positions.json"
            closes_path.write_text(
                '{"positions":[{"symbol":"PG","quantity":6,"exit_quantity":6,"exit_price":164.15,"closed_at":"2026-10-22T13:30:54Z","exit_execution_id":"ibkr-api","request_id":"req-1","broker_realized_pnl":27.139693,"source":"IBKR_ACCOUNT_TRADES"}]}',
                encoding="utf-8",
            )
            with (
                patch.object(flex_statement, "MEMORY_DIR", root),
                patch.object(flex_statement, "FLEX_UNMATCHED_PATH", runtime / "unmatched.json"),
            ):
                result = flex_statement.reconcile_statement_trades([
                    {
                        "symbol": "PG", "side": "SELL", "quantity": 6,
                        "price": 164.15, "trade_time": "2026-10-22T13:30:54-04:00",
                        "exec_id": "tws-pg", "order_id": "",
                        "realized_pnl": 113.7, "commission": 0.0, "fee": 0.0,
                    }
                ], return_details=True)
            payload = __import__("json").loads(closes_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"added": 0, "unmatched": 0})
        self.assertEqual(len(payload["positions"]), 1)
        self.assertEqual(payload["positions"][0]["source"], "IBKR_ACCOUNT_TRADES")
        self.assertAlmostEqual(payload["positions"][0]["broker_realized_pnl"], 27.139693)
