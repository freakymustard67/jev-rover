"""Measure real tactics.build_state() byte size/token cost with realistic semantic maps.

Runs entirely inside the scratch clone (/home/freakymustard/.hermes/cache/scratch/wave3/w3c/clone)
plus a scratch store dir; the subject repo is untouched.

Produces measure_results.json in the w3c scratch dir, and one state JSON file per
object count for the tiktoken pass (tok_tokens.py).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

W3C = Path("/home/freakymustard/.hermes/cache/scratch/wave3/w3c")
CLONE = W3C / "clone"
sys.path.insert(0, str(CLONE))

from pydantic_core import to_json  # SDK's wire serializer (typesafe_sdk/_core/json.py)

from config import RoomConfig
from perception import Perception
from scene import SemanticMap, SemanticObject, Scene, SweepConfirmation, SweepState
from semantics import Destination, FakeVision, SemanticsRunner, WorldFixture
from synthetic import SyntheticRoom
from tactics import build_state

STORE = W3C / "store"
STATE_DIR = W3C / "states"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------- realistic roster
# label, x, y, w, h, score  -- spread over the 6.4 x 3.6 m synthetic room
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
    ("dog bowl", 2.1, 3.2, 0.20, 0.09, 0.42),   # below min_confidence: still enters the map
    ("wooden box", 3.4, 0.9, 0.30, 0.30, 0.79),  # second instance of a label
    # extras for the 30-object over-bound probe
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


def sized(obj: dict) -> int:
    return len(json.dumps(obj))


def compact(obj) -> int:
    return len(to_json(obj))


def fit_and_cross(points: list[tuple[int, int]], limit: int) -> dict:
    """Least-squares bytes(N) = a + b*N; solve for bytes == limit."""
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a = (sy - b * sx) / n
    return {"a": a, "b_per_object": b,
            "n_cross": (limit - a) / b if b else None,
            "residual_max": max(abs(a + b * p[0] - p[1]) for p in points)}


def measure(perc, scene_base: Scene, n_objects: int, label: str,
            destination: bool = True, sweep: bool = False) -> dict:
    """Real pipeline: FakeVision -> SemanticsRunner -> store.merge -> snapshot -> build_state."""
    cfg = ROOMCFG
    fixtures = [WorldFixture(lbl, x, y, w, h, s) for lbl, x, y, w, h, s in ROSTER[:n_objects]]
    vision = FakeVision.from_world(fixtures, perc.homography)
    runner = SemanticsRunner(cfg, "measure", vision)
    try:
        # pass 1: everything appears
        ctx = perc.semantic_context(1.0)
        assert runner.maybe_pass(1.0, ctx, FRAME, force=True)
        m1 = _wait(runner, 1.0)
        assert m1 is not None, "pass 1 failed"
        # pass 2: per-object jitter (EMA -> non-round floats) + one real 0.4 m move
        jitter = [(lbl, x + 0.02, y + 0.015, w, h, s) for lbl, x, y, w, h, s in ROSTER[:n_objects]]
        jitter[0] = (jitter[0][0], jitter[0][1] + 0.40, jitter[0][2], jitter[0][3], jitter[0][4],
                     jitter[0][5])
        vision.fixtures = FakeVision.from_world(
            [WorldFixture(*e) for e in jitter], perc.homography).fixtures
        ctx = perc.semantic_context(5.0)
        assert runner.maybe_pass(5.0, ctx, FRAME, force=True)
        m2 = _wait(runner, 5.0)
        assert m2 is not None, "pass 2 failed"

        snap = runner.snapshot(6.0)
        if destination:
            runner.set_destination(Destination(label=snap.objects[0].label, x=snap.objects[0].x,
                                               y=snap.objects[0].y, confidence=snap.objects[0].confidence,
                                               source="vision", object_id=snap.objects[0].id))
            snap = runner.snapshot(6.0)
    finally:
        runner.close()

    scene = scene_base
    scene.t = 6.0
    scene.semantics = snap
    scene.sweep = None
    if sweep:
        scene.sweep = SweepState(age_s=1.1, last_seq=3, pose_sigma=[0.02, 0.02, 1.0],
                                 backend="servo-tof",
                                 confirmations=[SweepConfirmation(object_id=o.id, result="confirmed",
                                                                  range_err_m=0.03)
                                                for o in snap.objects[:4]])
    state = build_state(scene)
    test_bytes = len(json.dumps(state))          # the bound's convention (default separators)
    compact_bytes = compact(state)               # pydantic-core: what the SDK puts on the wire
    out = {
        "label": label,
        "n_objects": n_objects,
        "objects_in_map": len(state["observed"]["semantics"]["objects"]),
        "passes": state["observed"]["semantics"]["passes"],
        "test_bytes": test_bytes,
        "compact_bytes": compact_bytes,
        "sweep_present": bool(sweep),
        "destination_present": destination,
        "state": state,
    }
    return out


def _wait(runner, t, timeout=5.0):
    import time
    deadline = time.time() + timeout
    res = None
    while time.time() < deadline and res is None:
        res = runner.poll(t + 0.001)
        if res is None:
            time.sleep(0.01)
    return res


ROOMCFG = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
ROOMCFG.semantics.store_dir = str(STORE / "store.json")  # never write into the clone


def main() -> int:
    STORE.mkdir(parents=True, exist_ok=True)
    perc = Perception(ROOMCFG)
    syn = SyntheticRoom(ROOMCFG)
    frame = None
    for i in range(8):
        frame = syn.render()
        scene_base = perc.process(frame, i / 15.0)
    global FRAME
    FRAME = frame

    # realistic goal/path fields, as GoalManager + Executor would leave them
    from control import Executor
    g = scene_base.goal
    g.type, g.name, g.x, g.y, g.yaw_deg = "goto", "b", 5.5, 0.9, None
    g.bearing_deg, g.range_m = 12.4, 4.71
    g.path_valid, g.path_through_unknown = True, False
    g.path_bearing_deg, g.path_len_m, g.progress_s = 8.0, 4.9, 3.2

    # baseline: identical scene, no semantics
    scene_base.semantics = None
    scene_base.sweep = None
    base_state = build_state(scene_base)
    results = {
        "baseline_no_semantics": {"test_bytes": len(json.dumps(base_state)),
                                  "compact_bytes": compact(base_state)},
        "files": {},
    }

    # fixed prompt overhead: the question rubrics that ride with every call
    from tactics import MANEUVERS, MISSION, QUESTIONS
    qw = {name: q.model_dump(mode="json") for name, q in QUESTIONS.items()}
    results["fixed_prompt_overhead"] = {
        "questions_test_bytes": sized({"questions": qw}),
        "questions_compact_bytes": len(to_json(qw)),
        "maneuvers_rubric_bytes": sized({"criteria": MANEUVERS}),
        "mission_bytes": sized(MISSION),
    }

    # the test's own fixture, reproduced exactly (test_semantics_schema.py:89-99)
    tscene = Scene(t=5.0)
    tscene.semantics = SemanticMap(
        age_s=0.4, passes=3, model="fake-vision-v0",
        objects=[SemanticObject(id=f"obj_{i:04d}", label=f"thing {i}",
                                x=1.0 + 0.1 * i, y=2.0, confidence=0.9) for i in range(10)])
    tstate = build_state(tscene)
    results["test_fixture_10_objects"] = {
        "test_bytes": len(json.dumps(tstate)), "compact_bytes": compact(tstate),
        "objects": 10, "state": tstate}

    runs = {}
    for n in (8, 9, 10, 15, 20, 30):
        r = measure(perc, scene_base, n, f"realistic_{n}", destination=True, sweep=False)
        (STATE_DIR / f"real_{n}.json").write_text(json.dumps(r["state"]))
        runs[n] = r
    # variants at 20 objects: no destination, populated sweep (M4 preview)
    r_nod = measure(perc, scene_base, 20, "realistic_20_no_dest", destination=False)
    runs[("20,no_dest")] = r_nod
    r_sw = measure(perc, scene_base, 20, "realistic_20_sweep", destination=True, sweep=True)
    runs[("20,sweep")] = r_sw

    results["runs"] = {str(k): {kk: vv for kk, vv in v.items() if kk != "state"}
                       for k, v in runs.items()}

    # crossing fits (test convention and wire convention)
    pts_test = [(n, runs[n]["test_bytes"]) for n in (8, 9, 10, 15, 20, 30)]
    pts_compact = [(n, runs[n]["compact_bytes"]) for n in (8, 9, 10, 15, 20, 30)]
    results["fit_test_convention_6000"] = fit_and_cross(pts_test, 6000)
    results["fit_compact_6000"] = fit_and_cross(pts_compact, 6000)

    # component + per-field breakdown at 20 objects
    st20 = runs[20]["state"]
    sem = st20["observed"]["semantics"]
    results["components_20"] = {
        "full_test_bytes": runs[20]["test_bytes"],
        "robot_block": sized({"robot": st20["robot"]}),
        "mission_block": sized({"mission": st20["mission"]}),
        "observed_without_semantics": sized({k: v for k, v in st20["observed"].items()
                                             if k != "semantics"}),
        "semantics_total": sized({"semantics": sem}),
        "semantics_diff": sized({"diff": sem["diff"]}),
        "semantics_destination": sized({"destination": sem["destination"]}),
        "semantics_objects": sized({"objects": sem["objects"]}),
        "semantics_scalars": sized({k: v for k, v in sem.items()
                                    if k not in ("objects", "diff", "destination")}),
    }
    fc = defaultdict(int)
    fc_compact = defaultdict(int)
    for o in sem["objects"]:
        for k, v in o.items():
            fc[k] += sized({k: v})
            fc_compact[k] += len(to_json({k: v}))
    results["per_field_bytes_20"] = dict(sorted(fc.items(), key=lambda kv: -kv[1]))
    results["per_field_compact_20"] = dict(sorted(fc_compact.items(), key=lambda kv: -kv[1]))
    results["per_object_mean_test_bytes"] = \
        sized({"objects": sem["objects"]}) / len(sem["objects"])

    # single-pass (no EMA, round floats) vs two-pass, at 20 objects
    specs = [(e[0], e[1], e[2], e[3], e[4], e[5]) for e in ROSTER[:20]]
    vision = FakeVision.from_world([WorldFixture(*e) for e in specs], perc.homography)
    single_scene = scene_base
    runner = SemanticsRunner(ROOMCFG, "single", vision)
    try:
        runner.maybe_pass(1.0, perc.semantic_context(1.0), FRAME, force=True)
        _wait(runner, 1.0)
        single_scene.semantics = runner.snapshot(1.5)
    finally:
        runner.close()
    sstate = build_state(single_scene)
    results["single_pass_20"] = {"test_bytes": len(json.dumps(sstate)),
                                 "compact_bytes": compact(sstate)}

    # main-list variant: two passes, sweep variant for M4
    results["sweep_20_surcharge_test_bytes"] = runs[("20,sweep")]["test_bytes"] - runs[20]["test_bytes"]
    results["destination_surcharge_test_bytes"] = runs[20]["test_bytes"] - runs[("20,no_dest")]["test_bytes"]
    results["state_note"] = "all sizes = len(json.dumps(state)); compact = pydantic_core.to_json (SDK wire)"

    # cheapest realistic 10-object case: one pass, no destination, no EMA jitter
    vision = FakeVision.from_world([WorldFixture(*e) for e in ROSTER[:10]], perc.homography)
    runner = SemanticsRunner(ROOMCFG, "cheap10", vision)
    try:
        runner.maybe_pass(1.0, perc.semantic_context(1.0), FRAME, force=True)
        _wait(runner, 1.0)
        scene_base.semantics = runner.snapshot(1.5)
    finally:
        runner.close()
    cheap = build_state(scene_base)
    results["cheap_10_single_pass_no_dest"] = {"test_bytes": len(json.dumps(cheap)),
                                               "compact_bytes": compact(cheap)}

    # build_state cost: called on the control-loop thread in Tactician.offer (tactics.py:287)
    import time as _t
    n_build = 300
    t0 = _t.perf_counter()
    for _ in range(n_build):
        build_state(scene_base)
    results["build_state_ms_10_objects"] = round((_t.perf_counter() - t0) / n_build * 1000, 3)

    vision = FakeVision.from_world([WorldFixture(*e) for e in ROSTER[:20]], perc.homography)
    runner = SemanticsRunner(ROOMCFG, "timing20", vision)
    try:
        runner.maybe_pass(1.0, perc.semantic_context(1.0), FRAME, force=True)
        _wait(runner, 1.0)
        scene_base.semantics = runner.snapshot(1.5)
    finally:
        runner.close()
    t0 = _t.perf_counter()
    for _ in range(n_build):
        build_state(scene_base)
    results["build_state_ms_20_objects"] = round((_t.perf_counter() - t0) / n_build * 1000, 3)
    scene_base.semantics = None

    (W3C / "measure_results.json").write_text(json.dumps(results, indent=1))
    # small printed digest
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("runs", "state_note", "test_fixture_10_objects")}, indent=1))
    print("\n-- runs --")
    for k, v in results["runs"].items():
        print(k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
