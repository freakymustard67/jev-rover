#!/usr/bin/env python3
"""Isolate the main-thread cost of one merge: time a single runner.poll() that
consumes a finished PassResult (store.merge + snapshot + file writes)."""
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import RoomConfig                                    # noqa: E402
from perception import Perception                                # noqa: E402
from semantics import FakeVision, SemanticsRunner, WorldFixture  # noqa: E402
from synthetic import SyntheticRoom                              # noqa: E402

sys.path.insert(0, os.path.join(HERE))
from v9_harness import SleepVision, log, warm                    # noqa: E402

def main() -> int:
    scale = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
    cfg = RoomConfig.load(os.path.join(HERE, "config", "room.synthetic.json"))
    if scale != 1.0:
        cfg.camera = replace(cfg.camera, width=int(cfg.camera.width * scale),
                             height=int(cfg.camera.height * scale))
        cfg.homography = replace(cfg.homography, image_points_px=[
            [x * scale, y * scale] for x, y in cfg.homography.image_points_px])
        cfg.floor = replace(cfg.floor, polygon_px=[
            [x * scale, y * scale] for x, y in cfg.floor.polygon_px])
        cfg.synthetic = replace(cfg.synthetic, px_per_m=cfg.synthetic.px_per_m * scale)
    cfg.semantics = replace(cfg.semantics, store_dir=store_dir,
                            min_interval_s=0.0, max_passes_per_min=100000)
    perc, syn, frame = warm(cfg)
    fixtures = FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92),
         WorldFixture("red box", 4.4, 1.0, 0.3, 0.3, 0.85)], perc.homography).fixtures
    runner = SemanticsRunner(cfg, "v9mergeiso", SleepVision(0.01, fixtures))
    try:
        ts = []
        for i in range(reps):
            runner.maybe_pass(float(i), perc.semantic_context(float(i)), frame, force=True)
            deadline = time.time() + 2.0
            while runner.worker.completed < i + 1 and time.time() < deadline:
                time.sleep(0.001)
            t0 = time.perf_counter()
            res = runner.poll(float(i) + 0.002)
            ts.append(time.perf_counter() - t0)
            assert res is not None, f"merge {i} did not land"
        log("v9_merge_iso", {"rec": "poll_merge_single", "scale": scale, "reps": reps,
            "res": f"{frame.shape[1]}x{frame.shape[0]}", "passes": runner.store.passes,
            "objects": len(runner.store.objs),
            "mean_ms": round(statistics.mean(ts) * 1000, 3),
            "p50_ms": round(sorted(ts)[len(ts) // 2] * 1000, 3),
            "p95_ms": round(sorted(ts)[int(0.95 * (len(ts) - 1))] * 1000, 3),
            "max_ms": round(max(ts) * 1000, 3)})
    finally:
        runner.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
