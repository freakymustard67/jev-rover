"""Digest/compaction savings estimates, computed from the measured state files."""
from __future__ import annotations

import json
from pathlib import Path

W3C = Path("/home/freakymustard/.hermes/cache/scratch/wave3/w3c")
res = json.loads((W3C / "measure_results.json").read_text())


def b(o) -> int:
    return len(json.dumps(o))


def cb(o) -> int:
    from pydantic_core import to_json
    return len(to_json(o))


def tok(nbytes: int, ratio: float = 4.0) -> float:
    return nbytes / ratio


print("== measured runs (test convention / compact) ==")
for k, v in res["runs"].items():
    print(f"{k:>12}: {v['test_bytes']:>6} B test / {v['compact_bytes']:>6} B compact "
          f"({v['objects_in_map']} objects)")

sem20 = json.loads((W3C / "states" / "real_20.json").read_text())["observed"]["semantics"]
objs = sem20["objects"]
n = len(objs)
print(f"\n== digest variants, n={n} (objects block currently {b({'objects': objs})} B test convention) ==")

variants = {
    "A label+x+y": [{"label": o["label"], "x": o["x"], "y": o["y"]} for o in objs],
    "B label+x+y (2dp)": [{"label": o["label"], "x": round(o["x"], 2),
                           "y": round(o["y"], 2)} for o in objs],
    "C label+x+y+conf": [{"label": o["label"], "x": round(o["x"], 2), "y": round(o["y"], 2),
                          "confidence": o["confidence"]} for o in objs],
    "D list form [label,x,y]": [[o["label"], round(o["x"], 2), round(o["y"], 2)] for o in objs],
    "E label-only": [o["label"] for o in objs],
}
cur = b({"objects": objs})
for name, v in variants.items():
    size = b({"objects": v})
    print(f"{name:>24}: {size:>5} B test / {cb({'objects': v}):>5} B compact "
          f"-> saves {cur - size:>5} B ({100*(cur-size)/cur:.0f}%)  ~{(cur-size)/4:.0f} tok(char/4)")

print("\n== itemised savings (test convention, n=20) ==")
dropped_keys = ("plane_assumed", "sources", "height_suspect", "motion", "first_seen_s",
                "last_seen_s", "id")
for k in dropped_keys:
    save = sum(b({k: o[k]}) for o in objs)
    print(f"drop per-object field '{k}': {save} B")
save_round = 0
for o in objs:
    save_round += b({"x": o["x"]}) + b({"y": o["y"]}) - (
        b({"x": round(o["x"], 2)}) + b({"y": round(o["y"], 2)}))
print(f"round x,y to 2 dp: {save_round} B")
save_ts = sum(b({"first_seen_s": o["first_seen_s"]}) + b({"last_seen_s": o["last_seen_s"]})
              - b({"first_seen_s": int(o["first_seen_s"])})
              - b({"last_seen_s": int(o["last_seen_s"])}) for o in objs)
print(f"timestamps -> integer seconds: {save_ts} B")

diff = sem20["diff"]
print(f"full diff block: {b({'diff': diff})} B; appeared has {len(diff['appeared'])} ids "
      f"({b({'appeared': diff['appeared']})} B)")
one_line = {"diff": {"n_appeared": len(diff["appeared"]), "n_moved": len(diff["moved"]),
                     "n_vanished": len(diff["vanished"])}}
print(f"counts-only diff: {b(one_line)} B -> saves {b({'diff': diff}) - b(one_line)} B")
capped = {"diff": {"appeared": diff["appeared"][:3], "moved": diff["moved"][:3],
                   "vanished": diff["vanished"][:3]}}
print(f"capped-3 diff: {b(capped)} B -> saves {b({'diff': diff}) - b(capped)} B")

low = [o for o in objs if o["confidence"] < 0.5]
print(f"objects below min_confidence 0.5: {len(low)} ({[o['label'] for o in low]}) "
      f"-> dropping them saves {sum(b({'o': o}) for o in low)} B")
print(f"destination block: {b({'destination': sem20['destination']})} B")

print("\n== token estimates (state only, 20 objects) ==")
for label, size in (("test convention", res["runs"]["20"]["test_bytes"]),
                    ("compact/wire", res["runs"]["20"]["compact_bytes"])):
    print(f"{label}: {size} B -> char/4 {size/4:.0f} tok | anchored 2.6-2.8 B/tok "
          f"{size/2.8:.0f}-{size/2.6:.0f} tok")

print("\n== full prompt estimate (state + fixed rubrics, 20 objects) ==")
st = res["runs"]["20"]["test_bytes"]
rub = res["fixed_prompt_overhead"]["questions_test_bytes"]
print(f"state {st} + rubrics {rub} + robot/mission already in state = {st + rub} B "
      f"-> char/4 {(st+rub)/4:.0f} tok | 2.7 B/tok {(st+rub)/2.7:.0f} tok")
hist = {"runs": [("summary_20260928-193823", 29, 102392), ("summary_20260928-193922", 29, 102496),
                 ("summary_20260928-194120", 29, 102595), ("summary_20260928-194217", 10, 35370)],
        "baseline_state_test_bytes": res["baseline_no_semantics"]["test_bytes"],
        "rubric_test_bytes": rub}
print("historical live runs (no semantics), tokens per call:")
tot = 0
for name, calls, toks in hist["runs"]:
    print(f"  {name}: {toks}/{calls} = {toks/calls:.0f} tok/call")
    tot += toks / calls
print(f"  mean {tot/len(hist['runs']):.0f} tok/call for a payload of ~"
      f"{hist['baseline_state_test_bytes'] + rub} B -> {((hist['baseline_state_test_bytes']+rub)/(tot/len(hist['runs']))):.2f} B/tok")
