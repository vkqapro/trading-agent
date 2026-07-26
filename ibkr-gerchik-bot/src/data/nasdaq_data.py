"""Optional Nasdaq historical candle retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import pandas as pd
import requests

from src.config import LOGGER, NasdaqDataConfig, SETTINGS

_BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class NasdaqDataClient:
    """Fetch Nasdaq historical bars when a configured subscription is present."""

    def __init__(
        self,
        config: NasdaqDataConfig | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or SETTINGS.nasdaq_data
        self.session = session or requests.Session()
        self._cloud_token: str | None = None
        self._cloud_token_expires_at = datetime.min.replace(tzinfo=timezone.utc)

    @property
    def enabled(self) -> bool:
        return self.config.enabled and (
            self.config.data_link_configured or self.config.cloud_configured
        )

    def get_daily_bars(self, symbol: str, duration: str) -> pd.DataFrame:
        if not self.enabled or not self.config.data_link_configured:
            return _empty_bars()
        try:
            return self._get_data_link_daily_bars(symbol, duration)
        except requests.RequestException as exc:
            LOGGER.warning("Nasdaq Data Link daily fetch failed %s: %s", symbol, exc)
            return _empty_bars()
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Nasdaq Data Link daily response unreadable %s: %s", symbol, exc)
            return _empty_bars()

    def get_intraday_bars(self, symbol: str, duration: str, bar_size: str) -> pd.DataFrame:
        if not self.enabled or not self.config.cloud_configured:
            return _empty_bars()
        precision = _cloud_precision(bar_size)
        if precision is None:
            return _empty_bars()
        range_name = _cloud_range(duration, precision)
        if range_name is None:
            return _empty_bars()
        try:
            return self._get_cloud_bars(symbol, precision=precision, range_name=range_name)
        except requests.RequestException as exc:
            LOGGER.warning("Nasdaq Cloud bars fetch failed %s %s: %s", symbol, bar_size, exc)
            return _empty_bars()
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Nasdaq Cloud bars response unreadable %s %s: %s", symbol, bar_size, exc)
            return _empty_bars()

    def _get_data_link_daily_bars(self, symbol: str, duration: str) -> pd.DataFrame:
        base_url = self.config.data_link_base_url.rstrip("/")
        database = self.config.data_link_eod_database.strip().upper()
        endpoint = f"{base_url}/datasets/{database}/{symbol.upper()}/data.json"
        params = {
            "api_key": self.config.data_link_api_key,
            "start_date": _duration_start_date(duration).isoformat(),
            "order": "asc",
        }
        response = self.session.get(
            endpoint,
            params=params,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        dataset = payload.get("dataset_data") or payload.get("dataset") or payload
        return _normalize_records(dataset.get("data", []), dataset.get("column_names", []))

    def _get_cloud_bars(self, symbol: str, *, precision: str, range_name: str) -> pd.DataFrame:
        token = self._cloud_access_token()
        base_url = self.config.cloud_base_url.rstrip("/")
        source = self.config.cloud_source.strip() or "CQT"
        offset = self.config.cloud_offset.strip() or "delayed"
        endpoint = (
            f"{base_url}/v2/{source}/{offset}/equities/bars/"
            f"{symbol.upper()}/{precision}/false/{range_name}"
        )
        response = self.session.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        records = _extract_cloud_records(response.json(), symbol)
        return _normalize_mapping_records(records)

    def _cloud_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._cloud_token and now < self._cloud_token_expires_at:
            return self._cloud_token

        base_url = self.config.cloud_base_url.rstrip("/")
        response = self.session.post(
            f"{base_url}/v1/auth/token",
            json={
                "client_id": self.config.cloud_client_id,
                "client_secret": self.config.cloud_client_secret,
            },
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload["access_token"])
        expires_in = int(payload.get("expires_in", 3300))
        self._cloud_token = token
        self._cloud_token_expires_at = now + timedelta(seconds=max(60, expires_in - 60))
        return token


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=_BAR_COLUMNS)


def _duration_start_date(duration: str) -> datetime.date:
    normalized = str(duration or "").strip().upper()
    parts = normalized.split()
    if len(parts) != 2:
        return (datetime.now(timezone.utc) - timedelta(days=365)).date()

    amount = int(float(parts[0]))
    unit = parts[1]
    if unit.startswith("Y"):
        days = amount * 365
    elif unit.startswith("M"):
        days = amount * 31
    elif unit.startswith("W"):
        days = amount * 7
    else:
        days = amount
    return (datetime.now(timezone.utc) - timedelta(days=days)).date()


def _normalize_records(records: Iterable[Iterable[Any]], column_names: Iterable[str]) -> pd.DataFrame:
    columns = [str(column).strip().lower().replace(" ", "_") for column in column_names]
    rows = [dict(zip(columns, row)) for row in records]
    return _normalize_mapping_records(rows)


def _normalize_mapping_records(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        lowered = {str(key).strip().lower().replace(" ", "_"): value for key, value in record.items()}
        rows.append(
            {
                "date": _first_present(lowered, "date", "time", "timestamp", "t"),
                "open": _first_present(lowered, "open", "o"),
                "high": _first_present(lowered, "high", "h"),
                "low": _first_present(lowered, "low", "l"),
                "close": _first_present(lowered, "close", "c", "last"),
                "volume": _first_present(lowered, "volume", "v"),
            }
        )
    frame = pd.DataFrame(rows, columns=_BAR_COLUMNS)
    if frame.empty:
        return _empty_bars()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _first_present(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return None


def _cloud_precision(bar_size: str) -> str | None:
    normalized = " ".join(str(bar_size).strip().lower().split())
    return {
        "1 min": "1minute",
        "1 mins": "1minute",
        "5 mins": "5minute",
        "5 min": "5minute",
        "10 mins": "10minute",
        "10 min": "10minute",
        "15 mins": "15minute",
        "15 min": "15minute",
        "30 mins": "30minute",
        "30 min": "30minute",
        "1 day": "1day",
    }.get(normalized)


def _cloud_range(duration: str, precision: str) -> str | None:
    normalized = str(duration or "").strip().upper()
    days = _duration_days(normalized)
    if precision == "1minute":
        return "1d" if days is not None and days <= 1 else None
    if precision in {"5minute", "10minute", "15minute", "30minute"}:
        return "5d" if days is not None and days <= 5 else None
    if precision == "1day":
        return _daily_cloud_range(normalized)
    return None


def _duration_days(duration: str) -> int | None:
    parts = duration.split()
    if len(parts) != 2:
        return None
    amount = int(float(parts[0]))
    unit = parts[1]
    if unit.startswith("D"):
        return amount
    if unit.startswith("W"):
        return amount * 7
    if unit.startswith("M"):
        return amount * 31
    if unit.startswith("Y"):
        return amount * 365
    return None


def _daily_cloud_range(duration: str) -> str:
    parts = duration.split()
    if len(parts) != 2:
        return "1m"
    amount = int(float(parts[0]))
    unit = parts[1]
    if unit.startswith("D") or unit.startswith("W") or (unit.startswith("M") and amount <= 1):
        return "1m"
    if unit.startswith("M") and amount <= 3:
        return "3m"
    if unit.startswith("M") and amount <= 6:
        return "6m"
    return "1y"


def _extract_cloud_records(payload: Any, symbol: str) -> list[dict[str, Any]]:
    normalized_symbol = symbol.upper()
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if not isinstance(payload, dict):
        return []

    for key in (normalized_symbol, normalized_symbol.lower(), "bars", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [record for record in value if isinstance(record, dict)]
        if isinstance(value, dict):
            nested = _extract_cloud_records(value, normalized_symbol)
            if nested:
                return nested

    for value in payload.values():
        if isinstance(value, (dict, list)):
            nested = _extract_cloud_records(value, normalized_symbol)
            if nested:
                return nested
    return []
