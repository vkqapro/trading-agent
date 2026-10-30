from __future__ import annotations

from types import SimpleNamespace

from src.brokers.ibkr import IBKRClient


class FakeIB:
    def __init__(self, managed_accounts, summary_accounts):
        self._managed_accounts = managed_accounts
        self._summary_accounts = summary_accounts

    def isConnected(self):
        return True

    def managedAccounts(self):
        return self._managed_accounts

    def accountSummary(self):
        return [
            SimpleNamespace(account=account, tag="NetLiquidation", value="4958.78", currency="USD")
            for account in self._summary_accounts
        ]


def identity(managed_accounts, summary_accounts):
    client = object.__new__(IBKRClient)
    client.ib = FakeIB(managed_accounts, summary_accounts)
    return client.get_account_identity()


def test_one_du_managed_account_with_all_summary_rows_is_paper_verified():
    result = identity(["DU5454348"], ["DU5454348", "All", "All"])
    assert result["account_id"] == "DU5454348"
    assert result["accounts"] == ["DU5454348"]
    assert result["paper_verified"] is True
    assert result["environment"] == "paper"


def test_duplicate_du_summary_rows_are_deduplicated():
    result = identity(["DU5454348"], ["DU5454348", "DU5454348", "All"])
    assert result["paper_verified"] is True
    assert result["summary_accounts"] == ["DU5454348"]


def test_managed_du_and_matching_summary_du_pass():
    result = identity(["DU5454348"], ["DU5454348"])
    assert result["evidence"] == "ibkr_managed_account_du_prefix"
    assert result["paper_verified"] is True


def test_managed_du_and_conflicting_summary_du_fail_closed():
    result = identity(["DU1111111"], ["DU1111111", "DU2222222"])
    assert result["paper_verified"] is False
    assert result["evidence"] == "account_summary_ambiguous"


def test_two_managed_du_accounts_fail_closed():
    result = identity(["DU1111111", "DU2222222"], ["All"])
    assert result["paper_verified"] is False
    assert result["evidence"] == "managed_accounts_ambiguous"


def test_one_managed_u_account_is_live_not_paper():
    result = identity(["U123456"], ["U123456", "All"])
    assert result["paper_verified"] is False
    assert result["environment"] == "live"


def test_unknown_summary_account_format_fails_closed():
    result = identity(["DU5454348"], ["DU5454348", "DESK_GROUP"])
    assert result["paper_verified"] is False
    assert result["evidence"] == "account_summary_unknown_id"


def test_only_all_summary_rows_without_managed_account_fail():
    result = identity([], ["All", "All"])
    assert result["account_id"] is None
    assert result["paper_verified"] is False
    assert result["evidence"] == "managed_accounts_missing"
