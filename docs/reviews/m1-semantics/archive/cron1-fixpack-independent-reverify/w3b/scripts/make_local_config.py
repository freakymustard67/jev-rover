"""W3B: build a config whose vision kind is 'local' (M2-only) to check that
--semantics fake forces the fixture adapter."""
import json
import pathlib

src = json.loads(pathlib.Path("clone/config/room.synthetic.json").read_text())
src["semantics"]["enabled"] = False
src["semantics"]["model"]["kind"] = "local"
out = pathlib.Path("out/local.json")
out.write_text(json.dumps(src, indent=2))
print("wrote", out, "kind=local enabled=False")
