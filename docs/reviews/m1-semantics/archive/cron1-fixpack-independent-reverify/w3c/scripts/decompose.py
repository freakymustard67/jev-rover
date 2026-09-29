"""Fixture vs real-path decomposition: where the 1792 B gap at 10 objects comes from."""
import json
from pathlib import Path

W3C = Path("/home/freakymustard/.hermes/cache/scratch/wave3/w3c")
res = json.loads((W3C / "measure_results.json").read_text())


def b(o) -> int:
    return len(json.dumps(o))


fx = res["test_fixture_10_objects"]["state"]
rl = json.loads((W3C / "states" / "real_10.json").read_text())
for name, st in (("fixture (Scene(t=5.0))", fx), ("real scene pipeline", rl)):
    obs = st["observed"]
    sem = obs.get("semantics")
    print(f"{name}:")
    print("   total          :", b(st))
    print("   robot+mission  :", b({"robot": st["robot"], "mission": st["mission"]}))
    print("   observed w/o sem:", b({k: v for k, v in obs.items() if k != "semantics"}))
    print("   semantics      :", b({"semantics": sem}))
    print("   objects        :", b({"objects": sem["objects"]}),
          f"({b({'objects': sem['objects']})/len(sem['objects']):.1f} B/object)")
    print("   sem scalars    :", b({k: v for k, v in sem.items()
                                    if k not in ("objects", "diff", "destination")}))
    print("   diff           :", b({"diff": sem["diff"]}), json.dumps(sem["diff"]))
    print("   destination    :", b({"destination": sem["destination"]}))
    print("   label field    :", sum(b({"label": o["label"]}) for o in sem["objects"]),
          f"({sum(len(o['label']) for o in sem['objects'])} chars of label text)")
    print("   x+y fields     :", sum(b({"x": o["x"]}) + b({"y": o["y"]}) for o in sem["objects"]))
print("\nmax length of a float repr in real_20 states:",
      max(len(json.dumps(o[k])) for o in json.loads((W3C / "states" / "real_20.json")
          .read_text())["observed"]["semantics"]["objects"] for k in ("x", "y", "confidence")))
print("MAX_DETECTIONS (semantics.py:49) extrapolation from fit a=3812.7 b=229.9:",
      round(3812.7 + 229.9 * 64), "B at 64 objects (test convention)")
