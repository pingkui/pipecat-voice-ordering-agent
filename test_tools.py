#!/usr/bin/env python3
"""Checks for the small helper tools. No model, no network."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from latency_report import percentile, summarise  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name)


vals = [0.8, 0.9, 1.1, 1.2, 2.5]
s = summarise(vals)
check("median of five values", s["median"] == 1.1)
check("nearest-rank p90 of five values is the largest", s["p90"] == 2.5)
check("share under 1 s", abs(s["share_under_1s"] - 0.4) < 1e-9)
check("share under 2 s", abs(s["share_under_2s"] - 0.8) < 1e-9)
check("percentile on one value", percentile([3.0], 95) == 3.0)
hundred = [i / 100 for i in range(1, 101)]
check("p90 of 1..100 is the 90th value", percentile(hundred, 90) == 0.9)
try:
    percentile([], 90)
    check("empty input is an error", False)
except ValueError:
    check("empty input is an error", True)
print(f"\n{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)
