"""Fixed-camera perception: pixels -> Scene.

Pipeline per frame:
  1. optional undistort (if ``calibrate.py intrinsics`` was run)
  2. AprilTag on the rover -> world pose, straight from the floor homography.
     Because the rover drives on the same plane the homography is defined on,
     this is an exact 2D pose with no dead reckoning and no scale ambiguity.
  3. floor model -> free pixels; everything else inside the room polygon is an
     obstacle candidate (rover body and target are masked out).
  4. log-odds occupancy grid -> persistent map; unseen cells are *unknown*,
     not free, and rays report which one stopped them.
  5. blob tracker -> tracks with velocity (people, pets, the follow target).
  6. ray fan -> rover-frame sectors and gaps (``scene.summarize_rays``).
  7. Scene assembly with honest quality fields (ages, occlusion risk).

Nothing here calls the network and nothing here talks to the rover. It is a
pure function of (frame, time): the hardware never sees a pixel decision.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from config import CameraConfig, FloorConfig, RoomConfig, TargetConfig
from scene import (
    ObstacleTrack,
    PerceptionQuality,
    Pose,
    Scene,
    TargetObs,
    Twist,
    build_scene,
    rover_frame,
    summarize_rays,
    wrap_deg,
)


# --------------------------------------------------------------------------- camera

def open_source(source: str, width: int = 0, height: int = 0) -> cv2.VideoCapture:
    """Open a camera index, device path, stream URL or video file."""
    src: int | str = int(source) if source.isdigit() else source
    use_v4l2 = isinstance(src, int) or (isinstance(src, str) and src.startswith("/dev/video"))
    cap = cv2.VideoCapture(src, cv2.CAP_V4L2 if use_v4l2 else cv2.CAP_ANY)
    if not cap.isOpened():
        return cap
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if use_v4l2:
        # MJPG keeps 1080p USB cameras at full frame rate on USB 2.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class Camera:
    def __init__(self, cfg: CameraConfig):
        self.cap = open_source(cfg.source, cfg.width, cfg.height)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera source {cfg.source!r}")
        self.fps = 0.0
        self._t = None

    def read(self) -> tuple[bool, np.ndarray | None, float]:
        """Return (ok, frame, frame_age_s)."""
        t0 = time.time()
        ok, frame = self.cap.read()
        age = time.time() - t0
        if ok:
            now = time.time()
            if self._t is not None and now > self._t:
                inst = 1.0 / (now - self._t)
                self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
            self._t = now
        return ok, frame, age

    def release(self) -> None:
        self.cap.release()


def load_intrinsics(path: str) -> tuple[np.ndarray, np.ndarray] | None:
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return (np.asarray(data["camera_matrix"], float), np.asarray(data["distortion"], float))


# --------------------------------------------------------------------------- geometry

class Homography:
    """Planar floor mapping: image pixels <-> room metres."""

    def __init__(self, image_points_px: list, world_points_m: list):
        src = np.asarray(image_points_px, np.float32)
        dst = np.asarray(world_points_m, np.float32)
        if len(src) == 4:
            H = cv2.getPerspectiveTransform(src, dst)
        else:
            H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.05)
        if H is None:
            raise ValueError("homography could not be computed from the given points")
        self.H = H.astype(np.float64)
        self.Hinv = np.linalg.inv(self.H)

    @classmethod
    def for_scale(cls, base: "Homography", scale: float) -> "Homography":
        """Homography for a frame downscaled by ``scale`` (e.g. 0.5)."""
        obj = cls.__new__(cls)
        obj.H = base.H @ np.diag([1.0 / scale, 1.0 / scale, 1.0])
        obj.Hinv = np.linalg.inv(obj.H)
        return obj

    def _apply(self, M: np.ndarray, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        hom = np.hstack([pts, np.ones((len(pts), 1))]) @ M.T
        return hom[:, :2] / hom[:, 2:3]

    def img_to_world(self, pts: np.ndarray) -> np.ndarray:
        return self._apply(self.H, pts)

    def world_to_img(self, pts: np.ndarray) -> np.ndarray:
        return self._apply(self.Hinv, pts)

    def error_m(self, image_points_px: list, world_points_m: list) -> float:
        got = self.img_to_world(np.asarray(image_points_px, float))
        ref = np.asarray(world_points_m, float)
        return float(np.mean(np.linalg.norm(got - ref, axis=1)))


@dataclass
class SemanticContext:
    """Read-only snapshot handed to the semantics worker.

    The main thread mutates ``Perception`` at camera rate; the worker must never
    touch it. Arrays are copies and the homography is immutable after
    construction, so projection can run off-thread without locks.
    """

    homography: Homography
    room_w_m: float
    room_h_m: float
    cell_m: float
    polygon_px: np.ndarray          # full-res floor polygon, (N, 2)
    floor_lab: np.ndarray | None    # sampled floor colour (None until first classify)
    floor_lab_tolerance: float
    grid_log_odds: np.ndarray
    grid_last_seen: np.ndarray
    grid_t: float
    occupied_thr: float
    stale_s: float
    camera_w: int = 0               # configured capture size; 0 = unknown, no check
    camera_h: int = 0

    def occupied(self) -> np.ndarray:
        return self.grid_log_odds > self.occupied_thr

    def observed(self) -> np.ndarray:
        return (self.grid_t - self.grid_last_seen) <= self.stale_s

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        h, w = self.grid_log_odds.shape
        ix, iy = int(x / self.cell_m), int(y / self.cell_m)
        if 0 <= ix < w and 0 <= iy < h:
            return ix, iy
        return None


# --------------------------------------------------------------------------- tags

@dataclass
class TagFix:
    x: float
    y: float
    yaw_deg: float
    center_px: tuple[float, float]
    area_px: float
    corners_world: np.ndarray


class TagDetector:
    """AprilTag detection with subpixel corners for a stable pose."""

    def __init__(self, tag_id: int, yaw_offset_deg: float = 0.0):
        self.tag_id = int(tag_id)
        self.yaw_offset_deg = float(yaw_offset_deg)
        self.dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        params.adaptiveThreshWinSizeMin = 5
        params.adaptiveThreshWinSizeMax = 35
        params.adaptiveThreshWinSizeStep = 6
        self.detector = cv2.aruco.ArucoDetector(self.dictionary, params)

    def detect(self, frame: np.ndarray) -> np.ndarray | None:
        corners, ids, _ = self.detector.detectMarkers(frame)
        if ids is None:
            return None
        for i, tid in enumerate(ids.ravel()):
            if int(tid) == self.tag_id:
                return corners[i].reshape(4, 2).astype(np.float64)
        return None

    def pose_from_corners(self, corners_px: np.ndarray, H: Homography) -> TagFix:
        """Planar pose from the marker quad.

        Rover forward is the marker's printed 'up': the direction from the
        bottom-edge midpoint to the top-edge midpoint. Corner order from ArUco
        is [top_left, top_right, bottom_right, bottom_left] as seen in image.
        """
        cw = H.img_to_world(corners_px)
        center = cw.mean(axis=0)
        top_mid = 0.5 * (cw[0] + cw[1])
        bottom_mid = 0.5 * (cw[3] + cw[2])
        fwd = top_mid - bottom_mid
        yaw = math.degrees(math.atan2(fwd[1], fwd[0])) + self.yaw_offset_deg
        center_px = tuple(np.mean(corners_px, axis=0))
        area_px = float(cv2.contourArea(corners_px.astype(np.float32)))
        return TagFix(float(center[0]), float(center[1]), wrap_deg(yaw), center_px,
                      area_px, cw)


# --------------------------------------------------------------------------- floor

class FloorModel:
    """Classifies pixels as floor / not-floor inside the room polygon.

    The floor color is sampled once from the calibration polygon and can adapt
    slowly (``floor.adapt``) for daylight drift. An optional empty-room
    background image catches shadows and object transitions the color model
    alone would miss.

    ``polygon_px`` is in the *configured camera resolution*; the polygon is
    scaled to whatever frame size is actually handed to ``classify`` (the
    perception pipeline works on a downscaled frame).
    """

    def __init__(self, cfg: FloorConfig, ref_size: tuple[int, int] = (0, 0)):
        self.cfg = cfg
        self.ref_w, self.ref_h = int(ref_size[0]), int(ref_size[1])
        self.polygon = np.asarray(cfg.polygon_px, np.float32).reshape(-1, 1, 2)
        self.color_lab: np.ndarray | None = None
        self.background = cv2.imread(cfg.background, cv2.IMREAD_GRAYSCALE) if cfg.background else None

    def _scaled_polygon(self, shape: tuple[int, int]) -> np.ndarray:
        h, w = shape
        if self.ref_w and self.ref_h and (w != self.ref_w or h != self.ref_h):
            poly = self.polygon.copy()
            poly[:, 0, 0] *= w / self.ref_w
            poly[:, 0, 1] *= h / self.ref_h
            return poly
        return self.polygon

    def classify(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (floor_mask, notfloor_mask), both uint8 with 255 where true."""
        h, w = frame.shape[:2]
        poly = np.zeros((h, w), np.uint8)
        cv2.fillPoly(poly, [self._scaled_polygon((h, w)).astype(np.int32)], 255)

        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
        if self.color_lab is None:
            inside = lab[poly > 0].reshape(-1, 3)
            if len(inside) == 0:
                return np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)
            self.color_lab = np.median(inside, axis=0)

        dist = np.linalg.norm(lab - self.color_lab, axis=2)
        floor = ((dist < self.cfg.lab_tolerance) & (poly > 0)).astype(np.uint8) * 255

        if self.background is not None:
            bg = self.background
            if bg.shape != (h, w):
                bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(gray, bg)
            floor[(diff > self.cfg.bg_tolerance) & (poly > 0)] = 0

        if self.cfg.adapt:
            confident = (dist < self.cfg.lab_tolerance * 0.6) & (poly > 0)
            if int(confident.sum()) > 500:
                med = np.median(lab[confident].reshape(-1, 3), axis=0)
                self.color_lab = 0.98 * self.color_lab + 0.02 * med

        notfloor = ((floor == 0) & (poly > 0)).astype(np.uint8) * 255
        return floor, notfloor


