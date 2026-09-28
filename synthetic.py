"""Synthetic top-down room renderer.

Produces frames that behave like a fixed overhead camera pointed at the floor:
planar scene, no distortion, known ground truth. The exact same perception
pipeline that runs on the real camera runs here, so the whole loop
(perception -> scene -> Jev -> control -> link) can be developed and tested
before any hardware is on the floor.

Used by ``run.py --source synthetic`` and by the test suite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from config import RoomConfig
from scene import Pose, wrap_deg

FLOOR_BGR = (168, 162, 150)     # warm grey floor
WALL_BGR = (74, 74, 78)
ROVER_BGR = (36, 36, 40)

# name, x0, y0, x1, y1 (room fractions), color BGR
# Layout guarantees >= ~0.6 m corridors for a 0.18 m rover with 0.22 m inflation:
# furniture hugs walls, one island, nothing creates sub-rover-width gaps.
FURNITURE_LAYOUT = (
    ("sofa", 0.06, 0.55, 0.16, 0.95, (44, 44, 138)),
    ("table", 0.40, 0.04, 0.58, 0.22, (58, 96, 58)),
    ("box", 0.66, 0.55, 0.74, 0.70, (52, 52, 56)),
    ("shelf", 0.88, 0.60, 0.96, 0.92, (104, 62, 38)),
)


@dataclass
class Truth:
    """Ground truth, for tests and optional debug overlays only."""

    rover: Pose
    furniture: list[tuple[str, float, float, float, float]]
    target: Pose | None


class SyntheticRoom:
    def __init__(self, cfg: RoomConfig, seed: int = 0, noise: float = 2.5):
        self.cfg = cfg
        self.px = float(cfg.synthetic.px_per_m)
        self.w = int(round(cfg.width_m * self.px))
        self.h = int(round(cfg.height_m * self.px))
        self.rng = np.random.default_rng(seed)
        self.noise = noise
        self.t = 0.0
        self.rover = Pose(x=cfg.width_m * 0.5, y=cfg.height_m * 0.5, yaw_deg=0.0)
        self.target: Pose | None = None
        if cfg.target.mode == "color":
            self.target = Pose(x=cfg.width_m * 0.25, y=cfg.height_m * 0.75, yaw_deg=0.0)
            self._target_dir = np.array([0.9, 0.7])
        self.furniture = [
            (name, x0 * cfg.width_m, y0 * cfg.height_m, x1 * cfg.width_m, y1 * cfg.height_m, color)
            for name, x0, y0, x1, y1, color in FURNITURE_LAYOUT
        ]
        self.dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        self._tag_px = 200
        self._tag = cv2.aruco.generateImageMarker(self.dictionary, cfg.rover.tag_id, self._tag_px)
        # Render the tag larger than life: pose comes from the calibration
        # homography (tag size does not enter the geometry), so size only
        # affects detectability. Measured on this renderer: a rotated 40 px
        # marker misses 8/24 angles, 50 px misses 0 and stays within ~2 deg.
        self._tag_draw_px = max(24, int(round(cfg.rover.tag_size_m * self.px * 2.5)))

    # ------------------------------------------------------------ geometry
    def world_to_px(self, x: float, y: float) -> tuple[float, float]:
        return x * self.px, (self.cfg.height_m - y) * self.px

    def px_to_world(self, px: float, py: float) -> tuple[float, float]:
        return px / self.px, self.cfg.height_m - py / self.px

    # ------------------------------------------------------------ dynamics
    def step(self, v_mps: float, w_deg_s: float, dt: float) -> None:
        """Integrate the rover with a differential-drive-ish model."""
        self.t += dt
        self.rover.yaw_deg = wrap_deg(self.rover.yaw_deg + w_deg_s * dt)
        yaw = math.radians(self.rover.yaw_deg)
        self.rover.x += v_mps * math.cos(yaw) * dt
        self.rover.y += v_mps * math.sin(yaw) * dt
        self.rover.x = min(max(self.rover.x, 0.05), self.cfg.width_m - 0.05)
        self.rover.y = min(max(self.rover.y, 0.05), self.cfg.height_m - 0.05)
        if self.target is not None:
            self.target.x += float(self._target_dir[0]) * 0.25 * dt
            self.target.y += float(self._target_dir[1]) * 0.25 * dt
            if not (0.1 < self.target.x < self.cfg.width_m - 0.1):
                self._target_dir[0] *= -1
            if not (0.1 < self.target.y < self.cfg.height_m - 0.1):
                self._target_dir[1] *= -1

    def truth(self) -> Truth:
        return Truth(self.rover, [(f[0], f[1], f[2], f[3], f[4]) for f in self.furniture],
                     self.target)

    # ------------------------------------------------------------ rendering
    def render(self) -> np.ndarray:
        frame = np.full((self.h, self.w, 3), FLOOR_BGR, np.uint8)
        cv2.rectangle(frame, (0, 0), (self.w - 1, self.h - 1), WALL_BGR, thickness=int(0.12 * self.px))

        for _, x0, y0, x1, y1, color in self.furniture:
            p0 = tuple(int(round(v)) for v in self.world_to_px(x0, y1))
            p1 = tuple(int(round(v)) for v in self.world_to_px(x1, y0))
            cv2.rectangle(frame, p0, p1, color, thickness=-1)
            cv2.rectangle(frame, p0, p1, (28, 28, 30), thickness=max(1, int(0.02 * self.px)))

        if self.target is not None:
            c = tuple(int(round(v)) for v in self.world_to_px(self.target.x, self.target.y))
            cv2.circle(frame, c, int(0.09 * self.px), (40, 200, 40), thickness=-1)

        # rover body, then the rotated AprilTag on top
        cx, cy = self.world_to_px(self.rover.x, self.rover.y)
        cv2.circle(frame, (int(round(cx)), int(round(cy))),
                   int(self.cfg.rover.footprint_radius_m * 0.85 * self.px), ROVER_BGR, -1)
        frame = self._paste_tag(frame, cx, cy, self.rover.yaw_deg)

        if self.noise > 0:
            frame = self._add_noise(frame)
        return frame

    def _add_noise(self, frame: np.ndarray) -> np.ndarray:
        """Sensor-noise at half resolution: 3x cheaper than full-res gaussians."""
        h, w = frame.shape[:2]
        small = self.rng.integers(-3, 4, size=(h // 2, w // 2, 3), dtype=np.int16)
        up = cv2.resize(small.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        out = frame.astype(np.int16)
        out += np.rint(up).astype(np.int16)
        return np.clip(out, 0, 255).astype(np.uint8)

    def _paste_tag(self, frame: np.ndarray, cx: float, cy: float, yaw_deg: float) -> np.ndarray:
        """Paste the marker with a white quiet zone, 'up' along rover forward.

        The quiet zone is not cosmetic: without a white margin around the black
        border, the marker cannot be segmented from a dark rover body and
        detection fails at intermediate rotation angles. Real tags need a
        printed or mounted white margin for exactly the same reason.
        """
        side = self._tag_draw_px
        pad = max(4, side // 10)                 # white quiet zone, rotates with the tag
        full = side + 2 * pad
        canvas = full + 8
        theta = math.radians(90.0 - yaw_deg)     # see module docstring / tests
        c, s = math.cos(theta), math.sin(theta)
        # Rotation applied to marker-local image coordinates (y down).
        rot = np.array([[c, -s], [s, c]], np.float32)
        # Padded-marker centre -> canvas centre, rotating about the centre.
        M = np.zeros((2, 3), np.float32)
        M[:, :2] = rot
        centre = np.array([canvas / 2.0, canvas / 2.0], np.float32)
        M[:, 2] = centre - rot @ np.array([full / 2.0, full / 2.0], np.float32)

        marker_gray = cv2.resize(self._tag, (side, side), interpolation=cv2.INTER_AREA)
        marker_gray = cv2.copyMakeBorder(marker_gray, pad, pad, pad, pad,
                                         cv2.BORDER_CONSTANT, value=255)
        marker = cv2.cvtColor(marker_gray, cv2.COLOR_GRAY2BGR)
        warped = cv2.warpAffine(marker, M, (canvas, canvas), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        mask = cv2.warpAffine(np.full((full, full), 255, np.uint8), M, (canvas, canvas),
                              flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        x0 = int(round(cx - canvas / 2.0))
        y0 = int(round(cy - canvas / 2.0))
        x0 = max(0, min(frame.shape[1] - canvas, x0))
        y0 = max(0, min(frame.shape[0] - canvas, y0))
        roi = frame[y0:y0 + canvas, x0:x0 + canvas]
        roi[mask > 0] = warped[mask > 0]
        return frame


def synthetic_homography(cfg: RoomConfig) -> tuple[list[list[float]], list[list[float]]]:
    """Image/world correspondences for a perfect top-down view of the room."""
    w, h = cfg.width_m, cfg.height_m
    px = cfg.synthetic.px_per_m
    image = [[0, 0], [w * px, 0], [w * px, h * px], [0, h * px]]
    world = [[0, h], [w, h], [w, 0], [0, 0]]
    return image, world
