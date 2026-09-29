"""Reproduce the exact numbers of tests/test_semantics_integration.py::
test_state_size_bound_on_the_real_path (one pass, 20 fixtures) and dump the
payloads. Deterministic; writes only under the evidence dir.

Usage:
    PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
        probe_bound.py [--root CLONE]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent

ROOM_ROSTER_20 = [
    ("blue mat", 2.0, 1.0, 1.0, 0.8, 0.88),
    ("plastic water bottle", 1.2, 2.6, 0.08, 0.25, 0.90),
    ("wooden box", 3.4, 2.2, 0.35, 0.35, 0.83),
    ("charger", 4.0, 0.7, 0.06, 0.10, 0.72),
    ("cardboard box", 5.2, 2.4, 0.45, 0.40, 0.86),
    ("coffee mug", 0.8, 1.8, 0.10, 0.12, 0.77),
    ("black backpack", 2.7, 3.0, 0.35, 0.45, 0.90),
    ("white cable", 3.0, 0.6, 0.50, 0.05, 0.61),
    ("red box", 4.4, 1.0, 0.30, 0.30, 0.85),
    ("green ball", 1.5, 0.8, 0.15, 0.15, 0.80),
    ("running shoe", 5.7, 1.4, 0.30, 0.12, 0.68),
    ("paper stack", 2.4, 2.0, 0.25, 0.20, 0.71),
    ("phone", 3.8, 3.1, 0.08, 0.16, 0.66),
    ("tissue box", 5.0, 0.5, 0.25, 0.12, 0.74),
    ("yoga mat", 0.6, 2.9, 0.70, 0.40, 0.89),
    ("plastic crate", 4.8, 3.2, 0.40, 0.35, 0.81),
    ("power strip", 1.0, 0.5, 0.30, 0.08, 0.70),
    ("plant pot", 5.9, 2.9, 0.20, 0.20, 0.76),
    ("dog bowl", 2.1, 3.2, 0.20, 0.09, 0.42),
    ("wooden box", 3.4, 0.9, 0.30, 0.30, 0.79),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/freakymustard/.hermes/cache/scratch/i5/repo")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))

    from dataclasses import replace

    from config import RoomConfig
    from perception import Perception
    from semantics import FakeVision, SemanticsRunner, WorldFixture
    from synthetic import SyntheticRoom
    from tactics import build_state

    cfg = RoomConfig.load(root / "config" / "room.synthetic.json")
    cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=str(EVIDENCE / "store_probe")))

    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    for i in range(6):
        frame = syn.render()
        perc.process(frame, i / 15.0)
    scene = perc.process(frame, 1.0)

    vision = FakeVision.from_world([WorldFixture(*e) for e in ROOM_ROSTER_20], perc.homography)
    runner = SemanticsRunner(cfg, "probe", vision)
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
        deadline, res = time.time() + 5.0, None
        while time.time() < deadline and res is None:
            res = runner.poll(1.001)
            if res is None:
                time.sleep(0.01)
        assert res is not None
        scene.semantics = runner.snapshot(1.5)
    finally:
        runner.close()

    state = build_state(scene)
    disk = scene.to_dict()
    digest_bytes = len(json.dumps(state))
    disk_bytes = len(json.dumps(disk))
    out = {
        "root": str(root), "map_objects": len(res.objects),
        "digest_objects": len(state["observed"]["semantics"]["objects"]),
        "digest_bytes": digest_bytes, "digest_under_6000": digest_bytes < 6000,
        "disk_bytes": disk_bytes, "disk_under_10000": disk_bytes < 10_000,
        "digest_diff": state["observed"]["semantics"]["diff"],
    }
    print(json.dumps(out, indent=1))
    (EVIDENCE / "states" / "test_path_20.json").write_text(json.dumps(state))
    (EVIDENCE / "states" / "test_path_20_disk.json").write_text(json.dumps(disk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
