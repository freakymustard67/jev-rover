#!/usr/bin/env python3
"""Aggregate the V9 harness logs into compact per-config tables."""
import glob
import json
import statistics as st

def load(pattern):
    rows = []
    for f in sorted(glob.glob(pattern)):
        for line in open(f):
            rows.append(json.loads(line))
    return rows

print("== loop runs ==")
rows = load("v9_logs/v9_loop_scale*.jsonl")
by = {}
for r in rows:
    by.setdefault((r["scale"], r["L"], r.get("offer_mode")), []).append(r)
for (scale, L, om), rs in sorted(by.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), kv[0][2])):
    hzs = [x["achieved_hz"] for x in rs]
    lag = [x["max_lag_ms"] for x in rs]
    blk = [x["block_p50_ms"] for x in rs]
    bmax = [x["block_max_ms"] for x in rs]
    sem = [x.get("sem_max_ms") for x in rs]
    stats = [x.get("stats", {}) for x in rs]
    passes = [s.get("passes") for s in stats]
    sk = {k: [s.get("skipped", {}).get(k) for s in stats] for k in
          ("interval", "budget", "inflight", "no_context")}
    errs = [s.get("errors") for s in stats]
    stale = [s.get("stale_dropped") for s in stats]
    med = [s.get("median_ms") for s in stats]
    print(f"scale={scale} L={L} offer={om}: n={len(rs)} hz={hzs} median_hz={st.median(hzs):.3f} "
          f"maxlag_ms={lag} block_p50_ms={blk} block_max_ms={bmax} sem_max_ms={sem}")
    print(f"    passes={passes} skipped={sk} errors={errs} stale={stale} median_ms={med}")
    for r in rs:
        print(f"    rep{r['rep']}: offer_times={r['offer_times']} merge_times={r['merge_times']} "
              f"cadence_s={r.get('pass_cadence_s')} ticks={r['ticks']} res={r['res']}")

print("\n== costs ==")
for r in load("v9_logs/v9_costs_scale*.jsonl"):
    print(json.dumps(r, sort_keys=True))

print("\n== stale ==")
for r in load("v9_logs/v9_stale*.jsonl"):
    print(json.dumps(r, sort_keys=True))

print("\n== cooldown ==")
for r in load("v9_logs/v9_cooldown.jsonl"):
    print(json.dumps(r, sort_keys=True))
