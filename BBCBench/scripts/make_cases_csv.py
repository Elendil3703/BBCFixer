#!/usr/bin/env python3
"""Regenerates cases.csv from cases/*/meta.json."""
import csv, glob, json, os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = []
for p in sorted(glob.glob(f"{root}/cases/*/meta.json")):
    m = json.load(open(p))
    rows.append({
        "id": m["id"], "ecosystem": m["ecosystem"], "harness": m["harness"],
        "downstream": m["downstream"]["repo"], "library": m["lib"], "v_old": m["v_old"], "v_new": m["v_new"],
        "reference_fix_source": m["reference_fix_source"], "root_api": m.get("root_api", ""),
        "image": m["runtime"]["image"],
    })
rows.sort(key=lambda r: (r["ecosystem"], r["id"].lower()))
with open(f"{root}/cases.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(len(rows), "rows")
