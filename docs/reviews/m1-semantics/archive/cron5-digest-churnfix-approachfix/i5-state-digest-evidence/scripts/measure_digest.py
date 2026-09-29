"""Measure tactics.build_state() bytes through the REAL semantic pipeline,
before vs after the Jev digest (SemanticMap.jev_digest, prototype).

Deterministic and re-runnable: every run rebuilds the same maps through the same
code path (SyntheticRoom -> Perception -> FakeVision.from_world -> SemanticsRunner
-> merge -> snapshot -> build_state). The store dir is scratch, never the repo.

Usage (from anywhere):
    PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
        measure_digest.py [--root CLONE] [--store DIR] [--out results.json]

Defaults point at the i5 scratch clone (patched). Point --root at a pristine
bec1d91 clone to reproduce the pre-digest numbers with the real old build_state
(the script then reports pre == post and asserts the reconstruction formula).

Reconstruction of "pre": the old build_state body was
    observed = scene.to_dict(); mission = observed.pop("mission", ...)
    return {"robot": ROBOT, "mission": mission, "observed": observed}
so pre bytes are computed exactly, without needing the old code. On a pristine
clone the script cross-checks that reconstruction against the real build_state.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent

# label, x, y, w, h, score  -- the w3c audit roster, 6.4 x 3.6 m room
ROSTER = [
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
    ("dog bowl", 2.1, 3.2, 0.20, 0.09, 0.42),   # below min_confidence: map keeps it, digest drops it
    ("wooden box", 3.4, 0.9, 0.30, 0.30, 0.79),  # second instance of a label
    ("laptop", 0.9, 3.1, 0.35, 0.25, 0.87),
    ("hardcover book", 2.6, 1.5, 0.20, 0.05, 0.64),
    ("trash bin", 5.5, 3.0, 0.28, 0.30, 0.82),
    ("nintendo switch", 1.9, 1.3, 0.22, 0.04, 0.69),
    ("ceramic plate", 4.2, 2.0, 0.24, 0.03, 0.62),
    ("flashlight", 3.1, 2.7, 0.06, 0.18, 0.58),
    ("umbrella", 5.8, 0.9, 0.90, 0.10, 0.74),
    ("cardboard box", 0.7, 1.2, 0.35, 0.30, 0.80),
    ("extension cord", 2.9, 1.9, 0.60, 0.60, 0.55),
    ("reusable shopping bag", 4.6, 1.7, 0.30, 0.35, 0.72),
]
COUNTS = (8, 9, 10, 15, 20, 30)


def sized(obj) -> int:
    return len(json.dumps(obj))


def fit_and_cross(points: list[tuple[int, int]], limit: int) -> dict:
    """Least-squares bytes(N) = a + b*N; solve for bytes == limit."""
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a = (sy - b * sx) / n
    return {"a_intercept": round(a, 1), "b_per_object": round(b, 1),
            "n_cross_6000": round((limit - a) / b, 2) if b else None,
            "residual_max": round(max(abs(a + b * p[0] - p[1]) for p in points), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/freakymustard/.hermes/cache/scratch/i5/repo")
    ap.add_argument("--store", default=str(EVIDENCE / "store"))
    ap.add_argument("--out", default=str(EVIDENCE / "measure_results_digest.json"))
    ap.add_argument("--states", default=str(EVIDENCE / "states"))
    args = ap.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))

    from config import RoomConfig
    from perception import Perception
    from scene import Scene, SemanticMap
    from semantics import Destination, FakeVision, SemanticsRunner, WorldFixture
    from synthetic import SyntheticRoom
    from tactics import ROBOT, build_state

    has_digest = hasattr(SemanticMap, "jev_digest")
    states_dir = Path(args.states)
    states_dir.mkdir(parents=True, exist_ok=True)
    Path(args.store).mkdir(parents=True, exist_ok=True)

    cfg = RoomConfig.load(root / "config" / "room.synthetic.json")
    cfg.semantics.store_dir = args.store

    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    scene_base = None
    for i in range(8):
        frame = syn.render()
        scene_base = perc.process(frame, i / 15.0)

    # realistic goal/path fields, as GoalManager + Executor would leave them
    g = scene_base.goal
    g.type, g.name, g.x, g.y, g.yaw_deg = "goto", "b", 5.5, 0.9, None
    g.bearing_deg, g.range_m = 12.4, 4.71
    g.path_valid, g.path_through_unknown = True, False
    g.path_bearing_deg, g.path_len_m, g.progress_s = 8.0, 4.9, 3.2

    def state_pre(scene: Scene) -> dict:
        """The pre-digest build_state body, exactly (mission popped from to_dict)."""
        observed = scene.to_dict()
        mission = observed.pop("mission", {"mode": "goto"})
        return {"robot": ROBOT, "mission": mission, "observed": observed}

    def wait(runner, t, timeout=5.0):
        deadline = time.time() + timeout
        res = None
        while time.time() < deadline and res is None:
            res = runner.poll(t + 0.001)
            if res is None:
                time.sleep(0.01)
        return res

    def measure(n_objects: int) -> dict:
        fixtures = [WorldFixture(*e) for e in ROSTER[:n_objects]]
        vision = FakeVision.from_world(fixtures, perc.homography)
        runner = SemanticsRunner(cfg, "i5", vision)
        try:
            assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
            assert wait(runner, 1.0) is not None, "pass 1 failed"
            jitter = [(lbl, x + 0.02, y + 0.015, w, h, s) for lbl, x, y, w, h, s in ROSTER[:n_objects]]
            jitter[0] = (jitter[0][0], jitter[0][1] + 0.40, jitter[0][2], jitter[0][3],
                         jitter[0][4], jitter[0][5])
            vision.fixtures = FakeVision.from_world(
                [WorldFixture(*e) for e in jitter], perc.homography).fixtures
            assert runner.maybe_pass(5.0, perc.semantic_context(5.0), frame, force=True)
            assert wait(runner, 5.0) is not None, "pass 2 failed"
            snap = runner.snapshot(6.0)
            runner.set_destination(Destination(label=snap.objects[0].label, x=snap.objects[0].x,
                                               y=snap.objects[0].y,
                                               confidence=snap.objects[0].confidence,
                                               source="vision", object_id=snap.objects[0].id))
            snap = runner.snapshot(6.0)
        finally:
            runner.close()

        scene = scene_base
        scene.t = 6.0
        scene.semantics = snap
        scene.sweep = None

        pre_state = state_pre(scene)
        pre = sized(pre_state)
        real = sized(build_state(scene))           # old build_state on pristine; digest on patched
        if has_digest:
            post_state, post = build_state(scene), real
        else:
            assert real == pre, "reconstruction must match the real old build_state"
            post_state, post = pre_state, pre
        digest_view = post_state["observed"]["semantics"]
        (states_dir / f"post_{n_objects}.json").write_text(json.dumps(post_state))
        return {
            "n_fixtures": n_objects,
            "map_objects": len(snap.objects),
            "digest_objects": len(digest_view["objects"]),
            "objects_more": digest_view.get("objects_more", 0),
            "pre_bytes": pre,
            "build_state_bytes": real,
            "post_bytes": post,
            "saved_bytes": pre - post,
            "saved_pct": round(100.0 * (pre - post) / pre, 1),
            "post_objects_block_bytes": sized({"objects": digest_view["objects"]}),
            "post_diff_bytes": sized({"diff": digest_view["diff"]}),
            "post_destination_bytes": sized({"destination": digest_view["destination"]}),
            "post_scalars_bytes": sized({k: v for k, v in digest_view.items()
                                         if k not in ("objects", "diff", "destination",
                                                      "objects_more")}),
        }

    results = {"root": str(root), "has_digest": has_digest,
               "state_note": "all sizes = len(json.dumps(state)); default separators",
               "runs": []}
    for n in COUNTS:
        r = measure(n)
        results["runs"].append(r)
        print(json.dumps(r))

    pts_pre = [(r["n_fixtures"], r["pre_bytes"]) for r in results["runs"]]
    pts_post = [(r["n_fixtures"], r["post_bytes"]) for r in results["runs"]]
    results["fit_pre"] = fit_and_cross(pts_pre, 6000)
    results["fit_post"] = fit_and_cross(pts_post, 6000)
    results["bound_check_20"] = {
        "post_bytes": next(r["post_bytes"] for r in results["runs"] if r["n_fixtures"] == 20),
        "under_6000": next(r["post_bytes"] for r in results["runs"]
                           if r["n_fixtures"] == 20) < 6000,
    }
    Path(args.out).write_text(json.dumps(results, indent=1))
    print("\n== summary ==")
    print(json.dumps({k: v for k, v in results.items() if k != "runs"}, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
