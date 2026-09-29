"""Spot-check report figures."""
import json
ids = [f"obj_{i:04d}" for i in range(1, 21)]
d20 = {"diff": {"appeared": ids, "moved": [], "vanished": []}}
print("diff with 20 appeared ids:", len(json.dumps(d20)), "B")
d1 = {"diff": {"appeared": ["obj_0001"], "moved": [], "vanished": []}}
print("diff with 1 appeared id  :", len(json.dumps(d1)), "B")
print("one-off cost of a 20-id appeared list vs 1-id:", len(json.dumps(d20)) - len(json.dumps(d1)))
print("component sum check:", 716 + 65 + 2751 + 34 + 4566 + 54 + 65 + 151)
print("rubrics/baseline:", round(5394 / 3566, 2), " rubrics/20obj-state:", round(5394 / 8397, 2))
print("digest C savings per call: bytes", 3210, "-> char/4 tok", round(3210/4, 1),
      "| 2.53 B/tok", round(3210/2.53, 1), "| x60 calls", round(3210/4*60/1000, 1), "-",
      round(3210/2.53*60/1000, 1), "k")
print("10-obj semantics delta vs baseline:", 6125 - 3566, "B")
