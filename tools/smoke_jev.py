"""Live smoke test for the Jev tactical layer: real frames -> real judgments.

Runs the real perception pipeline on the synthetic room, then asks Jev the
five tactical questions directly and prints the full answers, latency and
token cost. No rover moves, a handful of calls.

    set -a && . ./.env && set +a
    .venv/bin/python tools/smoke_jev.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import RoomConfig
from control import Executor
from perception import Perception
from scene import Pose, rover_frame
from synthetic import SyntheticRoom
from tactics import QUESTIONS, build_state
from typesafe_sdk import TypeSafeClient

# label, rover pose, goal waypoint name -> world coords
CASES = (
    ("open room, goal ahead-right", Pose(3.2, 1.8, 0.0), ("c", 3.3, 2.9)),
    ("table dead ahead, goal beyond it", Pose(1.6, 0.75, 0.0), ("b", 5.5, 0.9)),
    ("cornered, goal behind the rover", Pose(0.75, 1.4, 137.0), ("b", 5.5, 0.9)),
)


def scene_at(cfg: RoomConfig, pose: Pose, goal: tuple[str, float, float], warm: int = 8):
    """Fresh perception for each case: no teleporting inside one map."""
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    syn.rover = pose
    scene = None
    for i in range(warm):
        scene = perc.process(syn.render(), i / 15.0)
    name, gx, gy = goal
    g = scene.goal
    g.type, g.name, g.x, g.y = "goto", name, gx, gy
    _, _, bearing, rng = rover_frame(gx - scene.pose.x, gy - scene.pose.y, scene.pose.yaw_deg)
    g.bearing_deg, g.range_m = round(bearing, 1), round(rng, 2)
    path, through_unknown = Executor(cfg, perc.grid).planner.plan(
        (scene.pose.x, scene.pose.y), (gx, gy), scene.t)
    g.path_valid, g.path_through_unknown = bool(path), through_unknown
    g.path_len_m = round(sum(
        ((path[i + 1][0] - path[i][0]) ** 2 + (path[i + 1][1] - path[i][1]) ** 2) ** 0.5
        for i in range(len(path) - 1)), 2) if len(path) > 1 else None
    return scene


def main() -> int:
    cfg = RoomConfig.load(ROOT / "config" / "room.synthetic.json")
    client = TypeSafeClient()
    total_in = total_out = 0
    try:
        for label, pose, goal in CASES:
            scene = scene_at(cfg, pose, goal)
            state = build_state(scene)
            size = len(json.dumps(state))
            t0 = time.time()
            r = client.system_one(state=state, model="jev-latest", questions=QUESTIONS)
            dt = time.time() - t0
            a = r.answers
            total_in += r.usage.input_tokens
            total_out += r.usage.output_tokens

            print(f"\n=== {label}")
            print(f"    scene: pose=({scene.pose.x:.2f},{scene.pose.y:.2f},{scene.pose.yaw_deg:+.0f}) "
                  f"nearest={scene.nearest_m}@{scene.nearest_bearing_deg}deg "
                  f"clear_ahead={scene.clear_ahead_m} occ={scene.quality.occlusion_risk} "
                  f"tracks={len(scene.tracks)}")
            print(f"    goal: {scene.goal.name} bearing={scene.goal.bearing_deg} "
                  f"range={scene.goal.range_m} path_valid={scene.goal.path_valid} "
                  f"path_len={scene.goal.path_len_m} through_unknown={scene.goal.path_through_unknown}")
            print(f"    state: {size} bytes | latency {dt * 1000:.0f} ms | "
                  f"tokens {r.usage.input_tokens}+{r.usage.output_tokens}")
            print(f"    maneuver  : {a['maneuver'].choice:16s} conf={a['maneuver'].confidence:.2f} "
                  f"{ {k: round(v, 2) for k, v in a['maneuver'].probabilities.items()} }")
            print(f"    risk      : {a['risk'].score:.2f} (conf {a['risk'].confidence:.2f})")
            print(f"    truly_stuck: {a['truly_stuck'].noul:.2f}   "
                  f"path_obstructed: {a['path_obstructed'].noul:.2f}   "
                  f"observation_unreliable: {a['observation_unreliable'].noul:.2f}   "
                  f"path_still_good: {a['path_still_good'].noul:.2f}")
    finally:
        client.close()
    print(f"\ntotal tokens: {total_in} in + {total_out} out across {len(CASES)} calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
