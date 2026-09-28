"""Planner and executor: code decides, and code owns safety.

Layering, mirroring the drone project:

    reflex (this file + ESP32)   always wins, never asks Jev
    executor (this file)         turns a judgment (or the no-Jev baseline)
                                 into a stream of (v, w) commands
    planner  (this file)         A* on the inflated occupancy grid
    Jev      (tactics.py)        advisory maneuvers only

The baseline built in here - follow the path, brake for what is close, pick
the wider side - is deliberately the ablation condition: ``--no-jev`` runs this
file with the tactical layer switched off, which is what makes the comparison
in the README meaningful.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from config import RoomConfig
from link import Cmd
from perception import OccupancyGrid
from scene import Scene, wrap_deg
from tactics import THRESHOLDS

#: Maneuvers that are allowed to run for at most this long before the executor
#: falls back to path following and waits for a fresh judgment.
MODE_MAX_S = {
    "creep": 2.0,
    "back_and_turn": 1.5,
    "reacquire_goal": 3.0,
    "stop_and_wait": 2.5,
}


class Planner:
    """A* over the occupancy grid; unknown cells are allowed but expensive."""

    def __init__(self, grid: OccupancyGrid, blanket_radius_m: float, cfg: RoomConfig):
        self.grid = grid
        self.radius = blanket_radius_m
        self.cfg = cfg
        self.nogo_mask = np.zeros((grid.h, grid.w), bool)
        for x, y, r in (cfg.nogo or []):
            ix0, ix1 = max(0, int((x - r) / grid.cell_m)), min(grid.w, int((x + r) / grid.cell_m) + 1)
            iy0, iy1 = max(0, int((y - r) / grid.cell_m)), min(grid.h, int((y + r) / grid.cell_m) + 1)
            for iy in range(iy0, iy1):
                for ix in range(ix0, ix1):
                    if math.hypot((ix + 0.5) * grid.cell_m - x, (iy + 0.5) * grid.cell_m - y) <= r:
                        self.nogo_mask[iy, ix] = True

    def blocked(self) -> np.ndarray:
        return self.grid.inflated_occupied(self.radius) | self.nogo_mask

    def plan(self, start: tuple[float, float], goal: tuple[float, float],
             t: float) -> tuple[list[tuple[float, float]], bool]:
        """Return (world points, path_through_unknown). Empty list if impossible."""
        cell = self.grid.cell_m
        g = self.grid
        blocked = self.blocked()
        unknown = ~g.observed(t, self.cfg.grid.stale_s)

        def to_cell(p):
            return (min(max(int(p[0] / cell), 0), g.w - 1),
                    min(max(int(p[1] / cell), 0), g.h - 1))

        def free_near(ix, iy, max_r=6):
            if not blocked[iy, ix]:
                return ix, iy
            for r in range(1, max_r + 1):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if max(abs(dx), abs(dy)) != r:
                            continue
                        nx, ny = ix + dx, iy + dy
                        if 0 <= nx < g.w and 0 <= ny < g.h and not blocked[ny, nx]:
                            return nx, ny
            return None

        s = free_near(*to_cell(start))
        e = free_near(*to_cell(goal))
        if s is None or e is None:
            return [], False
        sx, sy = s
        ex, ey = e

        def h(ix, iy):
            dx, dy = abs(ix - ex), abs(iy - ey)
            return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)

        moves = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))
        open_heap = [(h(sx, sy), 0.0, sx, sy)]
        came: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
        cost = {(sx, sy): 0.0}
        found = False
        while open_heap:
            _, gc, ix, iy = heapq.heappop(open_heap)
            if (ix, iy) == (ex, ey):
                found = True
                break
            if gc > cost.get((ix, iy), math.inf):
                continue
            for dx, dy, w in moves:
                nx, ny = ix + dx, iy + dy
                if not (0 <= nx < g.w and 0 <= ny < g.h) or blocked[ny, nx]:
                    continue
                step = w * (3.0 if unknown[ny, nx] else 1.0)
                nc = gc + step
                if nc < cost.get((nx, ny), math.inf):
                    cost[(nx, ny)] = nc
                    came[(nx, ny)] = (ix, iy)
                    heapq.heappush(open_heap, (nc + h(nx, ny), nc, nx, ny))
        if not found:
            return [], False

        cells = []
        cur = (ex, ey)
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        cells.reverse()
        through_unknown = any(unknown[iy, ix] for ix, iy in cells)
        pts = [((ix + 0.5) * cell, (iy + 0.5) * cell) for ix, iy in cells]
        pts[-1] = (float(goal[0]), float(goal[1]))
        return self._smooth(pts, blocked), through_unknown

    def _smooth(self, pts: list[tuple[float, float]], blocked: np.ndarray) -> list[tuple[float, float]]:
        """Greedy line-of-sight shortcutting; never cuts a blocked cell."""
        if len(pts) <= 2:
            return pts
        cell = self.grid.cell_m
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self._clear(pts[i], pts[j], blocked, cell):
                j -= 1
            out.append(pts[j])
            i = j
        return out

    @staticmethod
    def _clear(a: tuple[float, float], b: tuple[float, float],
               blocked: np.ndarray, cell: float) -> bool:
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (cell * 0.5)))
        for k in range(1, n):
            x = a[0] + (b[0] - a[0]) * k / n
            y = a[1] + (b[1] - a[1]) * k / n
            ix, iy = int(x / cell), int(y / cell)
            if not (0 <= ix < blocked.shape[1] and 0 <= iy < blocked.shape[0]):
                return False
            if blocked[iy, ix]:
                return False
        return True


class Executor:
    def __init__(self, cfg: RoomConfig, grid: OccupancyGrid, require_telemetry: bool = False):
        self.cfg = cfg
        self.planner = Planner(grid, cfg.grid.inflation_m, cfg)
        self.require_telemetry = require_telemetry
        self.path: list[tuple[float, float]] = []
        self.path_through_unknown = False
        self.path_len_m: float | None = None
        self.last_plan_t = -1e9
        self.last_goal: tuple[float, float] | None = None
        self.mode: str | None = None
        self.mode_t = -1e9
        self.scan_dir = 1.0
        self.last_flip_t = -1e9
        self.commanded_v = 0.0
        self.no_progress_s = 0.0
        self.stats = {"modes": {}, "reflexes": {}, "replans": 0, "path_unknown": 0}

    # ------------------------------------------------------------- planning
    def _plan_fields(self, scene: Scene) -> None:
        """Publish the current plan to the scene EVERY frame.

        Replanning happens at most every 0.75 s, but the scene is rebuilt every
        perception frame. If the plan fields were only written on replan frames,
        the state Jev sees would advertise 'no path' most of the time and the
        model would correctly - but uselessly - keep asking to reacquire.
        """
        g = scene.goal
        g.path_valid = bool(self.path)
        g.path_through_unknown = self.path_through_unknown
        g.path_len_m = self.path_len_m
        if self.path:
            aim = self.path[-1]
            for pt in self.path:
                if math.hypot(pt[0] - scene.pose.x, pt[1] - scene.pose.y) >= 0.35:
                    aim = pt
                    break
            g.path_bearing_deg = round(self._bearing_to(scene, aim), 1)
        else:
            g.path_bearing_deg = None

    def _replan(self, scene: Scene, t: float) -> None:
        if scene.goal.x is None or scene.goal.y is None:
            self.path = []
            self.path_len_m = None
            return
        goal = (scene.goal.x, scene.goal.y)
        stale = t - self.last_plan_t > 0.75
        moved = self.last_goal is None or math.hypot(goal[0] - self.last_goal[0],
                                                      goal[1] - self.last_goal[1]) > 0.35
        if self.path and not stale and not moved:
            return
        self.path, self.path_through_unknown = self.planner.plan(
            (scene.pose.x, scene.pose.y), goal, t)
        self.path_len_m = round(sum(
            math.hypot(self.path[i + 1][0] - self.path[i][0],
                       self.path[i + 1][1] - self.path[i][1])
            for i in range(len(self.path) - 1)), 2) if len(self.path) > 1 else None
        self.last_plan_t = t
        self.last_goal = goal
        self.stats["replans"] += 1
        if self.path_through_unknown:
            self.stats["path_unknown"] += 1

    # -------------------------------------------------------------- helpers
    def _bearing_to(self, scene: Scene, target: tuple[float, float]) -> float:
        dx = target[0] - scene.pose.x
        dy = target[1] - scene.pose.y
        yaw = math.radians(scene.pose.yaw_deg)
        return wrap_deg(math.degrees(math.atan2(-dx * math.sin(yaw) + dy * math.cos(yaw),
                                                dx * math.cos(yaw) + dy * math.sin(yaw))))

    def _lookahead(self, scene: Scene, look_m: float) -> tuple[float, float] | None:
        if not self.path:
            return (scene.goal.x, scene.goal.y) if scene.goal.x is not None else None
        for pt in self.path:
            if math.hypot(pt[0] - scene.pose.x, pt[1] - scene.pose.y) >= look_m:
                return pt
        return self.path[-1]

    def _side_gap(self, scene: Scene, left: bool) -> float | None:
        """Center bearing of the widest verified gap on one side, if any."""
        best, best_span = None, 0.0
        for lo, hi, clearance in scene.free_runs_deg:
            if clearance < self.cfg.rover.footprint_radius_m * 2.0:
                continue
            lo2, hi2 = (lo, min(hi, 0.0)) if left else (max(lo, 0.0), hi)
            span = hi2 - lo2
            if span <= 1.0:
                continue
            if span > best_span:
                best, best_span = (lo2 + hi2) / 2.0, span
        return best

    def _side_clearance(self, scene: Scene, left: bool) -> float:
        names = ("near_left", "left", "far_left") if left else ("near_right", "right", "far_right")
        vals = [scene.sector(n) for n in names]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else 0.0

    def _follow(self, scene: Scene, speed_scale: float = 1.0,
                aim_override: tuple[float, float] | None = None,
                aim_bearing_deg: float | None = None) -> Cmd:
        cfg = self.cfg.rover
        aim = aim_override or self._lookahead(scene, 0.35 + 0.3 * cfg.max_speed_mps)
        if aim is None and aim_bearing_deg is None:
            return Cmd(0.0, 0.0, "path", "no waypoint")
        err = aim_bearing_deg if aim_bearing_deg is not None else self._bearing_to(scene, aim)
        w = max(-cfg.max_turn_deg_s, min(cfg.max_turn_deg_s, 2.2 * err))
        if abs(err) > 60.0:
            return Cmd(0.06, w, "path", f"turn {err:+.0f}deg")
        v = cfg.max_speed_mps * speed_scale * (1.0 - 0.65 * min(1.0, abs(err) / 90.0))
        return Cmd(max(0.06, v), w, "path", f"err {err:+.0f}deg")

    # ------------------------------------------------------------- baseline
    def _baseline(self, scene: Scene, t: float) -> Cmd:
        """No-Jev behavior: follow path; if blocked, turn to the wider side."""
        if scene.dynamics.no_progress_s > 4.0:
            self.mode, self.mode_t = "back_and_turn", t
            return Cmd(-self.cfg.rover.creep_speed_mps,
                       self.cfg.rover.max_turn_deg_s * 0.7, "baseline", "stuck")
        clear = scene.clear_ahead_m
        if clear is not None and clear < self.cfg.rover.footprint_radius_m * 2.2:
            left, right = self._side_clearance(scene, True), self._side_clearance(scene, False)
            turn = self.cfg.rover.max_turn_deg_s * 0.7 * (1 if left >= right else -1)
            return Cmd(0.0, turn, "baseline", "blocked: turn to wider side")
        return self._follow(scene)

    # ---------------------------------------------------------------- main
    def update(self, scene: Scene, judg: dict, t: float, dt: float) -> Cmd:
        cfg = self.cfg.rover
        fresh = judg.get("age_s") is not None and judg["age_s"] <= THRESHOLDS["stale_after_s"]
        use_jev = judg.get("source") == "jev" and fresh

        self._replan(scene, t)
        self._plan_fields(scene)

        # progress bookkeeping (used by Jev and by the baseline)
        obs_v = abs(scene.twist.v_mps)
        cmd_v = abs(self.commanded_v)
        if cmd_v > 0.05 and obs_v < 0.05:
            self.no_progress_s += dt
        else:
            self.no_progress_s = 0.0
        scene.dynamics.cmd_v_mps = round(self.commanded_v, 2)
        scene.dynamics.obs_v_mps = round(scene.twist.v_mps, 2)
        scene.dynamics.no_progress_s = round(self.no_progress_s, 1)
        scene.dynamics.slip_ratio = round(max(0.0, 1.0 - obs_v / cmd_v), 2) if cmd_v > 0.05 else 0.0
        scene.dynamics.blocked_forward = self.no_progress_s > 0.6

        # maneuver choice
        proposed = None
        if use_jev:
            proposed = judg["maneuver"]
        else:
            proposed = None

        if proposed is not None:
            if self.mode is None or proposed == self.mode:
                if self.mode is None:
                    self.mode, self.mode_t = proposed, t
            else:
                switch = (t - self.mode_t) >= THRESHOLDS["commit_s"] \
                    or judg["risk"] >= THRESHOLDS["override_risk"] \
                    or proposed in ("stop_and_wait", "back_and_turn", "reacquire_goal") \
                    or self.mode in ("stop_and_wait",)
                if switch:
                    self.mode, self.mode_t = proposed, t
            if self.mode in MODE_MAX_S and (t - self.mode_t) > MODE_MAX_S[self.mode]:
                self.mode = None  # exhausted: fall back until a fresh judgment arrives

        mode = self.mode
        self.stats["modes"][mode or "baseline"] = self.stats["modes"].get(mode or "baseline", 0) + 1

        # execute
        path_ok = bool(self.path) and judg.get("path_still_good", 0.0) >= 0.5
        if scene.goal.x is None and mode != "reacquire_goal":
            cmd = Cmd(0.0, 0.0, "idle", "no goal")
        elif mode is None:
            cmd = self._baseline(scene, t)
        elif use_jev and path_ok and mode in ("hold_course", "veer_left", "veer_right"):
            # The planner's route is judged still good: follow it. Side-bias
            # maneuvers only get to override the planner when it is NOT good.
            scale = 1.0
            if judg.get("risk", 0) > THRESHOLDS["risk_slow"]:
                scale *= 0.55
            if judg.get("observation_unreliable", 0) > THRESHOLDS["unreliable"]:
                scale = min(scale, 0.4)
            cmd = self._follow(scene, scale)
            cmd.source = f"jev:{mode}"
        elif mode == "hold_course":
            scale = 1.0
            if judg.get("risk", 0) > THRESHOLDS["risk_slow"]:
                scale *= 0.55
            if judg.get("observation_unreliable", 0) > THRESHOLDS["unreliable"]:
                scale = min(scale, 0.4)
            cmd = self._follow(scene, scale)
        elif mode in ("veer_left", "veer_right"):
            left = mode == "veer_left"
            gap = self._side_gap(scene, left)
            bearing = gap if gap is not None else (55.0 if left else -55.0)
            cmd = self._follow(scene, 0.55, aim_bearing_deg=bearing)
        elif mode == "creep":
            cmd = self._follow(scene, cfg.creep_speed_mps / cfg.max_speed_mps)
        elif mode == "back_and_turn":
            left_ok = scene.hardware.dist_rear_m is None or scene.hardware.dist_rear_m > 0.30
            left_clear, right_clear = self._side_clearance(scene, True), self._side_clearance(scene, False)
            turn = cfg.max_turn_deg_s * 0.7 * (1 if left_clear >= right_clear else -1)
            v = -cfg.creep_speed_mps if left_ok else 0.0
            cmd = Cmd(v, turn, "jev:back_and_turn" if use_jev else "baseline:back_and_turn",
                      "reverse blind: short and slow")
        elif mode == "stop_and_wait":
            cmd = Cmd(0.0, 0.0, "jev:stop_and_wait", "waiting for the picture to clear")
        elif mode == "reacquire_goal":
            if t - self.last_flip_t > 2.5:
                self.scan_dir *= -1.0
                self.last_flip_t = t
            cmd = Cmd(0.0, 40.0 * self.scan_dir, "jev:reacquire_goal", "scanning")
        else:
            cmd = self._baseline(scene, t)

        if use_jev:
            cmd.source = f"jev:{mode}" if mode else cmd.source

        # ---- reflex layer: code owns safety, judgment is irrelevant here
        cmd = self._reflex(scene, cmd)
        self.commanded_v = cmd.v_mps
        return cmd

    def _reflex(self, scene: Scene, cmd: Cmd) -> Cmd:
        cfg = self.cfg.rover
        def trip(reason: str, v: float = 0.0, keep_w: bool = True) -> Cmd:
            self.stats["reflexes"][reason] = self.stats["reflexes"].get(reason, 0) + 1
            scene.dynamics.hard_reflex = True
            scene.dynamics.reflex_reason = reason
            return Cmd(v, cmd.w_deg_s if keep_w else 0.0, f"reflex:{reason}", cmd.note)

        if scene.nogo_hit and cmd.v_mps > 0:
            return trip("nogo")
        hw = scene.hardware
        has_telemetry = hw.telemetry_age_s is not None and hw.telemetry_age_s <= 1.0
        if has_telemetry and hw.dist_front_m is not None:
            if hw.dist_front_m < cfg.tof_stop_m and cmd.v_mps > 0:
                return trip("tof_stop")
            if hw.dist_front_m < cfg.tof_slow_m and cmd.v_mps > cfg.creep_speed_mps:
                cmd = Cmd(cfg.creep_speed_mps, cmd.w_deg_s, cmd.source + "+tof_slow", cmd.note)
        if (scene.nearest_m is not None and scene.nearest_bearing_deg is not None
                and scene.nearest_m < 0.30 and abs(scene.nearest_bearing_deg) < 35.0
                and cmd.v_mps > 0):
            return trip("map_brake")
        if self.require_telemetry and not has_telemetry:
            return trip("no_telemetry", keep_w=False)
        return cmd
