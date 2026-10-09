#!/usr/bin/env python3
"""Summarises latency.jsonl written by bot.py: user-to-bot latency per turn (end of the caller's speech to
the first audio of the reply). Usage: python tools/latency_report.py [latency.jsonl]"""
import json
import math
import statistics
import sys


def percentile(sorted_vals, p):
    """Nearest-rank percentile on an already sorted list."""
    if not sorted_vals:
        raise ValueError("no values")
    k = max(1, math.ceil(p / 100 * len(sorted_vals)))
    return sorted_vals[k - 1]


def summarise(values):
    v = sorted(values)
    return {"turns": len(v), "median": statistics.median(v), "p90": percentile(v, 90),
            "p95": percentile(v, 95), "max": v[-1], "share_under_1s": sum(x < 1.0 for x in v) / len(v),
            "share_under_2s": sum(x < 2.0 for x in v) / len(v)}


def main(path):
    vals = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                vals.append(json.loads(line)["latency_secs"])
    if not vals:
        sys.exit("no latency records yet")
    s = summarise(vals)
    print(f"turns: {s['turns']}")
    print(f"median: {s['median']:.2f}s   p90: {s['p90']:.2f}s   p95: {s['p95']:.2f}s   max: {s['max']:.2f}s")
    print(f"under 1s: {s['share_under_1s']:.0%}   under 2s: {s['share_under_2s']:.0%}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "latency.jsonl")
