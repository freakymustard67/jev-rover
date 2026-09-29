#!/usr/bin/env python3
"""Emit the exact report tables from the harness JSONL logs."""
import glob
import json

rows = []
for f in sorted(glob.glob("v9_logs/v9_loop_scale*.jsonl")):
    for line in open(f):
        rows.append(json.loads(line))

print("### loop runs (markdown)")
print("| scale | secs | offer | L | achieved Hz | max_lag ms | sem_max ms | passes | interval | budget | inflight | no_ctx | pass cadence s | merge times |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    st = r.get("stats", {})
    sk = st.get("skipped", {})
    print(f"| {r['scale']} | {r['seconds']} | {r['offer_mode']} | {r['L']} | {r['achieved_hz']} | "
          f"{r['max_lag_ms']} | {r.get('sem_max_ms')} | {st.get('passes')} | {sk.get('interval')} | "
          f"{sk.get('budget')} | {sk.get('inflight')} | {sk.get('no_context')} | "
          f"{r.get('pass_cadence_s')} | {r['merge_times']} |")

print()
print("### cost probes (raw)")
for f in sorted(glob.glob("v9_logs/v9_costs_scale*.jsonl")) + sorted(glob.glob("v9_logs/v9_merge_iso.jsonl")):
    for line in open(f):
        print(line.strip())
