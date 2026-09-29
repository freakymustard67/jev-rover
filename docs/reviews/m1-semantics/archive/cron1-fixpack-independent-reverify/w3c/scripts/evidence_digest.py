"""Print the test-fixture measurement + a few derived ratios (evidence)."""
import json
from pathlib import Path

W3C = Path("/home/freakymustard/.hermes/cache/scratch/wave3/w3c")
r = json.loads((W3C / "measure_results.json").read_text())
tf = r["test_fixture_10_objects"]
print("test fixture (test_semantics_schema.py:89-99), 10 objects:")
print("  test_bytes:", tf["test_bytes"], " compact:", tf["compact_bytes"])
print("  per-object:", round(len(json.dumps({'objects': tf['state']['observed']['semantics']['objects']})) / 10, 1), "B")
print("  diff:", json.dumps(tf["state"]["observed"]["semantics"]["diff"]),
      "destination:", json.dumps(tf["state"]["observed"]["semantics"]["destination"]))
real10 = r["runs"]["10"]
print("realistic 10:", real10["test_bytes"], "B  -> ratio real/test-fixture:",
      round(real10["test_bytes"] / tf["test_bytes"], 3))
print("cheapest realistic 10:", r["cheap_10_single_pass_no_dest"])
print("build_state ms 10/20 objects:", r["build_state_ms_10_objects"], r["build_state_ms_20_objects"])
print("baseline:", r["baseline_no_semantics"])
print("per-object mean (realistic, 20):", round(r["per_object_mean_test_bytes"], 1), "B")
# what the bound test would report if it used the realistic map
print("\nIf the bound test used the realistic map:")
for n in ("10", "15", "20"):
    tb = r["runs"][n]["test_bytes"]
    print(f"  {n} objects: {tb} B -> {'FAIL' if tb >= 6000 else 'pass'} ({tb-6000:+d} B)")
print("  cheapest 10-object single pass:", r["cheap_10_single_pass_no_dest"]["test_bytes"], "B -> FAIL")
