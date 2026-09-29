"""v8 evidence: render the jev-rover semantic overlay on synthetic fixtures.

Deterministic (fixed seed, fixed rover pose) demo that:
  1. drives a real perception warm-up + one real semantics pass with the
     config's FakeVision fixtures, resolves a destination, computes its
     approach point;
  2. renders the canvas with the semantics overlay OFF and ON;
  3. asserts: OFF == pristine-clone baseline (pixel-identical), ON differs in
     the expected regions (projected object / destination / approach), both
     camera view and minimap exercised, no crash for empty map / None map /
     None grid with the flag on and off.

Run from a jev-rover clone root:
    PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
        /home/freakymustard/.hermes/cache/scratch/w5/v8/demo_semantics_overlay.py \
        --out <dir> [--baseline <pristine canvas_off.png>]

Exit code 0 = all checks pass. No display windows; PNGs via cv2.imwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd()))                       # clone root = cwd

from config import RoomConfig                            # noqa: E402
from link import Cmd                                     # noqa: E402
from perception import Perception                        # noqa: E402
from scene import SemanticMap                            # noqa: E402
from semantics import (SemanticsRunner, approach_point, build_vision,  # noqa: E402
                       resolve_destination)
from synthetic import SyntheticRoom                      # noqa: E402
from viz import Renderer                                 # noqa: E402

INSTRUCTION = "go to the green ball"
JUDG = {"source": "none", "age_s": None, "confidence": None, "risk": None,
        "truly_stuck": None, "path_obstructed": None, "observation_unreliable": None,
        "maneuver": None, "probabilities": {}}


def sha(img: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()[:16]


def changed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    assert a.shape == b.shape, (a.shape, b.shape)
    return np.any(a != b, axis=2)


def rect(mask: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> int:
    h, w = mask.shape
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    return int(mask[y0:y1, x0:x1].sum()) if x1 > x0 and y1 > y0 else 0


def has_flag() -> bool:
    return "show_semantics" in inspect.signature(Renderer.__init__).parameters


def make_renderer(cfg: RoomConfig, grid, on: bool) -> Renderer:
    """Honor clones that predate the flag (pristine baseline clone)."""
    kwargs = {"show_semantics": on} if has_flag() else {}
    return Renderer(cfg, grid, **kwargs)


def render(cfg: RoomConfig, grid, frame: np.ndarray, scene, on: bool) -> np.ndarray:
    return make_renderer(cfg, grid, on).draw(frame, scene, JUDG, Cmd(0.0, 0.0, "demo"))


def build(cfg: RoomConfig, out: Path):
    """Warm perception -> real semantics pass -> destination -> final scene."""
    cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=str(out / "store")))
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg, seed=0)
    frame = None
    for i in range(8):                                    # settle tag + floor + grid
        frame = syn.render()
        perc.process(frame, i / 15.0)
    vision = build_vision(cfg.semantics, perc.homography)
    runner = SemanticsRunner(cfg, cfg.name, vision)
    t = 0.6
    try:
        ok = runner.maybe_pass(t, perc.semantic_context(t), frame, kind="full", force=True)
        assert ok, "semantics pass was not accepted"
        deadline, merged = time.time() + 5.0, None
        while merged is None and time.time() < deadline:
            merged = runner.poll(t + 0.001)
            time.sleep(0.005)
        assert merged is not None and merged.objects, "semantics pass produced nothing"
        dest = resolve_destination(INSTRUCTION, merged, cfg=cfg.semantics)
        assert dest is not None, f"{INSTRUCTION!r} resolved to no destination"
        runner.set_destination(dest)
        sem = runner.snapshot(t + 0.001)
    finally:
        runner.close()
    scene = perc.process(frame, t + 0.01)
    scene.semantics = sem
    return cfg, perc, frame, scene


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", default=None, help="pristine-clone canvas_off.png")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    print(f"clone HEAD={head} show_semantics flag={'yes' if has_flag() else 'no'}")

    cfg, perc, frame, scene = build(RoomConfig.load("config/room.synthetic.json"), out)
    sem = scene.semantics
    print(f"scene t={scene.t:.2f} pose=({scene.pose.x:.2f},{scene.pose.y:.2f},"
          f"{scene.pose.yaw_deg:+.0f}) pose_src={scene.quality.pose_source}")
    print(f"semantics map: passes={sem.passes} model={sem.model} age={sem.age_s}s "
          f"objects={len(sem.objects)}")
    for o in sem.objects:
        px = perc.homography.world_to_img(np.array([[o.x, o.y]]))[0]
        print(f"  {o.id} '{o.label}' ({o.x:.2f},{o.y:.2f}) m conf={o.confidence:.2f} "
              f"height_suspect={o.height_suspect} -> view px=({px[0]:.0f},{px[1]:.0f})")
    dest = sem.destination
    ax, ay = approach_point(dest, (scene.pose.x, scene.pose.y), cfg.destination.standoff_m)
    print(f"destination: '{dest.label}' ({dest.x:.2f},{dest.y:.2f}) conf={dest.confidence:.2f} "
          f"object={dest.object_id}; approach=({ax:.2f},{ay:.2f}) m "
          f"standoff={cfg.destination.standoff_m}")

    off = render(cfg, perc.grid, frame, scene, False)
    cv2.imwrite(str(out / "canvas_off.png"), off)
    print(f"canvas_off sha={sha(off)} shape={off.shape}")
    if not has_flag():
        print("pristine clone (no flag): wrote canvas_off.png only")
        return 0

    on = render(cfg, perc.grid, frame, scene, True)
    cv2.imwrite(str(out / "canvas_on.png"), on)
    print(f"canvas_on  sha={sha(on)} difference vs off below")
    fails: list[str] = []

    # [A] overall change band
    d = changed(off, on)
    n = int(d.sum())
    print(f"[A] changed px on-vs-off = {n} (band 300..60000)")
    if not 300 <= n < 60000:
        fails.append(f"A: changed px {n}")

    # [B] camera-view changes near each projected object window
    view_w, view_h = frame.shape[1], frame.shape[0]
    for o in sem.objects:
        px, py = perc.homography.world_to_img(np.array([[o.x, o.y]]))[0]
        k = rect(d, px - 40, py - 40, px + 240, py + 40)
        print(f"[B] '{o.label}' window ({px - 40:.0f},{py - 40:.0f})-({px + 240:.0f},"
              f"{py + 40:.0f}) changed px = {k}")
        if k < 10:
            fails.append(f"B: no change near {o.label}")

    # [C]/[D] camera view and minimap both exercised
    n_mm = 0
    gh, gw = perc.grid.to_rgb(scene.t, cfg.grid.stale_s).shape[:2]
    mh = min(gh * 2, view_h // 2)
    mw = int(gw * 2 * mh / (gh * 2))
    n_mm = rect(d, view_w, 0, view_w + mw, mh)
    n_view = int(d[:, :view_w].sum())
    print(f"[C] camera-view region ({view_w}x{view_h}) changed px = {n_view}")
    print(f"[D] minimap region ({mw}x{mh} at x={view_w}) changed px = {n_mm}")
    if n_view < 200:
        fails.append(f"C: camera view barely changed ({n_view})")
    if n_mm < 50:
        fails.append(f"D: minimap not exercised ({n_mm})")

    # [E] destination + approach markers drew where projected
    dx, dy = perc.homography.world_to_img(np.array([[dest.x, dest.y]]))[0]
    kd = rect(d, dx - 40, dy - 40, dx + 240, dy + 40)
    px, py = perc.homography.world_to_img(np.array([[ax, ay]]))[0]
    ka = rect(d, px - 30, py - 30, px + 30, py + 30)
    print(f"[E] destination window changed px = {kd}; approach window changed px = {ka}")
    if kd < 10 or ka < 10:
        fails.append("E: destination/approach marker missing")

    # [F] exact colours present on the ON canvas, absent on OFF
    for name, color in (("object magenta", (255, 0, 255)), ("dest yellow", (0, 255, 255)),
                        ("approach orange", (0, 200, 255))):
        cnt_on = int(np.all(on == color, axis=2).sum())
        cnt_off = int(np.all(off == color, axis=2).sum())
        print(f"[F] exact-colour px {name}: on={cnt_on} off={cnt_off}")
        if cnt_on < 10 or cnt_off != 0:
            fails.append(f"F: colour {name}")

    # [G] empty map: flag on draws nothing -> identical to flag off
    scene_empty = replace(scene, semantics=SemanticMap())
    on_empty = render(cfg, perc.grid, frame, scene_empty, True)
    off_empty = render(cfg, perc.grid, frame, scene_empty, False)
    same_empty = bool(np.array_equal(on_empty, off)) and bool(np.array_equal(off_empty, off))
    print(f"[G] empty map: off==baseline {bool(np.array_equal(off_empty, off))}, "
          f"on==baseline {bool(np.array_equal(on_empty, off))}")
    if not same_empty:
        fails.append("G: empty map changed the canvas")

    # [H] semantics=None: no crash, no change, both flags
    scene_none = replace(scene, semantics=None)
    on_none = render(cfg, perc.grid, frame, scene_none, True)
    off_none = render(cfg, perc.grid, frame, scene_none, False)
    same_none = bool(np.array_equal(on_none, off)) and bool(np.array_equal(off_none, off))
    print(f"[H] semantics=None: on/off renders ok, both == baseline: {same_none}")
    if not same_none:
        fails.append("H: None map changed the canvas")

    # [I] grid=None: minimap path skipped, camera overlay still renders
    c_nogrid = render(cfg, None, frame, scene, True)
    print(f"[I] grid=None + flag on: ok, canvas {c_nogrid.shape}")
    if c_nogrid.shape != on.shape:
        fails.append("I: grid=None shape")

    # [J] baseline identity (pristine clone render of the same frame)
    if args.baseline:
        base = cv2.imread(args.baseline)
        assert base is not None, f"cannot read {args.baseline}"
        md = int(np.abs(base.astype(int) - off.astype(int)).max())
        same = bool(np.array_equal(base, off))
        print(f"[J] baseline={args.baseline} identical={same} max|diff|={md} "
              f"(baseline sha={sha(base)}, off sha={sha(off)})")
        if not same and md > 2:
            fails.append(f"J: baseline differs (max|diff|={md})")

    # zoomed crops for human inspection
    o1 = sem.objects[0]
    px, py = perc.homography.world_to_img(np.array([[o1.x, o1.y]]))[0]
    x0, y0 = int(max(0, px - 140)), int(max(0, py - 70))
    for name, canvas in (("on", on), ("off", off)):
        crop = canvas[y0:y0 + 150, x0:x0 + 420]
        cv2.imwrite(str(out / f"zoom_obj1_{name}.png"),
                    cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
    mm_crop = on[0:mh, view_w:view_w + mw]
    cv2.imwrite(str(out / "zoom_minimap_on.png"),
                cv2.resize(mm_crop, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST))
    print("[K] crops: zoom_obj1_on/off.png, zoom_minimap_on.png")

    if fails:
        print(f"\nRESULT: FAIL ({len(fails)}): " + "; ".join(fails))
        return 1
    print("\nRESULT: all checks PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())