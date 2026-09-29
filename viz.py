"""Debug rendering: what the camera sees, what the map believes, what Jev said.

One canvas, three views:
  * the camera frame with the belief overlay (grid warped through the same
    homography, so what you see on the floor is exactly what the map contains)
  * a top-down minimap of the whole room: grid, path, rover, tracks, target
  * a panel with the current judgment, its probabilities, and the command

Watch the overlay on the real camera during calibration: if the belief map
does not line up with the floor, nothing downstream can be trusted.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from config import RoomConfig
from link import Cmd
from scene import Scene, wrap_deg


def _panel(width: int, height: int, lines: list[tuple[str, tuple[int, int, int]]],
           bars: list[tuple[str, float]] | None = None) -> np.ndarray:
    img = np.full((height, width, 3), (28, 28, 32), np.uint8)
    y = 22
    for text, color in lines:
        cv2.putText(img, text[:80], (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        y += 19
    if bars:
        y += 4
        for name, p in bars[:5]:
            cv2.putText(img, f"{name[:16]:16s} {p:4.2f}", (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (200, 200, 200), 1, cv2.LINE_AA)
            w = int(p * (width - 170))
            cv2.rectangle(img, (150, y - 9), (150 + max(0, w), y - 1), (120, 200, 120), -1)
            y += 17
    return img


class Renderer:
    def __init__(self, cfg: RoomConfig, grid, show: bool = False, video_path: str | None = None,
                 fps: float = 15.0, panel_w: int = 430):
        self.cfg = cfg
        self.grid = grid
        self.show = show
        self.panel_w = panel_w
        self.fps = fps
        self.video_path = video_path
        self.writer = None                     # created on the first frame, real size
        self._warned = False

    # ------------------------------------------------------------- drawing
    def draw(self, frame: np.ndarray, scene: Scene, judg: dict, cmd: Cmd,
             path: list[tuple[float, float]] | None = None,
             truth_pose=None, target_truth=None) -> np.ndarray:
        """Render the HUD.

        Precondition: ``frame`` is ``Perception.frame_h`` (homography space,
        post-undistort) - the overlay warps the belief grid with the same
        homography, so a raw capture would misalign every overlay whenever
        camera intrinsics are configured.
        """
        view = frame.copy()
        H = _homography(self.cfg)
        Hin = np.linalg.inv(H)
        grid_rgb = self.grid.to_rgb(scene.t, self.cfg.grid.stale_s) if self.grid is not None else None

        # 1. belief grid warped into the camera view
        if grid_rgb is not None:
            g_img = _warp_grid_to_image(grid_rgb, self.cfg, Hin, view.shape[:2])
            view = cv2.addWeighted(view, 0.65, g_img, 0.35, 0)

        # 2. floor polygon + sector rays
        poly = np.asarray(self.cfg.floor.polygon_px, np.int32).reshape(-1, 1, 2)
        cv2.polylines(view, [poly], True, (80, 200, 255), 2)
        for bearing in (-90, -60, -30, 0, 30, 60, 90):
            free = _sector_free(scene, bearing)
            if free is None:
                continue
            rad = math.radians(scene.pose.yaw_deg + bearing)
            p1 = _world_to_px(Hin, scene.pose.x + free * math.cos(rad),
                              scene.pose.y + free * math.sin(rad))
            p0 = _world_to_px(Hin, scene.pose.x, scene.pose.y)
            color = (90, 220, 90) if bearing == 0 else (160, 160, 90)
            cv2.line(view, p0, p1, color, 2, cv2.LINE_AA)

        # 3. tracks, goal, target
        for tr in scene.tracks:
            p = _world_to_px(Hin, tr.x, tr.y)
            color = {"static": (60, 60, 220), "slow": (60, 200, 220), "moving": (0, 0, 255)}[tr.kind]
            cv2.circle(view, p, max(4, int(tr.radius_m * 100)), color, 2)
            cv2.putText(view, f"#{tr.id} {tr.kind[:3]}", (p[0] + 6, p[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
        if scene.goal.x is not None:
            pg = _world_to_px(Hin, scene.goal.x, scene.goal.y)
            pr = _world_to_px(Hin, scene.pose.x, scene.pose.y)
            cv2.arrowedLine(view, pr, pg, (255, 160, 60), 2, cv2.LINE_AA, tipLength=0.06)
        if scene.target.visible and scene.target.x is not None:
            pt = _world_to_px(Hin, scene.target.x, scene.target.y)
            cv2.drawMarker(view, pt, (60, 255, 60), cv2.MARKER_CROSS, 18, 2)

        # 4. rover marker
        pr = _world_to_px(Hin, scene.pose.x, scene.pose.y)
        rad = math.radians(scene.pose.yaw_deg)
        head = _world_to_px(Hin, scene.pose.x + 0.2 * math.cos(rad),
                            scene.pose.y + 0.2 * math.sin(rad))
        cv2.circle(view, pr, max(5, int(self.cfg.rover.footprint_radius_m * 100)), (255, 255, 255), 2)
        cv2.arrowedLine(view, pr, head, (255, 255, 255), 3, cv2.LINE_AA, tipLength=0.4)

        # 5. minimap + panel
        mm = self._minimap(scene, path, grid_rgb)
        h = view.shape[0]
        pad = np.full((h, self.panel_w, 3), (28, 28, 32), np.uint8)
        mh = min(mm.shape[0], h // 2)
        mw = int(mm.shape[1] * mh / mm.shape[0])
        mm = cv2.resize(mm, (mw, mh), interpolation=cv2.INTER_NEAREST)
        pad[:mh, :mw] = mm
        lines, bars = self._panel_lines(scene, judg, cmd)
        pad[mh:, :] = _panel(self.panel_w, h - mh, lines, bars)
        canvas = np.hstack([view, pad])
        return canvas

    def _minimap(self, scene: Scene, path: list[tuple[float, float]] | None,
                 grid_rgb: np.ndarray | None) -> np.ndarray:
        cell = self.cfg.grid.cell_m
        if grid_rgb is None:
            return np.zeros((10, 10, 3), np.uint8)
        img = np.flipud(grid_rgb)  # display orientation: y up
        scale = 2
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        if path:
            pts = [self._mm_pt(x, y, img.shape, cell, scale) for x, y in path]
            cv2.polylines(img, [np.asarray(pts).reshape(-1, 1, 2)], False, (255, 160, 60), 2)
        if scene.goal.x is not None:
            pg = self._mm_pt(scene.goal.x, scene.goal.y, img.shape, cell, scale)
            cv2.drawMarker(img, pg, (255, 160, 60), cv2.MARKER_DIAMOND, 12, 2)
        for tr in scene.tracks:
            p = self._mm_pt(tr.x, tr.y, img.shape, cell, scale)
            cv2.circle(img, p, max(3, int(tr.radius_m / cell * scale)), (60, 60, 220), 2)
        if scene.target.visible and scene.target.x is not None:
            p = self._mm_pt(scene.target.x, scene.target.y, img.shape, cell, scale)
            cv2.drawMarker(img, p, (60, 255, 60), cv2.MARKER_CROSS, 14, 2)
        pr = self._mm_pt(scene.pose.x, scene.pose.y, img.shape, cell, scale)
        rad = math.radians(scene.pose.yaw_deg)
        tip = (int(pr[0] + 18 * math.cos(rad)), int(pr[1] - 18 * math.sin(rad)))
        cv2.arrowedLine(img, pr, tip, (255, 255, 255), 2, cv2.LINE_AA, tipLength=0.5)
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (90, 90, 90), 1)
        return img

    @staticmethod
    def _mm_pt(x: float, y: float, shape, cell: float, scale: int) -> tuple[int, int]:
        return int(x / cell * scale), int(shape[0] - y / cell * scale)

    def _panel_lines(self, scene: Scene, judg: dict, cmd: Cmd):
        q = scene.quality
        hw = scene.hardware
        jets = (200, 200, 200)
        color = (140, 230, 140) if judg.get("source") == "jev" else (170, 170, 170)
        bars = [(k, v) for k, v in (judg.get("probabilities") or {}).items()]
        lines = [
            (f"t={scene.t:.1f}s  pose=({scene.pose.x:.2f},{scene.pose.y:.2f}) yaw={scene.pose.yaw_deg:+.0f}",
             jets),
            (f"pose_src={q.pose_source} age={q.pose_age_s:.2f}s  tag={'Y' if q.tag_visible else 'N'} "
             f"fps={q.fps:.0f}", jets),
            (f"occlusion={q.occlusion_risk:.2f} unknown_near={q.unknowns_near_rover}", jets),
            (f"nearest={scene.nearest_m} m @ {scene.nearest_bearing_deg}deg   "
             f"clear_ahead={scene.clear_ahead_m}", jets),
            (f"widest gap {scene.widest_run_deg:.0f}deg @ {scene.widest_run_center_deg} "
             f"clr={scene.widest_run_clearance_m}", jets),
            ("", jets),
            (f"JEV [{judg.get('source')}] age={judg.get('age_s')} "
             f"conf={judg.get('confidence')}", color),
            (f"risk={judg.get('risk')}  stuck={judg.get('truly_stuck')}  "
             f"blocked={judg.get('path_obstructed')}  unreliable={judg.get('observation_unreliable')}",
             jets),
            (f"goal={scene.goal.name or scene.goal.type} prog={scene.goal.progress_s:.0f}s "
             f"noprog={scene.dynamics.no_progress_s:.1f}s path={'ok' if scene.goal.path_valid else '-'}",
             jets),
            (f"CMD v={cmd.v_mps:+.2f} w={cmd.w_deg_s:+.0f}  [{cmd.source}]", (120, 200, 255)),
            (f"tof={hw.dist_front_m} m  wd={'ok' if hw.watchdog_ok else 'TRIPPED'} "
             f"batt={hw.batt_v} reflex={scene.dynamics.reflex_reason or hw.reflex}", jets),
        ]
        return lines, [(f"maneuver.{k}", v) for k, v in bars]

    # ------------------------------------------------------------ output
    def write(self, canvas: np.ndarray) -> None:
        if self.video_path and self.writer is None:
            self.writer = cv2.VideoWriter(self.video_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                          self.fps, (canvas.shape[1], canvas.shape[0]))
            if not self.writer.isOpened():
                print(f"[viz] cannot open video writer for {self.video_path}; "
                      f"continuing without recording")
                self.video_path = None
                self.writer = None
        if self.writer is not None:
            self.writer.write(canvas)
        if self.show:
            try:
                cv2.imshow("jev-rover", canvas)
                cv2.waitKey(1)
            except cv2.error:
                if not self._warned:
                    print("display unavailable; continuing without a window")
                    self._warned = True
                self.show = False

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()


# ------------------------------------------------------------------ helpers

_H_CACHE: dict[int, np.ndarray] = {}


def _homography(cfg: RoomConfig) -> np.ndarray:
    key = id(cfg)
    if key not in _H_CACHE:
        from perception import Homography
        _H_CACHE[key] = Homography(cfg.homography.image_points_px,
                                   cfg.homography.world_points_m).H
    return _H_CACHE[key]


def _warp_grid_to_image(grid_rgb: np.ndarray, cfg: RoomConfig, Hinv: np.ndarray,
                        shape: tuple[int, int]) -> np.ndarray:
    gh, gw = grid_rgb.shape[:2]
    cell = cfg.grid.cell_m
    src = np.float32([[0, 0], [gw, 0], [gw, gh], [0, gh]])
    dst = np.float32([[0, cfg.height_m], [cfg.width_m, cfg.height_m],
                      [cfg.width_m, 0], [0, 0]])
    G = cv2.getPerspectiveTransform(src, dst)          # grid px -> world metres
    M = Hinv @ G                                        # grid px -> image px
    return cv2.warpPerspective(grid_rgb, M, (shape[1], shape[0]),
                               flags=cv2.INTER_NEAREST, borderValue=(28, 28, 32))


def _sector_free(scene: Scene, bearing: float) -> float | None:
    from scene import sector_name
    return scene.sector(sector_name(bearing))


def _world_to_px(Hinv: np.ndarray, x: float, y: float) -> tuple[int, int]:
    p = Hinv @ np.array([x, y, 1.0])
    return int(round(p[0] / p[2])), int(round(p[1] / p[2]))
