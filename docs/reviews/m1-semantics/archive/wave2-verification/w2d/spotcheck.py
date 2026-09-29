"""Read-only spot checks of semantics.py claims (wave-2 meta-review)."""
import json
import sys
from dataclasses import fields

sys.path.insert(0, "/home/freakymustard/jev-rover")
import numpy as np

from config import SemanticsConfig
from semantics import _tokens, approach_point, rank_candidates
from scene import Destination, SemanticObject
from perception import Homography

cfg = SemanticsConfig()
print("min_label_score:", cfg.min_label_score, "probe_px:", cfg.project.probe_px)

def mk(oid, label, x, y):
    names = {f.name for f in fields(SemanticObject)}
    kw = dict(id=oid, label=label, x=x, y=y, confidence=0.9, height_suspect=False,
              last_seen_s=1.0, first_seen_s=0.0, sources=[], motion="static")
    return SemanticObject(**{k: v for k, v in kw.items() if k in names})

objs = [mk("obj_0001", "blue mat", 1.0, 1.0),
        mk("obj_0002", "plastic water bottle", 2.0, 2.0),
        mk("obj_0003", "yoga mat extra large", 3.0, 2.0)]

for q in ["go to the blue mat", "find the water bottle", "bottle", "mat",
          "chargr", "waterbottle", "boxes", "box"]:
    r = rank_candidates(q, objs, cfg)
    print(f"query {q!r:26} -> {[(c.obj.label, round(c.score, 3)) for c in r]}")

print("--- raw Jaccard pairs ---")
for a, b in [("bottle", "plastic water bottle"), ("mat", "yoga mat extra large"),
             ("blue mat", "blue mat large"), ("box", "boxes"), ("mat", "blue mat")]:
    q, w = _tokens(a), _tokens(b)
    print(f"{a!r:12} vs {b!r:26} tokens {sorted(q)} {sorted(w)} "
          f"jaccard={round(len(q & w) / len(q | w), 3) if q | w else 0}")

print("--- approach_point degeneracy ---")
d = Destination(label="x", x=3.0, y=1.0, confidence=1.0, source="vision", object_id=None)
print("from (3.2,1.0) standoff 0.35:", approach_point(d, (3.2, 1.0), 0.35))
print("from (0.0,1.0) standoff 0.35:", approach_point(d, (0.0, 1.0), 0.35))

print("--- polygon inset (room.example.json) ---")
c = json.load(open("/home/freakymustard/jev-rover/config/room.example.json"))
poly = c["floor"]["polygon_px"]
print("camera:", c["camera"]["width"], c["camera"]["height"], "room:",
      c["width_m"], c["height_m"], "semantics.enabled:", c.get("semantics", {}).get("enabled"))
arr = np.asarray(poly, float)
print("poly px x-range:", arr[:, 0].min(), arr[:, 0].max(),
      "y-range:", arr[:, 1].min(), arr[:, 1].max())
H = Homography(c["homography"]["image_points_px"], c["homography"]["world_points_m"])
wpts = H.img_to_world(arr)
print("poly world:", np.round(wpts, 3).tolist())
print("min wall clearance m: x", round(float(wpts[:, 0].min()), 3),
      "->", round(float(c["width_m"] - wpts[:, 0].max()), 3),
      "y", round(float(wpts[:, 1].min()), 3),
      "->", round(float(c["height_m"] - wpts[:, 1].max()), 3))

print("--- config semantics sections in shipped configs ---")
for name in ("room.example.json", "room.synthetic.json"):
    try:
        cc = json.load(open(f"/home/freakymustard/jev-rover/config/{name}"))
        print(name, "semantics:", cc.get("semantics", "ABSENT"), "| destination:",
              cc.get("destination", "ABSENT"))
    except FileNotFoundError:
        print(name, "missing")
