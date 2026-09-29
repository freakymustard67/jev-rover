"""Build the W3B enabled-semantics config (semantics.enabled=true, fast audit cadence)."""
import json
import pathlib

src = json.loads(pathlib.Path("clone/config/room.synthetic.json").read_text())
src["semantics"]["enabled"] = True
src["semantics"]["audit_period_s"] = 1.5
src["semantics"]["min_interval_s"] = 0.5
src["semantics"]["max_passes_per_min"] = 60
out = pathlib.Path("out/enabled.json")
out.write_text(json.dumps(src, indent=2))
print("wrote", out, "enabled=", src["semantics"]["enabled"],
      "audit_period_s=", src["semantics"]["audit_period_s"])
