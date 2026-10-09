#!/usr/bin/env python3
"""Turns scenario runs written by chat_sim.py (runs/*.json) into a readable markdown file of sample calls,
including the tool calls and the final order state. Usage: python tools/make_sample_calls.py > docs/examples/sample_calls.md"""
import glob
import json
import os

PICK = ["change_of_mind", "unknown_item", "allergy_goes_to_staff", "impatient_then_confirms", "reservation_out_of_hours"]
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest(name):
    files = sorted(glob.glob(os.path.join(HERE, "runs", f"*-{name}-*.json")))
    return json.load(open(files[-1])) if files else None


print("# Sample calls\n")
print("Real runs of the scripted scenarios against a real model through the same prompt, tools and core as the voice bot.")
print("The customer lines are scripted; the agent lines and tool calls are the model's. Text only, no audio.\n")
for name in PICK:
    r = latest(name)
    if not r:
        continue
    print(f"## {name}\n")
    for who, text in r["transcript"]:
        print(f"- **{who}:** {text}")
    print("\nTool calls (in order):\n")
    for tool, args, ok in r["calls"]:
        print(f"- `{tool}({json.dumps(args)})` -> {'ok' if ok else 'refused'}")
    print(f"\nFinal state: `{json.dumps(r['summary'])}`\n")
