"""Small OKX REST client for public candles and future private order work."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from src.crypto.config import CRYPTO_SETTINGS


OKX_BAR_BY_TIMEFRAME = {
    # Use UTC-anchored daily candles so the active 24/7 crypto day displays
    # as today's date in the dashboard. OKX's plain "1D" candle is anchored
    # to UTC+8, which appears as the previous UTC/US calendar date.
    "daily": "1Dutc",
    "intraday_4h": "4H",
    "intraday_1h": "1H",
    "intraday_5m": "5m",
}


@dataclass(frozen=True)
class OKXInstrument:
    inst_id: str
    inst_type: str
    base_ccy: str = ""
    quote_ccy: str = ""
    state: str = ""


class OKXClient:
    def __init__(self, base_url: str | None = None, timeout: int | None = None) -> None:
        self.base_url = (base_url or CRYPTO_SETTINGS.okx_base_url).rstrip("/")
        self.timeout = timeout or CRYPTO_SETTINGS.request_timeout_seconds

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None, private: bool = False) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        payload = json.dumps(body or {}, separators=(",", ":")) if body else ""
        if private:
            headers.update(self._auth_headers(method, path, payload))
        response = requests.request(method, url, params=params, data=payload or None, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if str(data.get("code", "0")) != "0":
            raise RuntimeError(f"OKX error code={data.get('code')} msg={data.get('msg')}")
        return data

    def _auth_headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        if not (CRYPTO_SETTINGS.okx_api_key and CRYPTO_SETTINGS.okx_api_secret and CRYPTO_SETTINGS.okx_api_passphrase):
            raise RuntimeError("OKX private credentials are not configured.")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        digest = hmac.new(CRYPTO_SETTINGS.okx_api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return {
            "OK-ACCESS-KEY": CRYPTO_SETTINGS.okx_api_key,
            "OK-ACCESS-SIGN": base64.b64encode(digest).decode(),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": CRYPTO_SETTINGS.okx_api_passphrase,
            "x-simulated-trading": "1" if CRYPTO_SETTINGS.okx_simulated_trading else "0",
        }

    def instruments(self, inst_type: str = "SPOT") -> List[OKXInstrument]:
        data = self._request("GET", "/api/v5/public/instruments", params={"instType": inst_type.upper()})
        rows = data.get("data", [])
        return [
            OKXInstrument(
                inst_id=str(row.get("instId", "")),
                inst_type=str(row.get("instType", inst_type)).upper(),
                base_ccy=str(row.get("baseCcy", "")),
                quote_ccy=str(row.get("quoteCcy", "")),
                state=str(row.get("state", "")),
            )
            for row in rows
            if isinstance(row, dict) and row.get("instId")
        ]

    def candles(self, inst_id: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        bar = OKX_BAR_BY_TIMEFRAME.get(timeframe, timeframe)
        data = self._request(
            "GET",
            "/api/v5/market/candles",
            params={"instId": inst_id, "bar": bar, "limit": str(limit or CRYPTO_SETTINGS.candle_limit)},
        )
        rows = data.get("data", [])
        parsed = []
        # OKX: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            parsed.append({
                "date": pd.to_datetime(int(row[0]), unit="ms", utc=True),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        if not parsed:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(parsed).sort_values("date").reset_index(drop=True)
