"""Projected post-compaction sizes and crossing points (all inputs measured)."""
import json
from pathlib import Path

W3C = Path("/home/freakymustard/.hermes/cache/scratch/wave3/w3c")
res = json.loads((W3C / "measure_results.json").read_text())
BOUND = 6000


def b(o) -> int:
    return len(json.dumps(o))


BASE = res["baseline_no_semantics"]["test_bytes"]          # 3566: scene + robot + mission, no semantics
runs = res["runs"]
sem20 = json.loads((W3C / "states" / "real_20.json").read_text())["observed"]["semantics"]
objs = sem20["objects"]
n = len(objs)
objs_bytes = b({"objects": objs})
scal = b({k: v for k, v in sem20.items() if k not in ("objects", "diff", "destination")})
diff = b({"diff": sem20["diff"]})
dest = b({"destination": sem20["destination"]})

print(f"fixed floor (no semantics)                 : {BASE} B")
print(f"semantics at n={n}: objects {objs_bytes} + scalars {scal} + diff {diff} + dest {dest}"
      f" = {objs_bytes+scal+diff+dest} B")
print(f"measured full state n={n}                   : {runs['20']['test_bytes']} B")

boiler = sum(b({k: objs[0][k]}) for k in
             ("plane_assumed", "sources", "height_suspect", "motion", "first_seen_s",
              "last_seen_s", "id")) * n
print(f"per-object boilerplate (7 fields) x {n}     : {boiler} B "
      f"({100*boiler/objs_bytes:.0f}% of the objects block)")
lab = sum(b({"label": o["label"]}) for o in objs)
pos = sum(b({"x": o["x"]}) + b({"y": o["y"]}) for o in objs)
print(f"labels {lab} B ({100*lab/objs_bytes:.0f}%), positions {pos} B ({100*pos/objs_bytes:.0f}%)")

# option R1: keep full schema, drop/omit the boilerplate fields, round, int timestamps
drop_ids_ts = sum(b({k: o[k]}) for o in objs for k in ("id", "first_seen_s", "last_seen_s"))
drop_planes = sum(b({k: o[k]}) for o in objs for k in ("plane_assumed", "sources"))
omit_motion = sum(b({"motion": o["motion"]}) for o in objs if o["motion"] == "static")
omit_hs = sum(b({"height_suspect": o["height_suspect"]}) for o in objs if not o["height_suspect"])
rounding = sum(b({"x": o["x"]}) + b({"y": o["y"]}) - b({"x": round(o["x"], 2)})
               - b({"y": round(o["y"], 2)}) for o in objs)
r1_objects = objs_bytes - drop_ids_ts - drop_planes - omit_motion - omit_hs - rounding
print(f"\nR1 (keep schema, omit constant fields + round) saves {objs_bytes - r1_objects} B "
      f"-> objects {r1_objects} B")
print(f"   state {BASE + r1_objects + scal + diff + dest} B at n={n}")

variants = {"digest B label,x,y 2dp": [{"label": o["label"], "x": round(o["x"], 2),
                                        "y": round(o["y"], 2)} for o in objs],
            "digest C +confidence": [{"label": o["label"], "x": round(o["x"], 2),
                                      "y": round(o["y"], 2), "confidence": o["confidence"]}
                                     for o in objs],
            "digest D list [label,x,y]": [[o["label"], round(o["x"], 2), round(o["y"], 2)]
                                          for o in objs]}

for name, objs_new in variants.items():
    blocks = b({"objects": objs_new})
    committed = BASE + blocks + scal + diff + dest
    per_obj = blocks / n
    cross = (BOUND - (BASE + scal + diff + dest)) / per_obj
    print(f"{name:>26}: {blocks} B ({per_obj:.1f} B/obj) -> state {committed} B at n={n}; "
          f"bound crossing {cross:.1f} objects")

# token impact, anchored at 2.53 B/token (historical live runs) and char/4
def t(nb, ratio):
    return nb / ratio

for label, size in (("current full form, n=20", runs["20"]["test_bytes"]),
                    ("R2 digest C, n=20", BASE + b({"objects": variants["digest C +confidence"]})
                     + scal + diff + dest),
                    ("baseline no semantics", BASE)):
    print(f"tokens state-only, {label:>24}: {size} B -> char/4 {t(size,4):.0f} tok | "
          f"anchored {t(size,2.53):.0f} tok")
calls_30s = (5, 60)
cur, new = runs["20"]["test_bytes"], (BASE + b({"objects": variants["digest C +confidence"]})
                                      + scal + diff + dest)
print(f"\n30 s run with a 20-object map, 5-60 calls: current {t(cur,2.53)*5/1000:.0f}-"
      f"{t(cur,2.53)*60/1000:.0f}k tok vs digest {t(new,2.53)*5/1000:.0f}-{t(new,2.53)*60/1000:.0f}k tok")
print(f"rubric overhead {res['fixed_prompt_overhead']['questions_test_bytes']} B = "
      f"{t(res['fixed_prompt_overhead']['questions_test_bytes'],2.53):.0f} anchored tok per call "
      f"(constant, paid on every call)")
print(f"\n64-object worst case (MAX_DETECTIONS, extrapolated from fit): "
      f"{round(3812.7 + 229.9*64)} B = {t(round(3812.7 + 229.9*64),2.53):.0f} anchored tok state-only; "
      f"64 x digestC(67.8 B) + {BASE+scal+diff+dest} B floor = "
      f"{round(64*67.8 + BASE + scal + diff + dest)} B")
