"""Calibration utilities: camera listing, tag printing, floor homography, intrinsics.

Typical first run on a new room:
    .venv/bin/python calibrate.py cameras
    .venv/bin/python calibrate.py tag --id 0 --size 0.15        # print at 100%
    .venv/bin/python calibrate.py floor --config config/room.json
    .venv/bin/python calibrate.py check --config config/room.json

Order matters: if you calibrate lens intrinsics, do that first and point
``camera.intrinsics`` at the result; then re-run ``floor`` on undistorted frames.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from config import RoomConfig, _need  # noqa: F401  (kept for symmetry of config imports)
from depth import (DepthModel, calibrate_from_depth, depth_overlay,
                   floor_polygon_px, median_depth)
from perception import load_intrinsics


def _open(source: str, width: int = 0, height: int = 0) -> cv2.VideoCapture:
    src: int | str = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(src, cv2.CAP_V4L2 if isinstance(src, int) else cv2.CAP_ANY)
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def _grab(cap: cv2.VideoCapture, warmup: int = 10) -> np.ndarray:
    frame = None
    for _ in range(warmup):
        ok, f = cap.read()
        if ok:
            frame = f
    if frame is None:
        raise SystemExit("camera delivered no frames")
    return frame


def _homography_space(frame: np.ndarray, cfg: RoomConfig) -> np.ndarray:
    """Undistort iff camera.intrinsics is configured (the same rule Perception applies).

    Calibration clicks must land in the space the homography is defined in -
    otherwise a calibrated lens silently biases every reference point, and the
    perception/semantics pipeline (which undistorts first) inherits the error.
    """
    if not cfg.camera.intrinsics:
        return frame
    intr = load_intrinsics(cfg.camera.intrinsics)
    if intr is None:
        raise SystemExit(f"camera.intrinsics is set but {cfg.camera.intrinsics} is not readable")
    return cv2.undistort(frame, intr[0], intr[1])


# ------------------------------------------------------------------ cameras

def cmd_cameras(args) -> int:
    print("video4linux devices:")
    for p in sorted(Path("/sys/class/video4linux").glob("video*")):
        name = (p / "name").read_text().strip() if (p / "name").exists() else "?"
        print(f"  /dev/{p.name:10s} {name}")
    print("\nprobe with OpenCV (first readable index per device):")
    for idx in range(0, 12):
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        if ok:
            print(f"  index {idx}: {frame.shape[1]}x{frame.shape[0]}")
        else:
            print(f"  index {idx}: opened, no frame")
        cap.release()
    return 0


# ---------------------------------------------------------------- tag print

def cmd_tag(args) -> int:
    dpi = 300.0
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    size_mm = float(args.size) * 1000.0
    side_px = int(round(size_mm / 25.4 * dpi))
    marker = cv2.aruco.generateImageMarker(dictionary, args.id, side_px)
    quiet = max(8, side_px // 8)                     # >= 1 module of white margin
    img = cv2.copyMakeBorder(marker, quiet, quiet, quiet, quiet,
                             cv2.BORDER_CONSTANT, value=255)
    caption_h = 90
    canvas = np.full((img.shape[0] + caption_h, img.shape[1],), 255, np.uint8)
    canvas[:img.shape[0], :] = img
    text = [f"APRILTAG 36h11  id={args.id}",
            f"black square {size_mm:.1f} mm - print at 100%, no scaling",
            "mount flat; keep the white margin visible"]
    y = img.shape[0] + 28
    for line in text:
        cv2.putText(canvas, line, (quiet, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1, cv2.LINE_AA)
        y += 26
    out = Path(args.out or f"calibration/tag_{args.id}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas)
    print(f"wrote {out}  ({canvas.shape[1]}x{canvas.shape[0]} px at {dpi:.0f} dpi)")
    print("check after printing: measure the black square; it must match the size above.")
    return 0


# ------------------------------------------------------------------- floor

class _Clicker:
    def __init__(self, window: str):
        self.window = window
        self.points: list[tuple[float, float]] = []
        self.done = False
        cv2.namedWindow(window)
        cv2.setMouseCallback(window, self._on_mouse)

    def _on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((float(x), float(y)))

    def reset(self):
        self.points = []


def _prompt_reference_points(n: int) -> list[list[float]]:
    print(f"\nEnter {n} reference points on the floor, in world metres, as 'x,y'.")
    print("Pick points you can click precisely in the image (tape corners, tile joints).")
    pts = []
    while len(pts) < n:
        raw = input(f"  point {len(pts) + 1}/{n} (x,y in m): ").strip()
        try:
            x, y = (float(v) for v in raw.replace(",", " ").split())
        except ValueError:
            print("  ... expected something like: 1.25,0.40")
            continue
        pts.append([x, y])
    return pts


def _draw_overlay(frame: np.ndarray, points: list[tuple[float, float]],
                  world: list[list[float]] | None) -> np.ndarray:
    out = frame.copy()
    for i, p in enumerate(points):
        c = (int(p[0]), int(p[1]))
        cv2.circle(out, c, 6, (0, 220, 255), -1)
        label = str(i + 1) if world is None else f"{i + 1}:{world[i][0]:.2f},{world[i][1]:.2f}"
        cv2.putText(out, label, (c[0] + 8, c[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 220, 255), 2, cv2.LINE_AA)
    if len(points) >= 2:
        cv2.polylines(out, [np.asarray(points, np.int32).reshape(-1, 1, 2)],
                      len(points) > 3, (255, 120, 0), 2)
    cv2.putText(out, "click floor corners  [p]=close polygon  [r]=reset  [q]=quit",
                (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def cmd_floor(args) -> int:
    cfg_path = Path(args.config)
    raw = json.loads(cfg_path.read_text())
    cfg = RoomConfig.load(cfg_path)
    cap = _open(args.camera or cfg.camera.source, cfg.camera.width, cfg.camera.height)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {args.camera or cfg.camera.source}")
    frame = _grab(cap)
    cap.release()
    frame = _homography_space(frame, cfg)
    h, w = frame.shape[:2]
    if (w, h) != (cfg.camera.width, cfg.camera.height):
        print(f"note: capture is {w}x{h} but config says "
              f"{cfg.camera.width}x{cfg.camera.height}; updating the config to the capture size")

    window = "calibrate floor"
    clicker = _Clicker(window)
    print("\nStep 1: click around the floor area you want modeled (>= 4 points).")
    while True:
        cv2.imshow(window, _draw_overlay(frame, clicker.points, None))
        key = cv2.waitKey(20) & 0xFF
        if key == ord("r"):
            clicker.reset()
        elif key == ord("p") and len(clicker.points) >= 3:
            break
        elif key == ord("q"):
            cv2.destroyAllWindows()
            return 1
    polygon = list(clicker.points)
    print(f"floor polygon: {len(polygon)} points")

    print("\nStep 2: reference points for the metric homography.")
    world = _prompt_reference_points(max(4, args.points))
    clicker.reset()
    print("Now click those same points in the image, in the order you entered them.")
    while True:
        cv2.imshow(window, _draw_overlay(frame, clicker.points, world[:len(clicker.points)]))
        key = cv2.waitKey(20) & 0xFF
        if key == ord("r"):
            clicker.reset()
        elif key == ord("q"):
            cv2.destroyAllWindows()
            return 1
        elif len(clicker.points) >= len(world):
            break
    image_points = list(clicker.points)
    cv2.destroyAllWindows()

    if len(image_points) == 4:
        H = cv2.getPerspectiveTransform(np.float32(image_points), np.float32(world))
    else:
        H, _ = cv2.findHomography(np.float32(image_points), np.float32(world), cv2.RANSAC, 0.05)
    if H is None:
        raise SystemExit("homography failed; re-run and click the points more precisely")
    got = cv2.perspectiveTransform(np.float32(image_points).reshape(-1, 1, 2), H).reshape(-1, 2)
    err = float(np.mean(np.linalg.norm(got - np.asarray(world), axis=1)))
    print(f"homography reprojection error: {err:.3f} m "
          f"({'good' if err < 0.05 else 'HIGH - re-click the points'})")

    raw["camera"]["width"], raw["camera"]["height"] = w, h
    raw.setdefault("floor", {})["polygon_px"] = [[round(x, 1), round(y, 1)] for x, y in polygon]
    raw.setdefault("homography", {})["image_points_px"] = [[round(x, 1), round(y, 1)] for x, y in image_points]
    raw["homography"]["world_points_m"] = [[round(x, 3), round(y, 3)] for x, y in world]
    cfg_path.write_text(json.dumps(raw, indent=2) + "\n")
    print(f"updated {cfg_path}")
    return 0


# --------------------------------------------------------------- intrinsics

def cmd_intrinsics(args) -> int:
    cfg = RoomConfig.load(args.config)
    cap = _open(args.camera or cfg.camera.source, cfg.camera.width, cfg.camera.height)
    if not cap.isOpened():
        raise SystemExit("cannot open camera")
    pattern = (args.cols, args.rows)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    objp = np.zeros((args.rows * args.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square
    obj_points, img_points = [], []
    window = "calibrate intrinsics"
    cv2.namedWindow(window)
    print(f"\nShow a {args.cols}x{args.rows} checkerboard (square {args.square} m) and press "
          f"SPACE to capture; need {args.views} views from varied angles; ESC to finish.")
    while len(img_points) < args.views:
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCornersSB(gray, pattern, None)
        vis = frame.copy()
        if found:
            cv2.drawChessboardCorners(vis, pattern, corners, found)
        cv2.putText(vis, f"{len(img_points)}/{args.views} captured", (16, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0) if found else (0, 0, 255), 2)
        cv2.imshow(window, vis)
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            break
        if key == ord(" ") and found:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            obj_points.append(objp.copy())
            img_points.append(corners)
            print(f"  captured {len(img_points)}")
    cv2.destroyAllWindows()
    cap.release()
    if len(img_points) < 6:
        raise SystemExit("need at least 6 good views")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_points, img_points, gray.shape[::-1], None, None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "camera_matrix": K.tolist(), "distortion": dist.ravel().tolist(),
        "rms": float(rms), "resolution": [int(gray.shape[1]), int(gray.shape[0])],
    }, indent=2) + "\n")
    print(f"rms {rms:.3f} px -> {out}")
    print(f"now set \"intrinsics\": \"{out}\" in {args.config} and re-run: calibrate.py floor")
    return 0


# ---------------------------------------------------------------- bg reference

def cmd_bg(args) -> int:
    """Capture an empty-room reference frame (rover absent) into the config."""
    cfg_path = Path(args.config)
    cfg = RoomConfig.load(cfg_path)
    frame = _grab_frames(args.camera or cfg.camera.source, cfg, 1, warm=5)[0]
    out = Path(args.out or "calibration/empty_room.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    raw = json.loads(cfg_path.read_text())
    raw.setdefault("floor", {})["background"] = str(out)
    cfg_path.write_text(json.dumps(raw, indent=2) + "\n")
    RoomConfig.load(cfg_path)
    print(f"[bg] wrote {out} and set floor.background in {cfg_path}")
    print("[bg] the rover must be ABSENT from this frame: it is the background "
          "reference for the floor model and for pose_source=blob")
    return 0


# -------------------------------------------------------------------- depth

def _grab_frames(source: str, cfg: RoomConfig, count: int, warm: int = 5) -> list[np.ndarray]:
    cap = _open(source, cfg.camera.width, cfg.camera.height)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {source}")
    frames: list[np.ndarray] = []
    try:
        for _ in range(count + warm):
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
    finally:
        cap.release()
    if len(frames) < count:
        raise SystemExit(f"camera produced only {len(frames)} frames; need {count}")
    return frames[-count:]


def cmd_depth(args) -> int:
    """Camera-only calibration: metric depth -> floor plane -> pixel->floor mapping."""
    cfg_path = Path(args.config)
    cfg = RoomConfig.load(cfg_path)
    hfov = float(args.hfov) if args.hfov is not None else float(cfg.camera.hfov_deg)

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit(f"cannot read image {args.image!r}")
        frames = [img]
    else:
        frames = _grab_frames(args.camera or cfg.camera.source, cfg, args.frames)
    print(f"[depth] {len(frames)} frame(s) @ {frames[0].shape[1]}x{frames[0].shape[0]}, "
          f"hfov {hfov:.1f} deg -> focal {_need_focal(hfov, frames[0].shape[1]):.0f} px")

    model = DepthModel(args.model) if args.model else DepthModel()
    print(f"[depth] model {model.name} (metric indoor); first inference loads the weights ...")
    stack: list[np.ndarray] = []
    for i, frame in enumerate(frames):
        stack.append(model.infer(frame))
        print(f"[depth]   frame {i + 1}/{len(frames)}: {model.latencies_s[-1]:.1f}s "
              f"(load {model.load_s:.1f}s)")
    depth_m = median_depth(stack)

    result = calibrate_from_depth(depth_m, hfov, tol_m=args.tol, seed=args.seed)
    if result is None:
        print("[depth] calibration FAILED: no plausible floor plane in the lower ~2/3 "
              "of the frame. Check the camera mount, the hfov, and that the floor is "
              "visible.", file=sys.stderr)
        return 1
    quality = result.quality()
    for key, value in quality.items():
        print(f"[depth]   {key}: {value}")

    out = Path(args.out or "runs/depth_calibration.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), depth_overlay(frames[-1], depth_m, result))
    print(f"[depth] overlay -> {out}  (eyeball the 0.5 m grid on the floor)")

    # --- write the mapping back into the config ---
    raw = json.loads(cfg_path.read_text())
    h, w = result.image_wh[1], result.image_wh[0]
    H = result.mapping.homography
    pts = result.plane.inlier_pts_px
    x0, y0 = (float(v) for v in pts.min(axis=0))
    x1, y1 = (float(v) for v in pts.max(axis=0))
    corners = [[round(x0, 1), round(y0, 1)], [round(x1, 1), round(y0, 1)],
               [round(x1, 1), round(y1, 1)], [round(x0, 1), round(y1, 1)]]
    world = []
    for px, py in corners:
        p = H @ np.array([px, py, 1.0])
        world.append([round(float(p[0] / p[2]), 3), round(float(p[1] / p[2]), 3)])
    raw["camera"]["width"], raw["camera"]["height"] = int(w), int(h)
    raw["camera"]["hfov_deg"] = round(hfov, 1)
    raw["homography"] = {"image_points_px": corners, "world_points_m": world}
    poly = floor_polygon_px(result.plane, (h, w))
    if poly is not None:
        raw.setdefault("floor", {})["polygon_px"] = [[round(float(px), 1), round(float(py), 1)]
                                                    for px, py in poly]
    else:
        print("[depth] warning: no floor polygon found; keeping the existing one")
    room_w, room_h = (round(v, 2) for v in result.mapping.floor_size_m)
    raw["width_m"], raw["height_m"] = room_w, room_h
    raw["depth"] = {
        "model": model.name,
        "frames": len(frames),
        "hfov_deg": round(hfov, 1),
        "focal_px": round(result.f_px, 1),
        "image_wh": [int(w), int(h)],
        "normal": [round(float(v), 5) for v in result.plane.normal],
        "offset_m": round(float(result.plane.offset_m), 4),
        "camera_height_m": round(float(result.plane.height_m), 3),
        "inlier_ratio": round(float(result.plane.inlier_ratio), 3),
        "residual_median_m": round(float(result.plane.residual_median_m), 4),
        "residual_p90_m": round(float(result.plane.residual_p90_m), 4),
        "inliers": int(result.plane.n_inliers),
        "samples": int(result.plane.n_samples),
        "floor_size_m": [room_w, room_h],
        "metres_per_px_center": None if result.mapping.mpp_center_m is None
        else [round(float(v), 5) for v in result.mapping.mpp_center_m],
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "camera-only, test-grade (~5-15% scale error, frame wobble); "
                "the sweep-ruler replaces this without interface changes",
    }
    for name, wp in (raw.get("waypoints") or {}).items():
        cx = min(max(float(wp[0]), 0.3), room_w - 0.3)
        cy = min(max(float(wp[1]), 0.3), room_h - 0.3)
        if (round(cx, 2), round(cy, 2)) != (float(wp[0]), float(wp[1])):
            print(f"[depth] waypoint {name!r} clamped into the calibrated room: "
                  f"({wp[0]}, {wp[1]}) -> ({cx:.2f}, {cy:.2f})")
        wp[0], wp[1] = round(cx, 2), round(cy, 2)
    cfg_path.write_text(json.dumps(raw, indent=2) + "\n")
    RoomConfig.load(cfg_path)          # parses or raises: never leave a broken config
    print(f"[depth] wrote mapping + polygon + quality to {cfg_path}")
    print("[depth] limits: test-grade (~5-15% scale error, wobble); pose_source=blob "
          "consumes this mapping; the sweep-ruler will replace it unchanged")
    return 0


def _need_focal(hfov_deg: float, width_px: int) -> float:
    from depth import focal_px_from_hfov
    return focal_px_from_hfov(hfov_deg, width_px)


# -------------------------------------------------------------------- check

def cmd_check(args) -> int:
    from perception import Camera, Perception
    from viz import Renderer

    cfg = RoomConfig.load(args.config)
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            raise SystemExit(f"cannot read {args.image}")
    else:
        cam = Camera(cfg.camera)
        ok, frame, _ = cam.read()
        cam.release()
        if not ok:
            raise SystemExit("no frame from camera")
    perc = Perception(cfg)
    scene = perc.process(frame, 0.0)
    print(scene.json_line())
    out = Path(args.out or "runs/calibration_check.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = Renderer(cfg, perc.grid)
    canvas = r.draw(perc.frame_h if perc.frame_h is not None else frame, scene,
                    {"source": "none", "maneuver": "-", "probabilities": {},
                     "age_s": None}, __import__("link").Cmd())
    cv2.imwrite(str(out), canvas)
    print(f"overlay written to {out} - check that the belief map lines up with the floor")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cameras", help="list video devices and test them")
    c.set_defaults(func=cmd_cameras)

    t = sub.add_parser("tag", help="write a printable AprilTag with a white quiet zone")
    t.add_argument("--id", type=int, default=0)
    t.add_argument("--size", type=float, default=0.15, help="black square size in metres")
    t.add_argument("--out", default=None)
    t.set_defaults(func=cmd_tag)

    f = sub.add_parser("floor", help="click the floor polygon and metric reference points")
    f.add_argument("--config", default="config/room.json")
    f.add_argument("--camera", default=None)
    f.add_argument("--points", type=int, default=4, help="minimum reference points")
    f.set_defaults(func=cmd_floor)

    i = sub.add_parser("intrinsics", help="checkerboard lens calibration (optional)")
    i.add_argument("--config", default="config/room.json")
    i.add_argument("--camera", default=None)
    i.add_argument("--cols", type=int, default=9)
    i.add_argument("--rows", type=int, default=6)
    i.add_argument("--square", type=float, default=0.025, help="chessboard square in metres")
    i.add_argument("--views", type=int, default=15)
    i.add_argument("--out", default="calibration/camera.json")
    i.set_defaults(func=cmd_intrinsics)

    k = sub.add_parser("check", help="run perception once and write an annotated frame")
    k.add_argument("--config", default="config/room.json")
    k.add_argument("--image", default=None, help="use an image file instead of the camera")
    k.add_argument("--out", default=None)
    k.set_defaults(func=cmd_check)

    b = sub.add_parser("bg", help="capture the empty-room reference frame (rover absent)")
    b.add_argument("--config", default="config/room.json")
    b.add_argument("--camera", default=None)
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_bg)

    d = sub.add_parser("depth", help="camera-only calibration: depth -> floor plane + mapping")
    d.add_argument("--config", default="config/room.json")
    d.add_argument("--camera", default=None)
    d.add_argument("--image", default=None, help="calibrate from a still image")
    d.add_argument("--frames", type=int, default=5, help="frames to median (default 5)")
    d.add_argument("--hfov", type=float, default=None, help="override camera.hfov_deg")
    d.add_argument("--model", default=None, help="depth model id override")
    d.add_argument("--tol", type=float, default=0.05, help="RANSAC inlier distance (m)")
    d.add_argument("--seed", type=int, default=0)
    d.add_argument("--out", default=None, help="annotated overlay path")
    d.set_defaults(func=cmd_depth)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