# --------------------------------------------------------------------------- grid

class OccupancyGrid:
    """Log-odds occupancy grid with explicit unknown cells."""

    OCCUPIED_THR = 0.42   # log-odds above which a cell counts as occupied

    def __init__(self, width_m: float, height_m: float, cell_m: float = 0.05):
        self.cell_m = float(cell_m)
        self.w = int(math.ceil(width_m / cell_m))
        self.h = int(math.ceil(height_m / cell_m))
        self.log_odds = np.zeros((self.h, self.w), np.float32)
        self.last_seen = np.full((self.h, self.w), -1e9, np.float32)
        ys, xs = np.mgrid[0:self.h, 0:self.w]
        self.cell_cx = (xs + 0.5) * self.cell_m
        self.cell_cy = (ys + 0.5) * self.cell_m

    # -- writes -------------------------------------------------------------
    def clear_disc(self, x: float, y: float, radius_m: float, t: float) -> None:
        """Forget everything inside a disc: the rover's own footprint.

        The rover's body is a sensor blind spot, not an obstacle. Without this,
        its pixels are stamped into the grid before the first pose fix exists
        and can never be re-observed as free, so the rover becomes its own
        obstacle at the exact cells where rays start.
        """
        ix0 = max(0, int((x - radius_m) / self.cell_m))
        ix1 = min(self.w - 1, int((x + radius_m) / self.cell_m))
        iy0 = max(0, int((y - radius_m) / self.cell_m))
        iy1 = min(self.h - 1, int((y + radius_m) / self.cell_m))
        for iy in range(iy0, iy1 + 1):
            xs = np.arange(ix0, ix1 + 1)
            cx = (xs + 0.5) * self.cell_m
            cy = (iy + 0.5) * self.cell_m
            inside = (cx - x) ** 2 + (cy - y) ** 2 <= radius_m ** 2
            idx = xs[inside]
            if len(idx):
                self.log_odds[iy, idx] = 0.0
                self.last_seen[iy, idx] = t

    def set_occupied(self, mask: np.ndarray, t: float, log_odds: float = 3.2) -> None:
        """Force a boolean cell mask to occupied (used for the room's wall ring)."""
        self.log_odds[mask] = np.maximum(self.log_odds[mask], log_odds)
        self.last_seen[mask] = t

    def update(self, free_pts: np.ndarray, occ_pts: np.ndarray, t: float,
               free_delta: float = -0.45, occ_delta: float = 0.65) -> None:
        for pts, delta in ((occ_pts, occ_delta), (free_pts, free_delta)):
            if pts is None or len(pts) == 0:
                continue
            arr = np.asarray(pts)
            ix = np.floor(arr[:, 0] / self.cell_m).astype(np.int64)
            iy = np.floor(arr[:, 1] / self.cell_m).astype(np.int64)
            valid = (ix >= 0) & (ix < self.w) & (iy >= 0) & (iy < self.h)
            ix, iy = ix[valid], iy[valid]
            if len(ix) == 0:
                continue
            # bincount over cell ids: much faster than np.add.at at ~100k points.
            flat = iy * self.w + ix
            counts = np.bincount(flat, minlength=self.h * self.w).reshape(self.h, self.w)
            self.log_odds += delta * counts.astype(np.float32)
            self.last_seen[iy, ix] = t
        np.clip(self.log_odds, -3.0, 4.5, out=self.log_odds)

    # -- reads --------------------------------------------------------------
    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """Immutable copies of (log_odds, last_seen) for hand-off to workers."""
        return self.log_odds.copy(), self.last_seen.copy()

    def occupied(self, thr: float | None = None) -> np.ndarray:
        return self.log_odds > (self.OCCUPIED_THR if thr is None else thr)

    def observed(self, t: float, stale_s: float) -> np.ndarray:
        return (t - self.last_seen) <= stale_s

    def inflated_occupied(self, radius_m: float) -> np.ndarray:
        occ = self.occupied().astype(np.uint8)
        r = max(1, int(round(radius_m / self.cell_m)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        return cv2.dilate(occ, kernel).astype(bool)

    def to_rgb(self, t: float, stale_s: float) -> np.ndarray:
        """Debug/minimap image in world orientation (row 0 = bottom)."""
        prob = 1.0 / (1.0 + np.exp(-self.log_odds))
        img = np.zeros((self.h, self.w, 3), np.uint8)
        img[:] = (70, 70, 70)                                   # unknown grey
        seen = self.observed(t, stale_s)
        img[seen] = (235, 235, 235)                             # observed free
        occ = self.occupied()
        img[occ] = (40, 40, 40)                                 # occupied
        return np.flipud(img)

    # -- rays ---------------------------------------------------------------
    def ray_distances(self, x: float, y: float, yaw_deg: float, angles_deg: np.ndarray,
                      t: float, max_range_m: float, stale_s: float,
                      start_offset_m: float = 0.0) -> tuple[np.ndarray, list[str]]:
        """Marched free distances for a fan of bearings (rover frame, left positive).

        Stops at occupied cells ("obstacle") and at cells with no recent
        observation ("unseen"); otherwise the model range cap ("range").
        Vectorized: all rays marched in one array operation.
        """
        occ = self.occupied()
        unseen = ~self.observed(t, stale_s)
        stop = occ | unseen
        kind = np.where(occ, 1, 2).astype(np.int8)      # 1 obstacle, 2 unseen

        step = self.cell_m * 0.5
        n = int(math.ceil(max(0.0, max_range_m - start_offset_m) / step)) + 1
        ds = start_offset_m + np.arange(n, dtype=np.float64) * step
        ang = np.radians(yaw_deg + np.asarray(angles_deg, dtype=np.float64))
        px = x + np.cos(ang)[:, None] * ds[None, :]
        py = y + np.sin(ang)[:, None] * ds[None, :]
        ix = (px / self.cell_m).astype(np.int32)
        iy = (py / self.cell_m).astype(np.int32)
        inside = (ix >= 0) & (ix < self.w) & (iy >= 0) & (iy < self.h)
        np.clip(ix, 0, self.w - 1, out=ix)
        np.clip(iy, 0, self.h - 1, out=iy)
        hit = stop[iy, ix] & inside
        found = hit.any(axis=1)
        first = np.argmax(hit, axis=1)
        rows = np.arange(len(ang))

        dists = np.where(found, ds[first], max_range_m).astype(np.float32)
        kinds = np.where(found, kind[iy[rows, first], ix[rows, first]], 0)
        name = {1: "obstacle", 2: "unseen"}
        limits = [name.get(int(k), "range") for k in kinds]
        return dists, limits


# --------------------------------------------------------------------------- blobs

@dataclass
class _Track:
    id: int
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 0.05
    area: float = 0.0
    first_t: float = 0.0
    last_t: float = 0.0
    hist: deque = field(default_factory=lambda: deque(maxlen=32))


class BlobTracker:
    """Clusters current-frame obstacle points into persistent tracks with velocity."""

    def __init__(self, width_m: float, height_m: float, cell_m: float = 0.04,
                 max_age_s: float = 1.2, match_m: float = 0.55,
                 min_area_m2: float = 0.004):
        self.cell_m = cell_m
        self.w = int(math.ceil(width_m / cell_m))
        self.h = int(math.ceil(height_m / cell_m))
        self.max_age_s = max_age_s
        self.match_m = match_m
        self.min_area_m2 = min_area_m2
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1

    def _blobs(self, pts: np.ndarray, exclude: tuple[float, float, float] | None):
        mask = np.zeros((self.h, self.w), np.uint8)
        if pts is None or len(pts) == 0:
            return []
        ix = np.floor(pts[:, 0] / self.cell_m).astype(np.int32)
        iy = np.floor(pts[:, 1] / self.cell_m).astype(np.int32)
        ok = (ix >= 0) & (ix < self.w) & (iy >= 0) & (iy < self.h)
        mask[iy[ok], ix[ok]] = 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        ex, ey, er = exclude if exclude else (None, None, 0.0)
        for i in range(1, n):
            area = float(stats[i, cv2.CC_STAT_AREA]) * self.cell_m ** 2
            if area < self.min_area_m2:
                continue
            cx = float(centroids[i][0]) * self.cell_m
            cy = float(centroids[i][1]) * self.cell_m
            if ex is not None and math.hypot(cx - ex, cy - ey) < er:
                continue  # rover body / shadow halo
            radius = math.sqrt(area / math.pi)
            out.append((cx, cy, area, radius, i))
        return out

    def update(self, pts: np.ndarray, t: float,
               exclude: tuple[float, float, float] | None = None) -> list[ObstacleTrack]:
        blobs = self._blobs(pts, exclude)
        unmatched = set(self._tracks)
        result: list[ObstacleTrack] = []

        for cx, cy, area, radius, label in blobs:
            best_id, best_d = None, self.match_m
            for tid in unmatched:
                tr = self._tracks[tid]
                d = math.hypot(cx - tr.x, cy - tr.y)
                if d < best_d:
                    best_id, best_d = tid, d
            if best_id is None:
                tid = self._next_id
                self._next_id += 1
                tr = _Track(id=tid, x=cx, y=cy, first_t=t, last_t=t)
                self._tracks[tid] = tr
            else:
                tr = self._tracks[best_id]
                tr.x, tr.y, tr.last_t = cx, cy, t
                unmatched.discard(best_id)
            tr.area, tr.radius, tr.last_t = area, radius, t
            tr.hist.append((t, cx, cy))

            # Velocity from a >= 0.3 s baseline; one 15 Hz frame turns pixel
            # noise into metres per second.
            if len(tr.hist) >= 2 and tr.hist[-1][0] - tr.hist[0][0] >= 0.3:
                (t0, x0, y0), (t1, x1, y1) = tr.hist[0], tr.hist[-1]
                vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
                tr.vx = 0.6 * tr.vx + 0.4 * vx
                tr.vy = 0.6 * tr.vy + 0.4 * vy

            speed = math.hypot(tr.vx, tr.vy)
            kind = "moving" if speed > 0.12 else ("slow" if speed > 0.03 else "static")
            result.append(ObstacleTrack(
                id=tr.id, x=round(tr.x, 2), y=round(tr.y, 2),
                vx=round(tr.vx, 2), vy=round(tr.vy, 2),
                radius_m=round(tr.radius, 2), area_m2=round(tr.area, 3),
                bearing_deg=0.0, range_m=0.0, kind=kind,
                age_s=round(t - tr.first_t, 1), seen_s_ago=round(t - tr.last_t, 2),
            ))

        for tid in list(self._tracks):
            if t - self._tracks[tid].last_t > self.max_age_s:
                del self._tracks[tid]
        return result


# --------------------------------------------------------------------------- target

class TargetTracker:
    """Tracks the follow target, either a second AprilTag or a colored object."""

    def __init__(self, cfg: TargetConfig):
        self.cfg = cfg
        self.detector = TagDetector(cfg.tag_id) if cfg.mode == "tag" else None
        self.last_world: tuple[float, float] | None = None
        self.last_t = -1e9
        self.vx = 0.0
        self.vy = 0.0
        self.hist: deque = deque(maxlen=32)
        self._hsv_lo = np.array(cfg.hsv_lo, np.uint8)
        self._hsv_hi = np.array(cfg.hsv_hi, np.uint8)

    def update(self, frame: np.ndarray, H: Homography, t: float) -> tuple[dict, np.ndarray]:
        """Return (target info dict, target mask uint8)."""
        h, w = frame.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        world: tuple[float, float] | None = None

        if self.cfg.mode == "tag" and self.detector is not None:
            corners = self.detector.detect(frame)
            if corners is not None:
                fix = self.detector.pose_from_corners(corners, H)
                world = (fix.x, fix.y)
                cv2.fillPoly(mask, [corners.astype(np.int32)], 255)
                mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
        elif self.cfg.mode == "color":
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            raw = cv2.inRange(hsv, self._hsv_lo, self._hsv_hi)
            raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
            raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            contours, _ = cv2.findContours(raw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                big = max(contours, key=cv2.contourArea)
                if cv2.contourArea(big) >= self.cfg.min_area_px:
                    m = np.zeros((h, w), np.uint8)
                    cv2.drawContours(m, [big], -1, 255, -1)
                    mask = cv2.dilate(m, np.ones((5, 5), np.uint8))
                    mom = cv2.moments(big)
                    if mom["m00"] > 0:
                        px = mom["m10"] / mom["m00"]
                        py = mom["m01"] / mom["m00"]
                        wx, wy = H.img_to_world(np.array([[px, py]]))[0]
                        world = (float(wx), float(wy))

        visible = world is not None
        if visible:
            if self.last_world is not None and t - self.last_t >= 0.05:
                dt = t - self.last_t
                self.hist.append((t, *world))
                if len(self.hist) >= 2 and self.hist[-1][0] - self.hist[0][0] >= 0.3:
                    (t0, x0, y0), (t1, x1, y1) = self.hist[0], self.hist[-1]
                    self.vx = 0.6 * self.vx + 0.4 * (x1 - x0) / (t1 - t0)
                    self.vy = 0.6 * self.vy + 0.4 * (y1 - y0) / (t1 - t0)
                del dt
            self.last_world, self.last_t = world, t
        return {
            "visible": visible,
            "world": world,
            "vx": self.vx if visible else 0.0,
            "vy": self.vy if visible else 0.0,
            "unseen_for_s": 0.0 if visible else round(t - self.last_t, 1),
        }, mask


# --------------------------------------------------------------------------- estimator

class RoverEstimator:
    """Tag fixes when visible; short-horizon dead reckoning when not."""

    def __init__(self, footprint_radius_m: float):
        self.pose: Pose | None = None
        self.vel = np.zeros(2)
        self.yaw_rate = 0.0
        self.last_fix_t = -1e9
        self.last_t = 0.0
        self.hist: deque = deque(maxlen=64)
        self.footprint_radius_m = footprint_radius_m

    def update(self, fix: TagFix | None, t: float, cmd_v: float, cmd_w_deg_s: float) -> None:
        dt = t - self.last_t
        if fix is not None:
            if self.pose is None:
                self.pose = Pose(round(fix.x, 3), round(fix.y, 3), round(fix.yaw_deg, 1))
            else:
                a = 0.55
                self.pose.x += a * (fix.x - self.pose.x)
                self.pose.y += a * (fix.y - self.pose.y)
                self.pose.yaw_deg = wrap_deg(self.pose.yaw_deg + a * wrap_deg(fix.yaw_deg - self.pose.yaw_deg))
            self.hist.append((t, fix.x, fix.y, fix.yaw_deg))
            if len(self.hist) >= 2 and self.hist[-1][0] - self.hist[0][0] >= 0.3:
                (t0, x0, y0, y0d), (t1, x1, y1, y1d) = self.hist[0], self.hist[-1]
                vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
                self.vel = 0.6 * self.vel + 0.4 * np.array([vx, vy])
                self.yaw_rate = 0.6 * self.yaw_rate + 0.4 * wrap_deg(y1d - y0d) / (t1 - t0)
            self.last_fix_t = t
        elif self.pose is not None and 0.0 < dt < 0.5 and cmd_v != 0.0:
            yaw = math.radians(self.pose.yaw_deg + cmd_w_deg_s * dt * 0.5)
            self.pose.x += cmd_v * math.cos(yaw) * dt
            self.pose.y += cmd_v * math.sin(yaw) * dt
            self.pose.yaw_deg = wrap_deg(self.pose.yaw_deg + cmd_w_deg_s * dt)
        self.last_t = t

    def state(self, t: float) -> tuple[Pose | None, str, float]:
        if self.pose is None:
            return None, "lost", t - self.last_fix_t
        age = t - self.last_fix_t
        source = "tag" if age <= 0.35 else ("dead_reckon" if age <= 5.0 else "lost")
        return self.pose, source, age

    def twist(self, pose: Pose | None) -> Twist:
        if pose is None:
            return Twist()
        yaw = math.radians(pose.yaw_deg)
        v = float(self.vel[0] * math.cos(yaw) + self.vel[1] * math.sin(yaw))
        return Twist(v_mps=round(v, 2), w_deg_s=round(self.yaw_rate, 1))


# --------------------------------------------------------------------------- perception

class Perception:
    """Owns the calibration, the map and the trackers. Feed it frames."""

    def __init__(self, cfg: RoomConfig, proc_scale: float = 0.5):
        self.cfg = cfg
        self.homography = Homography(cfg.homography.image_points_px, cfg.homography.world_points_m)
        self.proc_scale = proc_scale
        self.homography_small = Homography.for_scale(self.homography, proc_scale)
        self.tag = TagDetector(cfg.rover.tag_id, cfg.rover.tag_yaw_offset_deg)
        self.floor = FloorModel(cfg.floor, (cfg.camera.width, cfg.camera.height))
        self.grid = OccupancyGrid(cfg.width_m, cfg.height_m, cfg.grid.cell_m)
        self.blobs = BlobTracker(cfg.width_m, cfg.height_m)
        self.target = TargetTracker(cfg.target)
        self.est = RoverEstimator(cfg.rover.footprint_radius_m)
        self.angles = np.arange(-90.0, 90.01, 3.0)
        self.intrinsics = load_intrinsics(cfg.camera.intrinsics) if cfg.camera.intrinsics else None
        self.frame_id = 0
        self.fps = 0.0
        self._t_prev: float | None = None
        self.nogo = np.asarray(cfg.nogo, float) if cfg.nogo else np.zeros((0, 3))

        # Everything outside the floor polygon is wall: occupied, not unknown.
        # Without this, cells beyond the declared floor stay "unseen" forever
        # and rays stop in the middle of an empty room.
        poly_world = self.homography.img_to_world(np.asarray(cfg.floor.polygon_px, float))
        gx = np.round(poly_world[:, 0] / self.grid.cell_m).astype(np.int32)
        gy = np.round(poly_world[:, 1] / self.grid.cell_m).astype(np.int32)
        inside = np.zeros((self.grid.h, self.grid.w), np.uint8)
        cv2.fillPoly(inside, [np.column_stack([gx, gy])], 1)
        self._wall_mask = inside == 0

    # -- helpers ------------------------------------------------------------
    def _rover_mask(self, shape: tuple[int, int], pose: Pose | None,
                    H: Homography) -> np.ndarray:
        mask = np.zeros(shape, np.uint8)
        if pose is None:
            return mask
        r = self.cfg.rover.footprint_radius_m + self.cfg.rover.rover_mask_extra_m
        ang = np.linspace(0, 2 * math.pi, 16, endpoint=False)
        ring = np.column_stack([pose.x + r * np.cos(ang), pose.y + r * np.sin(ang)])
        px = H.world_to_img(ring)
        cv2.fillPoly(mask, [px.astype(np.int32)], 255)
        return mask

    def _nogo_hit(self, pose: Pose | None) -> bool:
        if pose is None or len(self.nogo) == 0:
            return False
        d = np.hypot(self.nogo[:, 0] - pose.x, self.nogo[:, 1] - pose.y)
        return bool(np.any(d < self.nogo[:, 2]))

    def semantic_context(self, t: float) -> SemanticContext:
        """Snapshot for the semantics worker; safe to hand to another thread."""
        lo, seen = self.grid.snapshot()
        poly = np.asarray(self.cfg.floor.polygon_px, np.float32).reshape(-1, 2).copy()
        lab = None if self.floor.color_lab is None else np.array(self.floor.color_lab, float)
        return SemanticContext(
            homography=self.homography,
            room_w_m=self.cfg.width_m,
            room_h_m=self.cfg.height_m,
            cell_m=self.grid.cell_m,
            polygon_px=poly,
            floor_lab=lab,
            floor_lab_tolerance=self.cfg.floor.lab_tolerance,
            grid_log_odds=lo,
            grid_last_seen=seen,
            grid_t=t,
            occupied_thr=self.grid.OCCUPIED_THR,
            stale_s=self.cfg.grid.stale_s,
            camera_w=int(self.cfg.camera.width),
            camera_h=int(self.cfg.camera.height),
        )

    # -- main ---------------------------------------------------------------
    def process(self, frame: np.ndarray, t: float, cmd_v: float = 0.0,
                cmd_w_deg_s: float = 0.0, frame_age_s: float = 0.0) -> Scene:
        self.frame_id += 1
        if self._t_prev is not None and t > self._t_prev:
            inst = 1.0 / (t - self._t_prev)
            self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
        self._t_prev = t

        if self.intrinsics is not None:
            frame = cv2.undistort(frame, self.intrinsics[0], self.intrinsics[1])

        # 1. pose from the rover tag
        corners = self.tag.detect(frame)
        fix = self.tag.pose_from_corners(corners, self.homography) if corners is not None else None
        self.est.update(fix, t, cmd_v, cmd_w_deg_s)
        pose, pose_source, pose_age = self.est.state(t)
        twist = self.est.twist(pose)

        # 2. floor / obstacle candidates at reduced resolution
        small = cv2.resize(frame, None, fx=self.proc_scale, fy=self.proc_scale,
                           interpolation=cv2.INTER_AREA)
        floor_m, notfloor_m = self.floor.classify(small)
        rover_m = self._rover_mask(small.shape[:2], pose, self.homography_small)
        target_info, target_m = self.target.update(small, self.homography_small, t)

        candidates = cv2.bitwise_and(notfloor_m, cv2.bitwise_not(rover_m))
        candidates = cv2.bitwise_and(candidates, cv2.bitwise_not(target_m))
        candidates = cv2.morphologyEx(candidates, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        # 3. world points -> occupancy grid
        step = 2
        free_pts = self._points_to_world(floor_m, self.homography_small, step)
        occ_pts = self._points_to_world(candidates, self.homography_small, step)
        self.grid.update(free_pts, occ_pts, t)
        if pose is not None:
            self.grid.clear_disc(pose.x, pose.y,
                                 self.cfg.rover.footprint_radius_m * 1.15, t)
        self.grid.set_occupied(self._wall_mask, t)

        # 4. tracks + rays
        exclude = None if pose is None else (
            pose.x, pose.y, self.cfg.rover.footprint_radius_m + self.cfg.rover.rover_mask_extra_m)
        tracks = self.blobs.update(occ_pts, t, exclude)
        if pose is not None:
            dists, limits = self.grid.ray_distances(
                pose.x, pose.y, pose.yaw_deg, self.angles, t,
                self.cfg.grid.max_range_m, self.cfg.grid.stale_s,
                start_offset_m=self.cfg.rover.footprint_radius_m * 1.05)
        else:
            dists = np.full(len(self.angles), self.cfg.grid.max_range_m, np.float32)
            limits = ["unseen"] * len(self.angles)

        blocked_at = self.cfg.rover.footprint_radius_m * 2.0 + 0.12
        rays = summarize_rays(
            self.angles.tolist(), dists.tolist(), limits=limits,
            free_threshold_m=blocked_at + 0.10, blocked_at_m=blocked_at)

        # 5. quality: what the map cannot see near the rover
        risk, near_unknown = self._occlusion(pose, t)
        for tr in tracks:
            if pose is not None:
                _, _, bearing, rng = rover_frame(tr.x - pose.x, tr.y - pose.y, pose.yaw_deg)
                tr.bearing_deg, tr.range_m = round(bearing, 1), round(rng, 2)
        tracks.sort(key=lambda tr: tr.range_m)

        target_obs = self._target_obs(target_info, pose)

        return build_scene(
            rays,
            t=round(t, 2), frame_id=self.frame_id,
            pose=Pose(round(pose.x, 3), round(pose.y, 3), round(pose.yaw_deg, 1)) if pose else Pose(),
            twist=twist,
            quality=PerceptionQuality(
                fps=round(self.fps, 1), frame_age_s=round(frame_age_s, 3),
                pose_source=pose_source, pose_age_s=round(pose_age, 2),
                tag_visible=fix is not None, occlusion_risk=round(risk, 2),
                unknowns_near_rover=near_unknown),
            tracks=tracks,
            nogo_hit=self._nogo_hit(pose),
            target=target_obs,
        )

    def _points_to_world(self, mask: np.ndarray, H: Homography, step: int) -> np.ndarray:
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return np.zeros((0, 2), np.float64)
        pts = np.column_stack([xs, ys]).astype(np.float64)[::step]
        return H.img_to_world(pts)

    def _occlusion(self, pose: Pose | None, t: float) -> tuple[float, int]:
        if pose is None:
            return 1.0, 0
        seen = self.grid.observed(t, self.cfg.grid.stale_s)
        d = np.hypot(self.grid.cell_cx - pose.x, self.grid.cell_cy - pose.y)
        ring = (d > 0.4) & (d < 1.5)
        total = int(ring.sum())
        if total == 0:
            return 0.0, 0
        unknown = int((ring & ~seen).sum())
        return unknown / total, unknown

    def _target_obs(self, info: dict, pose: Pose | None) -> TargetObs:
        world = info["world"]
        if world is None:
            return TargetObs(visible=False, mode=self.cfg.target.mode,
                             unseen_for_s=info["unseen_for_s"])
        if pose is None:
            return TargetObs(visible=True, mode=self.cfg.target.mode,
                             x=round(world[0], 2), y=round(world[1], 2))
        _, _, bearing, rng = rover_frame(world[0] - pose.x, world[1] - pose.y, pose.yaw_deg)
        return TargetObs(
            visible=True, mode=self.cfg.target.mode,
            bearing_deg=round(bearing, 1), range_m=round(rng, 2),
            x=round(world[0], 2), y=round(world[1], 2),
            vx=round(info["vx"], 2), vy=round(info["vy"], 2))
