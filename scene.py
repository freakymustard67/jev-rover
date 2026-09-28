"""The symbolic scene: the one contract between perception, Jev and control.

Nothing downstream of ``perception.py`` touches pixels. Everything downstream
of ``perception.py`` reads a ``Scene``. That keeps the boundary honest: if a
decision needs a fact, the fact has to exist here with a name, a number and an
age.

Conventions
-----------
* World frame: metres, x right, y up, yaw in degrees CCW from +x.
* Rover frame: ``fwd`` along the rover's forward axis, ``left`` 90 deg CCW.
* Bearings are degrees, positive to the LEFT, zero straight ahead.
* Angles in this module are always wrapped to (-180, 180].
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

#: Named angular sectors, in rover frame. Keep the names stable: the Jev
#: question rubrics reference them.
SECTORS: tuple[tuple[str, float, float], ...] = (
    ("far_left", -90.0, -60.0),
    ("left", -60.0, -30.0),
    ("near_left", -30.0, -10.0),
    ("ahead", -10.0, 10.0),
    ("near_right", 10.0, 30.0),
    ("right", 30.0, 60.0),
    ("far_right", 60.0, 90.0),
)


def wrap_deg(a: float) -> float:
    """Wrap an angle in degrees to (-180, 180]."""
    a = math.fmod(a + 180.0, 360.0)
    if a < 0.0:
        a += 360.0
    a -= 180.0
    return 180.0 if a == -180.0 else a


def rover_frame(dx: float, dy: float, yaw_deg: float) -> tuple[float, float, float, float]:
    """World displacement -> (fwd_m, left_m, bearing_deg, range_m) in rover frame."""
    yaw = math.radians(yaw_deg)
    c, s = math.cos(yaw), math.sin(yaw)
    fwd = dx * c + dy * s
    left = -dx * s + dy * c
    return fwd, left, wrap_deg(math.degrees(math.atan2(left, fwd))), math.hypot(dx, dy)


def sector_name(bearing_deg: float) -> str:
    """Name of the sector a bearing falls in (clamped to the outer sectors)."""
    b = wrap_deg(bearing_deg)
    if b < -90.0 or b > 90.0:
        b = max(-89.999, min(89.999, b))
    for name, lo, hi in SECTORS:
        if lo <= b < hi or (hi == 90.0 and b >= lo):
            return name
    return "ahead"


def summarize_rays(
    angles_deg: list[float],
    ranges_m: list[float],
    free_threshold_m: float = 0.55,
    blocked_at_m: float = 0.50,
    clear_ahead_deg: float = 20.0,
    min_run_deg: float = 10.0,
    limits: list[str] | None = None,
) -> dict[str, Any]:
    """Turn a fan of rays into the fields Jev actually reasons over.

    ``ranges_m`` is the free distance along each ray (distance to the first
    obstacle, capped at the sensor/model range). ``limits`` optionally says why
    each ray stopped: "obstacle", "unseen" or "range". Numpy arrays or lists work.
    """
    if len(angles_deg) != len(ranges_m) or not angles_deg:
        raise ValueError("angles_deg and ranges_m must be non-empty and equal length")
    if limits is not None and len(limits) != len(angles_deg):
        raise ValueError("limits must match angles_deg length")

    per_sector: dict[str, list[tuple[float, str]]] = {name: [] for name, _, _ in SECTORS}
    for i, (a, r) in enumerate(zip(angles_deg, ranges_m)):
        name = sector_name(a)
        if name in per_sector:
            per_sector[name].append((float(r), limits[i] if limits else "range"))

    sectors = []
    for name, lo, hi in SECTORS:
        vals = per_sector[name]
        if vals:
            free, limit = min(vals, key=lambda p: p[0])
        else:
            free, limit = math.inf, "range"
        sectors.append({
            "name": name,
            "lo_deg": lo,
            "hi_deg": hi,
            "free_m": None if math.isinf(free) else round(free, 2),
            "blocked": bool(vals and free < blocked_at_m),
            "limit": limit if vals else "range",
        })

    pairs = sorted(zip((float(a) for a in angles_deg), (float(r) for r in ranges_m)))
    ahead = [r for a, r in pairs if abs(a) <= clear_ahead_deg]
    nearest_a, nearest_r = min(pairs, key=lambda p: p[1])

    # Contiguous angular runs with more than free_threshold_m of clearance.
    runs: list[list[float]] = []
    run_lo: float | None = None
    run_min = math.inf
    prev_a = None
    for a, r in pairs:
        if r > free_threshold_m:
            if run_lo is None:
                run_lo = a
            run_min = min(run_min, r)
        elif run_lo is not None:
            runs.append([run_lo, prev_a if prev_a is not None else a, run_min])
            run_lo, run_min = None, math.inf
        prev_a = a
    if run_lo is not None:
        runs.append([run_lo, prev_a if prev_a is not None else run_lo, run_min])

    runs = [r for r in runs if (r[1] - r[0]) >= min_run_deg]
    signed_runs = []
    for lo, hi, clearance in runs:
        if lo < 0 <= hi:  # split a run that straddles straight ahead, keep the sides clean
            signed_runs.append((lo, -0.001, clearance))
            signed_runs.append((0.001, hi, clearance))
        else:
            signed_runs.append((lo, hi, clearance))
    widest = max(signed_runs, key=lambda r: r[1] - r[0]) if signed_runs else None

    return {
        "sectors": sectors,
        "nearest_m": round(nearest_r, 2),
        "nearest_bearing_deg": round(nearest_a, 1),
        "clear_ahead_m": round(min(ahead), 2) if ahead else None,
        "free_runs_deg": [[round(lo, 1), round(hi, 1), round(cl, 2)] for lo, hi, cl in signed_runs],
        "widest_run_deg": round(widest[1] - widest[0], 1) if widest else 0.0,
        "widest_run_clearance_m": round(widest[2], 2) if widest else 0.0,
        "widest_run_center_deg": round((widest[0] + widest[1]) / 2.0, 1) if widest else None,
    }


# --------------------------------------------------------------------------- scene

@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    yaw_deg: float = 0.0


@dataclass
class Twist:
    v_mps: float = 0.0
    w_deg_s: float = 0.0


@dataclass
class Sector:
    name: str
    lo_deg: float
    hi_deg: float
    free_m: float | None
    blocked: bool
    # Why the reported free distance stops there: "obstacle" (seen something),
    # "unseen" (no recent observation beyond this), "range" (model range cap).
    limit: str = "range"


@dataclass
class ObstacleTrack:
    id: int
    x: float
    y: float
    vx: float
    vy: float
    radius_m: float
    area_m2: float
    bearing_deg: float
    range_m: float
    kind: str          # static | slow | moving
    age_s: float
    seen_s_ago: float


@dataclass
class TargetObs:
    visible: bool = False
    mode: str = "none"
    bearing_deg: float | None = None
    range_m: float | None = None
    x: float | None = None
    y: float | None = None
    vx: float | None = None
    vy: float | None = None
    unseen_for_s: float = 0.0


@dataclass
class GoalInfo:
    type: str = "none"          # none | waypoint | track | patrol
    name: str | None = None
    index: int = 0
    x: float | None = None
    y: float | None = None
    yaw_deg: float | None = None
    bearing_deg: float | None = None
    range_m: float | None = None
    progress_s: float = 0.0
    no_progress_s: float = 0.0
    path_valid: bool = False
    path_len_m: float | None = None
    path_through_unknown: bool = False
    #: Initial bearing of the planned path in rover frame: where the planner
    #: wants to go right now (may differ from the straight-line goal bearing).
    path_bearing_deg: float | None = None


@dataclass
class Dynamics:
    cmd_v_mps: float = 0.0
    obs_v_mps: float = 0.0
    slip_ratio: float = 0.0
    no_progress_s: float = 0.0
    blocked_forward: bool = False
    hard_reflex: bool = False
    reflex_reason: str = ""


@dataclass
class Hardware:
    dist_front_m: float | None = None
    dist_rear_m: float | None = None
    batt_v: float | None = None
    watchdog_ok: bool = False
    reflex: bool = False
    telemetry_age_s: float | None = None


@dataclass
class PerceptionQuality:
    fps: float = 0.0
    frame_age_s: float = 0.0
    pose_source: str = "tag"        # tag | dead_reckon | lost
    pose_age_s: float = 0.0
    tag_visible: bool = False
    occlusion_risk: float = 0.0     # fraction of the 1.5 m ring around the rover never seen
    unknowns_near_rover: int = 0


@dataclass
class Mission:
    mode: str = "goto"              # goto | patrol | track
    instruction: str | None = None
    route: str | None = None


@dataclass
class Scene:
    t: float = 0.0
    frame_id: int = 0
    pose: Pose = field(default_factory=Pose)
    twist: Twist = field(default_factory=Twist)
    quality: PerceptionQuality = field(default_factory=PerceptionQuality)
    sectors: list[Sector] = field(default_factory=list)
    nearest_m: float | None = None
    nearest_bearing_deg: float | None = None
    clear_ahead_m: float | None = None
    free_runs_deg: list[list[float]] = field(default_factory=list)
    widest_run_deg: float = 0.0
    widest_run_clearance_m: float = 0.0
    widest_run_center_deg: float | None = None
    tracks: list[ObstacleTrack] = field(default_factory=list)
    nogo_hit: bool = False
    target: TargetObs = field(default_factory=TargetObs)
    goal: GoalInfo = field(default_factory=GoalInfo)
    dynamics: Dynamics = field(default_factory=Dynamics)
    hardware: Hardware = field(default_factory=Hardware)
    mission: Mission = field(default_factory=Mission)

    # ------------------------------------------------------------ accessors
    def sector(self, name: str) -> float | None:
        for s in self.sectors:
            if s.name == name:
                return s.free_m
        return None

    def obstacles_ahead(self, max_bearing_deg: float = 35.0, min_range_m: float = 0.0) -> list[ObstacleTrack]:
        return [tr for tr in self.tracks
                if abs(tr.bearing_deg) <= max_bearing_deg and tr.range_m >= min_range_m]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    def json_line(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


def build_scene(rays: dict[str, Any], **parts) -> Scene:
    """Assemble a Scene from ``summarize_rays`` output plus named sub-parts."""
    scene = Scene(
        sectors=[Sector(**s) for s in rays.pop("sectors")],
        widest_run_deg=rays.pop("widest_run_deg"),
        widest_run_clearance_m=rays.pop("widest_run_clearance_m"),
        widest_run_center_deg=rays.pop("widest_run_center_deg"),
        free_runs_deg=rays.pop("free_runs_deg"),
        **rays,
    )
    for key, value in parts.items():
        setattr(scene, key, value)
    return scene
