"""W3B independent check #2b: eviction at the DEFAULT cap (run.py uses defaults)."""
import os
import sys

ROOT = os.environ.get("W3B_CLONE", os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = os.environ["W3B_OUT"]

from config import RoomConfig
from scene import SemanticObject
from semantics import SemanticStore

cfg = RoomConfig.load("config/room.synthetic.json")
cfg.semantics.store_dir = os.path.join(OUT, "store_check2b")
cap = getattr(cfg.semantics, "max_misses", None)
print(f"OBS default max_misses={cap}")

st = SemanticStore(cfg.semantics, "w3b2c")
st.merge([SemanticObject(id="", label="blue mat", x=3.0, y=1.2, confidence=0.9)], 0, "fake", 0.0)
for t in range(1, 11):
    st.merge([], 0, "fake", float(t))
    print(f"OBS misses={t} alive={'obj_0001' in st.objs}")