"""Task A2 measurements: per-snapshot copy bytes + copy latency, real configs.

Read-only wrt the repo: imports config/perception from the repo path, writes nothing.
"""
import statistics
import sys
import time

import numpy as np

REPO = "/home/freakymustard/jev-rover"
sys.path.insert(0, REPO)

from config import RoomConfig  # noqa: E402
from perception import Perception  # noqa: E402

CFGS = {
    "room.example.json": f"{REPO}/config/room.example.json",
    "room.synthetic.json": f"{REPO}/config/room.synthetic.json",
}


def copy_time(arr, n=200):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        _ = arr.copy()
        ts.append((time.perf_counter() - t0) * 1e6)
    return statistics.median(ts)


for name, path in CFGS.items():
    cfg = RoomConfig.load(path)
    perc = Perception(cfg)
    g = perc.grid
    print(f"== {name} (room {cfg.width_m}x{cfg.height_m} m, cell {g.cell_m} m) ==")
    print(f"camera declared {cfg.camera.width}x{cfg.camera.height}, "
          f"proc_scale={perc.proc_scale} -> proc frame "
          f"{int(cfg.camera.width*perc.proc_scale)}x{int(cfg.camera.height*perc.proc_scale)}"
          if cfg.camera.width else "camera size undeclared")

    # --- grid arrays exactly as OccupancyGrid builds them
    print(f"grid shape {g.log_odds.shape} = {g.w}x{g.h} cells")
    print(f"  grid_log_odds : {g.log_odds.dtype} {g.log_odds.nbytes:>8} B"
          f"  copy {copy_time(g.log_odds):7.1f} us")
    print(f"  grid_last_seen: {g.last_seen.dtype} {g.last_seen.nbytes:>8} B"
          f"  copy {copy_time(g.last_seen):7.1f} us")
    print(f"  cell_cx/cy (NOT copied, but live refs): "
          f"{g.cell_cx.nbytes + g.cell_cy.nbytes} B")

    # --- polygon as semantic_context copies it
    poly = np.asarray(cfg.floor.polygon_px, np.float32).reshape(-1, 2).copy()
    print(f"  polygon_px    : {poly.dtype} {poly.nbytes:>8} B "
          f"({poly.shape[0]} pts)")

    # --- floor_lab as semantic_context copies it (arrives only after classify)
    perc.floor.color_lab = np.array([200.0, 128.0, 128.0], np.float32)  # in-memory only
    lab = np.array(perc.floor.color_lab, float)
    print(f"  floor_lab     : {lab.dtype} {lab.nbytes:>8} B")

    ctx = perc.semantic_context(1.0)
    one = ctx.grid_log_odds.nbytes + ctx.grid_last_seen.nbytes \
        + ctx.polygon_px.nbytes + ctx.floor_lab.nbytes
    print(f"  SemanticContext snapshot total: {one} B "
          f"({one/1024:.1f} KiB)  [homography shared by ref: "
          f"{ctx.homography.H.nbytes + ctx.homography.Hinv.nbytes} B not copied]")

    # time the full semantic_context() call as run.py invokes it
    ts = []
    for _ in range(200):
        t0 = time.perf_counter()
        perc.semantic_context(1.0)
        ts.append((time.perf_counter() - t0) * 1e6)
    print(f"  Perception.semantic_context() median: {statistics.median(ts):.1f} us")

    # --- frame copy, as maybe_pass does: frame.copy()
    w, h = int(cfg.camera.width), int(cfg.camera.height)
    if w and h:
        frame = np.zeros((h, w, 3), np.uint8)
        print(f"  frame.copy() {w}x{h}x3 uint8 = {frame.nbytes} B "
              f"({frame.nbytes/1024/1024:.2f} MiB), median {copy_time(frame, 50):.0f} us")
        small = np.zeros((h // 2, w // 2, 3), np.uint8)
        print(f"  proc half-res frame {small.shape[1]}x{small.shape[0]}x3 = "
              f"{small.nbytes} B ({small.nbytes/1024/1024:.2f} MiB)")

    print(f"  semantics enabled in config: {cfg.semantics.enabled}, "
          f"min_interval_s={cfg.semantics.min_interval_s}, "
          f"max_passes_per_min={cfg.semantics.max_passes_per_min}, "
          f"max_age_s={cfg.semantics.max_age_s}")
    print()
