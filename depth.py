"""Camera-only depth calibration: floor plane + pixel->floor mapping.

No tags, no tape measure. A metric depth model (Depth-Anything-V2-Metric-Indoor)
predicts metres; the focal length comes from the configured horizontal FOV; the
floor is a RANSAC plane fit on the lower part of the median depth map; the
pixel->floor mapping (a homography, unchanged interface) falls out of the plane.

Honest limits - this is **test-grade**:
* the Small metric model has roughly 5-15% indoor scale error;
* per-frame depth wobbles, so the calibrator medians several frames and the
  blob pose smooths position;
* the FOV-derived focal ignores lens distortion;
* the room frame is camera-derived (origin + axes from the plane), so room
  coordinates are self-consistent but not survey-grade.

The sweep-ruler (M4) replaces this mapping without interface changes: floor
consumers only ever see a ``Homography``, a floor polygon and a room rectangle.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

DEFAULT_DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"


def focal_px_from_hfov(hfov_deg: float, width_px: int) -> float:
    if not 0.0 < float(hfov_deg) < 179.0:
        raise ValueError(f"hfov_deg must be in (0, 179), got {hfov_deg}")
    return (float(width_px) / 2.0) / math.tan(math.radians(float(hfov_deg)) / 2.0)


def median_depth(stack: list[np.ndarray]) -> np.ndarray:
    """Pixel-wise median of several metric depth maps (they must share a shape)."""
    if not stack:
        raise ValueError("no depth frames")
    arr = np.stack([np.asarray(d, np.float32) for d in stack], axis=0)
    return np.median(arr, axis=0)


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


@dataclass
class FloorPlane:
    normal: np.ndarray            # unit, camera frame (X right, Y down, Z forward), pointing up
    offset_m: float               # plane: normal . X = offset_m
    height_m: float               # camera height above the floor (= -offset_m)
    inlier_ratio: float
    residual_median_m: float
    residual_p90_m: float
    n_inliers: int
    n_samples: int
    inlier_mask: np.ndarray = field(repr=False)     # HxW bool, inlier points only
    inlier_pts_px: np.ndarray = field(repr=False)   # (M,2) float: the inlier image points


def _refit(pts: np.ndarray) -> tuple[np.ndarray, float] | None:
    if len(pts) < 3:
        return None
    c = pts.mean(axis=0)
    try:
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    n = _unit(vt[-1])
    # camera Y points DOWN, so "up from the floor" is the negative-Y hemisphere
    if n[1] > 0:
        n = -n
    return n, float(n @ c)


def fit_floor_plane(depth_m: np.ndarray, f_px: float, cx: float, cy: float, *,
                    lower_frac: float = 2.0 / 3.0, tol_m: float = 0.05,
                    iters: int = 256, seed: int = 0, sample_budget: int = 20000,
                    min_depth_m: float = 0.2, max_depth_m: float = 8.0,
                    ) -> FloorPlane | None:
    """RANSAC plane fit on the lower ``lower_frac`` of a metric depth map.

    Points are in camera coordinates: X = (u-cx)*d/f, Y = (v-cy)*d/f, Z = d.
    Returns None when there is not enough valid floor to fit.
    """
    depth = np.asarray(depth_m, np.float32)
    h, w = depth.shape
    row0 = int(round(h * (1.0 - float(lower_frac))))
    sub = depth[row0:h, :]
    valid = np.isfinite(sub) & (sub > min_depth_m) & (sub < max_depth_m)
    n_valid = int(valid.sum())
    if n_valid < 64:
        return None
    stride = max(1, int(round(math.sqrt(n_valid / float(sample_budget)))))
    ys, xs = np.mgrid[row0:h, 0:w]
    ys = ys[::stride, ::stride][valid[::stride, ::stride]]
    xs = xs[::stride, ::stride][valid[::stride, ::stride]]
    d = sub[::stride, ::stride][valid[::stride, ::stride]]
    pts = np.stack([(xs - cx) * d / f_px, (ys - cy) * d / f_px, d], axis=1).astype(np.float64)
    if len(pts) < 64:
        return None

    rng = np.random.default_rng(seed)
    best_n, best_off, best_count = None, 0.0, -1
    s = len(pts)
    for _ in range(int(iters)):
        i = rng.choice(s, 3, replace=False)
        p1, p2, p3 = pts[i]
        n = np.cross(p2 - p1, p3 - p1)
        norm = float(np.linalg.norm(n))
        if norm < 1e-9:
            continue
        n = n / norm
        off = float(n @ p1)
        count = int((np.abs(pts @ n - off) <= tol_m).sum())
        if count > best_count:
            best_n, best_off, best_count = n, off, count
    if best_n is None or best_count < 0.15 * s:
        return None

    mask = np.abs(pts @ best_n - best_off) <= tol_m
    refit = _refit(pts[mask])
    if refit is None:
        return None
    n, off = refit
    height = -off
    # A floor must be BELOW the camera: in camera coords (Y down) its up-normal
    # has a meaningful negative Y. A frontal plane (a wall, a constant-depth
    # image) has n_y ~= 0 and must not pass as a floor.
    if n[1] > -0.05 or not (0.2 <= height <= 5.0):
        return None
    dist = np.abs(pts @ n - off)
    mask = dist <= tol_m
    if int(mask.sum()) < 0.15 * s:
        return None
    residuals = dist[mask]

    # point back at the full-resolution image (strided grid -> sparse mask)
    full = np.zeros((h, w), bool)
    full_sub = full[row0:h, :]
    full_sub[::stride, ::stride][valid[::stride, ::stride]] = mask
    inlier_pts = np.stack([xs[mask], ys[mask]], axis=1).astype(np.float32)
    return FloorPlane(
        normal=n, offset_m=off, height_m=-off,
        inlier_ratio=float(mask.mean()),
        residual_median_m=float(np.median(residuals)),
        residual_p90_m=float(np.percentile(residuals, 90)),
        n_inliers=int(mask.sum()), n_samples=s,
        inlier_mask=full, inlier_pts_px=inlier_pts)


@dataclass
class FloorMapping:
    homography: np.ndarray            # image px -> floor metres (room frame)
    floor_size_m: tuple[float, float]  # visible floor bbox + margins
    origin_shift_m: tuple[float, float]
    mpp_center_m: tuple[float, float] | None   # metres per pixel at the image centre
    correspondences: int


def build_floor_mapping(plane: FloorPlane, f_px: float, cx: float, cy: float, *,
                        margin_m: float = 0.2, max_points: int = 200,
                        min_side_m: float = 0.5, max_side_m: float = 30.0,
                        ) -> FloorMapping | None:
    """Homography from the fitted plane + the observed floor points.

    The room frame is camera-derived: x follows image-right projected onto the
    floor, y points away from the camera; the origin is shifted so the observed
    floor starts at (margin, margin). Only points that were actually observed
    are used, so distant rays through walls cannot inflate the room.
    """
    n = plane.normal
    right = np.array([1.0, 0.0, 0.0])
    x_f = _unit(right - float(right @ n) * n)
    y_f = _unit(np.cross(n, x_f))

    def floor_xy(px: np.ndarray, py: np.ndarray) -> np.ndarray | None:
        r = np.stack([(px - cx) / f_px, (py - cy) / f_px, np.ones_like(px)], axis=1)
        denom = r @ n
        t = np.where(np.abs(denom) > 1e-9, plane.offset_m / denom, np.nan)
        xyz = r * t[:, None]
        ok = np.isfinite(t) & (t > 0)
        if not ok.all():
            return None
        return np.stack([xyz @ x_f, xyz @ y_f], axis=1)

    # orient y away from the camera (image "up" = +y)
    pts_px = plane.inlier_pts_px
    if len(pts_px) > max_points:
        idx = np.linspace(0, len(pts_px) - 1, max_points).astype(int)
        pts_px = pts_px[idx]
    coords = floor_xy(pts_px[:, 0], pts_px[:, 1])
    if coords is None:
        return None
    # sign of y: the point furthest "up" in the image should have the largest y
    up_idx = int(np.argmin(pts_px[:, 1]))
    down_idx = int(np.argmax(pts_px[:, 1]))
    if coords[up_idx, 1] < coords[down_idx, 1]:
        y_f = -y_f
        coords = floor_xy(pts_px[:, 0], pts_px[:, 1])
        if coords is None:
            return None

    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    extent = maxs - mins
    if not (min_side_m <= extent[0] <= max_side_m and min_side_m <= extent[1] <= max_side_m):
        return None
    shift = mins - float(margin_m)
    dst = coords - shift
    src = pts_px.astype(np.float64)
    if len(src) < 4:
        return None
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None:
        return None

    # metres per pixel at the image centre (scale diagnostic)
    mpp = None
    c0 = floor_xy(np.array([cx]), np.array([cy]))
    c1 = floor_xy(np.array([cx + 1.0]), np.array([cy]))
    c2 = floor_xy(np.array([cx]), np.array([cy + 1.0]))
    if c0 is not None and c1 is not None and c2 is not None:
        mpp = (float(np.linalg.norm(c1 - c0)), float(np.linalg.norm(c2 - c0)))
    return FloorMapping(
        homography=H.astype(np.float64),
        floor_size_m=(float(extent[0] + 2 * margin_m), float(extent[1] + 2 * margin_m)),
        origin_shift_m=(float(shift[0]), float(shift[1])),
        mpp_center_m=mpp,
        correspondences=len(src))


def floor_polygon_px(plane: FloorPlane, shape: tuple[int, int], *,
                     dilate_px: int = 9, epsilon_frac: float = 0.015,
                     min_area_frac: float = 0.02) -> np.ndarray | None:
    """Largest simplified contour of the floor inliers, in image pixels."""
    mask = plane.inlier_mask.astype(np.uint8) * 255
    k = max(1, int(dilate_px))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    big = max(contours, key=cv2.contourArea)
    if cv2.contourArea(big) < min_area_frac * shape[0] * shape[1]:
        return None
    eps = epsilon_frac * cv2.arcLength(big, True)
    poly = cv2.approxPolyDP(big, eps, True)
    return poly.reshape(-1, 2).astype(np.float32)


@dataclass
class DepthCalibrationResult:
    plane: FloorPlane
    mapping: FloorMapping
    f_px: float
    cx: float
    cy: float
    hfov_deg: float
    image_wh: tuple[int, int]

    def quality(self) -> dict:
        p, m = self.plane, self.mapping
        return {
            "inlier_ratio": round(p.inlier_ratio, 3),
            "residual_median_m": round(p.residual_median_m, 4),
            "residual_p90_m": round(p.residual_p90_m, 4),
            "camera_height_m": round(p.height_m, 3),
            "focal_px": round(self.f_px, 1),
            "floor_size_m": [round(v, 2) for v in m.floor_size_m],
            "metres_per_px_center": None if m.mpp_center_m is None
            else [round(v, 5) for v in m.mpp_center_m],
        }


def calibrate_from_depth(depth_m: np.ndarray, hfov_deg: float, *,
                         tol_m: float = 0.05, seed: int = 0) -> DepthCalibrationResult | None:
    """The whole pipeline on a prepared depth map: focal -> plane -> mapping."""
    h, w = depth_m.shape
    f_px = focal_px_from_hfov(hfov_deg, w)
    plane = fit_floor_plane(depth_m, f_px, w / 2.0, h / 2.0, tol_m=tol_m, seed=seed)
    if plane is None:
        return None
    mapping = build_floor_mapping(plane, f_px, w / 2.0, h / 2.0)
    if mapping is None:
        return None
    return DepthCalibrationResult(plane=plane, mapping=mapping, f_px=f_px,
                                  cx=w / 2.0, cy=h / 2.0, hfov_deg=hfov_deg,
                                  image_wh=(w, h))


def depth_overlay(frame: np.ndarray, depth_m: np.ndarray,
                  result: DepthCalibrationResult, *,
                  title: str = "depth calibration") -> np.ndarray:
    """Annotated overlay for eyeballing: floor inliers + 0.5 m grid + metrics."""
    h, w = frame.shape[:2]
    view = frame.copy()
    plane, mapping = result.plane, result.mapping

    # inlier scatter
    pts = plane.inlier_pts_px
    if len(pts):
        sel = pts[:: max(1, len(pts) // 4000)]
        overlay = view.copy()
        for x, y in sel.astype(int):
            cv2.circle(overlay, (int(x), int(y)), 2, (0, 255, 0), -1)
        view = cv2.addWeighted(view, 0.6, overlay, 0.4, 0)

    # floor grid back-projected through the mapping (H is px -> floor)
    try:
        Hinv = np.linalg.inv(mapping.homography)
    except np.linalg.LinAlgError:
        Hinv = None
    if Hinv is not None:
        size = mapping.floor_size_m
        gx = np.arange(0.0, size[0] + 1e-9, 0.5)
        gy = np.arange(0.0, size[1] + 1e-9, 0.5)
        for x in gx:
            line = np.stack([np.full_like(gy, x), gy], axis=1)
            _draw_floor_line(view, Hinv, line, (255, 140, 0), w)
        for y in gy:
            line = np.stack([gx, np.full_like(gx, y)], axis=1)
            _draw_floor_line(view, Hinv, line, (255, 140, 0), w)

    lines = [title,
             f"hfov {result.hfov_deg:.0f}deg  f {result.f_px:.0f}px  "
             f"height {plane.height_m:.2f} m",
             f"inliers {plane.inlier_ratio * 100:.0f}%  residual med "
             f"{plane.residual_median_m * 100:.1f} cm  p90 {plane.residual_p90_m * 100:.1f} cm",
             f"floor {mapping.floor_size_m[0]:.1f} x {mapping.floor_size_m[1]:.1f} m  "
             f"origin shift ({mapping.origin_shift_m[0]:.2f}, {mapping.origin_shift_m[1]:.2f})",
             "grid = 0.5 m (camera-derived room frame; test-grade)"]
    canvas = np.full((h, w + 380, 3), (28, 28, 32), np.uint8)
    canvas[:, :w] = view
    y = 26
    for line in lines:
        cv2.putText(canvas, line[:64], (w + 12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (220, 220, 220), 1, cv2.LINE_AA)
        y += 22

    # depth preview in the right column, under the metrics
    if depth_m is not None and np.isfinite(depth_m).any():
        d8 = np.clip((depth_m / max(0.1, float(np.nanpercentile(depth_m, 95)))) * 255.0,
                     0, 255).astype(np.uint8)
        prev = cv2.applyColorMap(d8, cv2.COLORMAP_TURBO)
        ph = max(80, int(prev.shape[0] * (380.0 / prev.shape[1])) // 3)
        prev = cv2.resize(prev, (380, ph), interpolation=cv2.INTER_AREA)
        ph = min(ph, h - y - 12)
        if ph > 40:
            canvas[y + 8:y + 8 + ph, w:w + 380] = prev[:ph]
            cv2.putText(canvas, "depth (m)", (w + 12, y + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def _draw_floor_line(view, Hinv, floor_pts, colour, w):
    pts = np.hstack([floor_pts, np.ones((len(floor_pts), 1))]) @ Hinv.T
    pts = pts[:, :2] / pts[:, 2:3]
    vis = pts[np.isfinite(pts).all(axis=1)]
    for a, b in zip(vis[:-1], vis[1:]):
        p0 = (int(round(a[0])), int(round(a[1])))
        p1 = (int(round(b[0])), int(round(b[1])))
        if max(abs(p0[0]), abs(p0[1])) < 10 * w:
            cv2.line(view, p0, p1, colour, 1, cv2.LINE_AA)


class DepthModel:
    """Depth-Anything-V2 metric depth on this host (lazy transformers import)."""

    def __init__(self, model_id: str = DEFAULT_DEPTH_MODEL, device: str = "auto"):
        self.model_id = model_id or DEFAULT_DEPTH_MODEL
        self.name = f"depth:{self.model_id.rsplit('/', 1)[-1]}"
        self.device = device
        self.load_s: float | None = None
        self.latencies_s: list[float] = []
        self._model = None
        self._processor = None
        self._torch = None
        self._device = ""
        self._load_error: str | None = None
        self._lock = threading.Lock()

    def infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Metric depth (H, W) float32 in metres, at the input frame's size."""
        self._ensure()
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        t0 = time.time()
        inputs = self._processor(images=rgb, return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        pred = self._processor.post_process_depth_estimation(
            outputs, target_sizes=[(h, w)])[0]["predicted_depth"]
        self.latencies_s.append(time.time() - t0)
        return pred.detach().cpu().numpy().astype(np.float32)

    def _ensure(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if self._load_error is not None:
                raise RuntimeError(self._load_error)
            try:
                import torch
                from transformers import AutoImageProcessor, AutoModelForDepthEstimation
            except ImportError as e:
                self._load_error = ("depth calibration needs torch+transformers+torchvision: "
                                    f"pip install -r requirements-vision.txt ({e})")
                raise RuntimeError(self._load_error) from e
            t0 = time.time()
            try:
                self._torch = torch
                if self.device == "auto":
                    self._device = "cuda" if torch.cuda.is_available() else "cpu"
                else:
                    self._device = self.device
                if self._device == "cpu":
                    torch.set_num_threads(min(4, torch.get_num_threads() or 4))
                self._processor = AutoImageProcessor.from_pretrained(self.model_id)
                self._model = AutoModelForDepthEstimation.from_pretrained(
                    self.model_id).to(self._device).eval()
                self.load_s = time.time() - t0
            except Exception as e:
                self._load_error = f"{type(e).__name__}: {e}"
                raise RuntimeError(self._load_error) from e
