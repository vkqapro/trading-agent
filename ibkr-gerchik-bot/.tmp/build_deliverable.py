import json
from collections import Counter
raw = json.load(open(r"C:/Users/Vitaly/Documents/GitHub/trading-agent/ibkr-gerchik-bot/.tmp/okx_paper_pairs_raw.json", encoding="utf-8"))
out = {
    "source": "OKX API v5 /public/instruments with x-simulated-trading:1 (paper/demo)",
    "base_url": raw["base_url"],
    "queried_at_utc": raw["queried_at_utc"],
    "demo_account_verified": raw["demo_account_check"],
    "counts": {"spot": len(raw["paper_spot_instruments"]), "swap": len(raw["paper_swap_instruments"]),
               "spot_live": raw["paper_spot_live_count"], "swap_live": raw["paper_swap_live_count"]},
    "quote_distribution_spot": dict(Counter(r["quote_ccy"] for r in raw["paper_spot_instruments"])),
    "bot_configured_pairs": raw["configured_symbols_validated"],
    "spot_instruments": raw["paper_spot_instruments"],
    "swap_instruments": raw["paper_swap_instruments"],
}
p = r"C:/Users/Vitaly/Documents/GitHub/trading-agent/ibkr-gerchik-bot/docs/okx-paper-crypto-pairs.json"
open(p, "w", encoding="utf-8").write(json.dumps(out, indent=2))
print("wrote", p, "spot", out["counts"]["spot"], "swap", out["counts"]["swap"])
