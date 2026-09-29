"""W3B independent check #2: eviction/resurrection.

An object unmatched for more than the configured miss cap must be removed, and a
later re-detection must NOT silently resurrect the same id.
"""
import os
import sys
from dataclasses import replace

ROOT = os.environ.get("W3B_CLONE", os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = os.environ["W3B_OUT"]

from config import RoomConfig
from scene import SemanticObject
from semantics import SemanticStore

cfg = RoomConfig.load("config/room.synthetic.json")
cfg.semantics.store_dir = os.path.join(OUT, "store_check2")
cap = getattr(cfg.semantics, "max_misses", None)
print(f"OBS vanish_passes={cfg.semantics.vanish_passes} max_misses={cap}")
if cap is not None:
    cfg.semantics = replace(cfg.semantics, max_misses=3)
    cap = 3
    print("OBS using max_misses=3 for this run (>= vanish_passes=2)")


def det(x=3.0, y=1.2):
    return SemanticObject(id="", label="blue mat", x=x, y=y, confidence=0.9)


# --- part A: miss cap -> eviction, return -> new id ---------------------------
st = SemanticStore(cfg.semantics, "w3b2")
st.merge([det()], 0, "fake", 0.0)
print(f"OBS t=0.0 ids={list(st.objs)}")
for t in (1, 2, 3, 4, 5, 6):
    m = st.merge([], 0, "fake", float(t))
    print(f"OBS t={t}.0 ids={list(st.objs)} misses={dict(st._misses)} "
          f"vanished={m.diff.vanished}")
print(f"OBS alive_after_6_missed_passes={'obj_0001' in st.objs}")
m = st.merge([det()], 0, "fake", 7.0)
print(f"OBS return_ids={[o.id for o in m.objects]} return_appeared={m.diff.appeared} "
      f"return_vanished={m.diff.vanished} return_labels={[o.label for o in m.objects]}")
print(f"OBS return_reused_obj_0001={'obj_0001' in [o.id for o in m.objects]}")

# --- part B: churn growth ----------------------------------------------------
st2 = SemanticStore(cfg.semantics, "w3b2b")
for k in range(6):
    t = k * 12.0
    st2.merge([det(3.0 + 0.01 * k)], 0, "fake", t)
    for j in range(1, 6):
        st2.merge([], 0, "fake", t + j)
print(f"OBS churn_map_size={len(st2.objs)} churn_next_id={st2.next_id}")
print(f"OBS churn_ids={sorted(st2.objs)}")

# --- part C: config validation of the cap --------------------------------------
import json
from config import ConfigError
raw = json.loads(open("config/room.synthetic.json").read())
raw["semantics"]["vanish_passes"] = 5
raw["semantics"]["max_misses"] = 2
try:
    RoomConfig.from_dict(raw, where="badcap")
    print("OBS bad_cap_validation=NO_ERROR")
except ConfigError as e:
    print(f"OBS bad_cap_validation=ConfigError: {e}")

print("EXPECT_AFTER_FIX (max_misses=3, vanish_passes=2): evicted at the 3rd miss, "
      "ids=[] at t=3.0, return creates obj_0002 + appeared=['obj_0002'], reused=False")
print("PRE_PATCH_SYMPTOM: obj_0001 kept forever, return reuses obj_0001 (silent resurrection)")
