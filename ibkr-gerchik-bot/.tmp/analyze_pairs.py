import json, re
from collections import Counter
p = r"C:/Users/Vitaly/Documents/GitHub/trading-agent/ibkr-gerchik-bot/.tmp/okx_paper_pairs_raw.json"
d = json.load(open(p, encoding="utf-8"))
spot = d["paper_spot_instruments"]; swap = d["paper_swap_instruments"]
all_ids = [r["inst_id"] for r in spot] + [r["inst_id"] for r in swap]
for base in ("KITE","CC","LUNR","JTO","ZEC","MORPHO","DOGE"):
    hits = sorted(i for i in all_ids if re.match(rf"^{base}-", i))
    print(base, "->", hits[:12], f"({len(hits)} total)")
print("SPOT quote dist:", Counter(r["quote_ccy"] for r in spot).most_common(15))
print("SWAP ids sample:", [r["inst_id"] for r in swap[:8]])
print("SPOT states:", Counter(r["state"] for r in spot))
print("SWAP states:", Counter(r["state"] for r in swap))
print("acct_lv:", d.get("demo_account_acct_lv"))
print("bal:", d["demo_account_check"])
print("validated missing:", [v["okx_inst_id"] for v in d["configured_symbols_validated"] if not v["available_in_paper"]])
