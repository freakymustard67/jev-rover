"""Room, camera, robot and mission configuration.

Everything machine-specific lives in ``config/room*.json`` so no module
hard-codes a number that changes behavior. Load once with ``RoomConfig.load``;
all validation happens at load time so a bad config fails immediately and
loudly, not mid-episode.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints


class ConfigError(ValueError):
    """Raised when a room config is missing or inconsistent."""


def _need(data: dict, key: str, where: str) -> Any:
    if key not in data:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return data[key]


def _num_list(value: Any, n_min: int, where: str, n_max: int | None = None) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < n_min:
        raise ConfigError(f"{where}: expected at least {n_min} numbers, got {value!r}")
    if n_max is not None and len(value) > n_max:
        raise ConfigError(f"{where}: expected at most {n_max} numbers, got {value!r}")
    out: list[float] = []
    for v in value:
        if not isinstance(v, (int, float)):
            raise ConfigError(f"{where}: {v!r} is not a number")
        out.append(float(v))
    return out


@dataclass
class CameraConfig:
    # "0" for the first USB camera, "/dev/videoN", an RTSP/HTTP URL, or a video file.
    source: str = "0"
    width: int = 1280
    height: int = 720
    # Horizontal field of view in degrees, approx from the device spec: the
    # camera-only depth calibration derives the focal from it (f = (w/2)/tan(hfov/2)).
    hfov_deg: float = 60.0
    # Optional ``calibrate.py intrinsics`` output; when set, frames are undistorted first.
    intrinsics: str = ""


@dataclass
class FloorConfig:
    # Room outline in image pixels. Obstacle candidates are only looked for inside it.
    polygon_px: list[list[float]] = field(default_factory=list)
    # Max CIE-Lab distance from the sampled floor color for a pixel to count as floor.
    lab_tolerance: float = 22.0
    # Optional empty-room reference frame; grayscale difference above this is not floor.
    background: str = ""
    bg_tolerance: float = 18.0
    # Slowly re-estimate the floor color while running (handles daylight drift).
    adapt: bool = False


@dataclass
class HomographyConfig:
    # >= 4 pixel/world correspondences of floor points, in the same order.
    image_points_px: list[list[float]] = field(default_factory=list)
    world_points_m: list[list[float]] = field(default_factory=list)


@dataclass
class GridConfig:
    cell_m: float = 0.05
    inflation_m: float = 0.22
    max_range_m: float = 5.5
    occupancy_threshold: float = 0.5   # probability above which a cell is an obstacle
    stale_s: float = 3.0               # a cell not seen for this long becomes unknown


@dataclass
class RoverConfig:
    footprint_radius_m: float = 0.18
    #: Where pose comes from: "tag" (AprilTag on the rover) or "blob"
    #: (camera-only: static-scene background subtraction -> rover blob).
    pose_source: str = "tag"
    tag_id: int = 0
    tag_size_m: float = 0.10
    # Rotation between the tag's +y (printed "up") and the rover's forward axis.
    tag_yaw_offset_deg: float = 0.0
    # Pixels around the rover's footprint that are ignored as obstacles (body, shadow lip).
    rover_mask_extra_m: float = 0.08
    max_speed_mps: float = 0.6
    max_turn_deg_s: float = 120.0
    creep_speed_mps: float = 0.15
    tof_stop_m: float = 0.18           # mirror of the ESP32 reflex, laptop-side
    tof_slow_m: float = 0.35
    command_ttl_ms: int = 400


@dataclass
class TargetConfig:
    mode: str = "none"                 # none | tag | color
    tag_id: int = 1
    hsv_lo: list[int] = field(default_factory=lambda: [0, 120, 80])
    hsv_hi: list[int] = field(default_factory=lambda: [15, 255, 255])
    min_area_px: int = 200
    standoff_m: float = 1.0


# ------------------------------------------------------------- semantics (M1)

@dataclass
class FixtureEntry:
    """One world-space fixture for FakeVision (tests, replay, demos)."""

    label: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.3
    h: float = 0.3
    score: float = 0.9


@dataclass
class VisionModelConfig:
    kind: str = "fake"                 # fake | local | remote
    labels: list[str] = field(default_factory=list)
    endpoint: str = ""
    timeout_s: float = 10.0
    fixtures: list[FixtureEntry] = field(default_factory=list)
    # --- M2 real adapters (m2-design.md §2.6) ---
    model_id: str = ""                 # local: HF id; "" -> vision.DEFAULT_LOCAL_MODEL
    device: str = "auto"               # auto | cpu | cuda | cuda:N
    half: bool = False                 # fp16, cuda only
    image_shortest_edge: int = 800     # processor size (CPU profile: 400)
    image_longest_edge: int = 1333     # (CPU profile: 666)
    box_threshold: float = 0.30
    text_threshold: float = 0.25
    warmup: bool = True                # one forward pass on the worker thread at startup
    input_scale: float = 1.0           # optional frame downscale; boxes rescaled back
    prompt_attributes: bool = False    # FG-OVD-style repeated class token in the prompt
    jpeg_quality: int = 85             # remote
    jpeg_max_px: int = 0               # remote: longest JPEG edge; 0 = off
    auth_env: str = "JEV_ROVER_VISION_TOKEN"   # remote bearer token, env only


@dataclass
class ProjectConfig:
    point: str = "bbox_bottom_center"  # bbox_bottom_center | bbox_center | centroid
    probe_px: int = 6
    point_by_label: dict[str, str] = field(default_factory=dict)


@dataclass
class SemanticsConfig:
    enabled: bool = False
    model: VisionModelConfig = field(default_factory=VisionModelConfig)
    project: ProjectConfig = field(default_factory=ProjectConfig)
    audit_period_s: float = 60.0
    max_passes_per_min: int = 4
    max_age_s: float = 30.0
    roi_min_confidence: float = 0.5
    min_confidence: float = 0.5
    match_radius_m: float = 0.5
    ema_alpha: float = 0.4
    move_threshold_m: float = 0.25
    vanish_passes: int = 2
    max_misses: int = 10               # evict (cap resurrection) after this many missed passes
    dedupe_iou: float = 0.5
    dedupe_center_m: float = 0.15      # ...or same-label bbox centres closer than this (metres)
    min_interval_s: float = 2.0
    failure_cooldown_s: float = 10.0
    min_label_score: float = 0.34
    ambiguity_epsilon: float = 0.15
    store_dir: str = "runs/semantic"


@dataclass
class DestinationConfig:
    standoff_m: float = 0.35


@dataclass
class SweepSensorConfig:
    kind: str = "tof"
    min_range_m: float = 0.04
    max_range_m: float = 4.0
    # --- scan control (spec v1 §2.3; mirrors link.encode_scan_command) ---
    start_deg: float = -90.0
    end_deg: float = 90.0
    step_deg: float = 6.0
    rate_hz: float = 20.0
    budget_ms: int = 33                # VL53L1X timing budget (20-1000 ms)
    scan_daemon: bool = False          # reserved for M4 continuous scanning (no consumer yet)


@dataclass
class SweepMatchConfig:
    coarse_step_m: float = 0.2
    fine_step_m: float = 0.05
    yaw_step_deg: float = 5.0
    min_beams: int = 6                 # fewer valid beams than this -> no match
    inlier_m: float = 0.15             # residual counted as an inlier


@dataclass
class SweepConfig:
    enabled: bool = False
    sensor: SweepSensorConfig = field(default_factory=SweepSensorConfig)
    match: SweepMatchConfig = field(default_factory=SweepMatchConfig)
    desmear: bool = False


@dataclass
class ConfirmationConfig:
    backend: str = "none"              # none | sweep
    tolerance_m: float = 0.15


@dataclass
class DepthCalibration:
    """Written by ``calibrate.py depth``: the camera-only floor calibration."""

    model: str = ""
    frames: int = 0
    hfov_deg: float = 0.0
    focal_px: float = 0.0
    image_wh: list[int] = field(default_factory=list)
    normal: list[float] = field(default_factory=list)
    offset_m: float = 0.0
    camera_height_m: float = 0.0
    inlier_ratio: float = 0.0
    residual_median_m: float = 0.0
    residual_p90_m: float = 0.0
    inliers: int = 0
    samples: int = 0
    floor_size_m: list[float] = field(default_factory=list)
    metres_per_px_center: list[float] = field(default_factory=list)
    calibrated_at: str = ""
    note: str = ""


@dataclass
class SyntheticConfig:
    """Frames generated by ``synthetic.py`` — lets the whole loop run without hardware."""

    px_per_m: float = 200.0
    drive_v_mps: float = 0.45
    drive_w_deg_s: float = 14.0


@dataclass
class RoomConfig:
    name: str = "room"
    width_m: float = 6.4
    height_m: float = 3.6
    camera: CameraConfig = field(default_factory=CameraConfig)
    floor: FloorConfig = field(default_factory=FloorConfig)
    homography: HomographyConfig = field(default_factory=HomographyConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    rover: RoverConfig = field(default_factory=RoverConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    semantics: SemanticsConfig = field(default_factory=SemanticsConfig)
    destination: DestinationConfig = field(default_factory=DestinationConfig)
    sweep: SweepConfig = field(default_factory=SweepConfig)
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    depth: DepthCalibration | None = None
    synthetic: SyntheticConfig = field(default_factory=SyntheticConfig)
    # name -> [x_m, y_m] or [x_m, y_m, yaw_deg]
    waypoints: dict[str, list[float]] = field(default_factory=dict)
    # route name -> ordered list of waypoint names
    routes: dict[str, list[str]] = field(default_factory=dict)
    # manually marked no-go circles: [x_m, y_m, radius_m]
    nogo: list[list[float]] = field(default_factory=list)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path) -> "RoomConfig":
        raw = json.loads(Path(path).read_text())
        return cls.from_dict(raw, where=str(path))

    @classmethod
    def from_dict(cls, data: dict, where: str = "config") -> "RoomConfig":
        cfg = cls(
            name=str(data.get("name", "room")),
            width_m=float(data.get("width_m", 6.4)),
            height_m=float(data.get("height_m", 3.6)),
        )
        for key, typ, strict in (("camera", CameraConfig, False),
                                 ("floor", FloorConfig, False),
                                 ("homography", HomographyConfig, False),
                                 ("grid", GridConfig, False),
                                 ("rover", RoverConfig, False),
                                 ("target", TargetConfig, False),
                                 ("synthetic", SyntheticConfig, False),
                                 ("semantics", SemanticsConfig, True),
                                 ("destination", DestinationConfig, True),
                                 ("sweep", SweepConfig, True),
                                 ("confirmation", ConfirmationConfig, True),
                                 ("depth", DepthCalibration, False)):
            if key in data and data[key] is not None:
                cfg.__dict__[key] = _section(typ, data[key], f"{where}.{key}", strict=strict)
        cfg.waypoints = _waypoints(data.get("waypoints", {}), where)
        cfg.routes = dict(data.get("routes", {}))
        cfg.nogo = [
            _num_list(v, 3, f"{where}.nogo[{i}]", 3) for i, v in enumerate(data.get("nogo", []))
        ]
        cfg.validate(where)
        return cfg

    def validate(self, where: str = "config") -> None:
        if self.width_m <= 0 or self.height_m <= 0:
            raise ConfigError(f"{where}: room must have positive width_m/height_m")
        h = self.homography
        if len(h.image_points_px) != len(h.world_points_m):
            raise ConfigError(
                f"{where}.homography: {len(h.image_points_px)} image points but "
                f"{len(h.world_points_m)} world points"
            )
        if len(h.image_points_px) < 4:
            raise ConfigError(f"{where}.homography: need >= 4 correspondences, "
                              f"got {len(h.image_points_px)} (run: calibrate.py floor)")
        for i, p in enumerate(h.image_points_px):
            if len(p) != 2:
                raise ConfigError(f"{where}.homography.image_points_px[{i}] must be [x, y]")
        for i, p in enumerate(h.world_points_m):
            if len(p) != 2:
                raise ConfigError(f"{where}.homography.world_points_m[{i}] must be [x, y]")
        if len(self.floor.polygon_px) < 3:
            raise ConfigError(f"{where}.floor.polygon_px: need >= 3 points "
                              f"(run: calibrate.py floor)")
        r = self.rover
        if not (0.02 <= r.footprint_radius_m <= 0.5):
            raise ConfigError(f"{where}.rover.footprint_radius_m out of range: "
                              f"{r.footprint_radius_m}")
        _check_choice(r.pose_source, ("tag", "blob"), f"{where}.rover.pose_source")
        if not 0.0 < self.camera.hfov_deg < 179.0:
            raise ConfigError(f"{where}.camera.hfov_deg must be in (0, 179)")
        if r.tof_stop_m >= r.tof_slow_m:
            raise ConfigError(f"{where}.rover: tof_stop_m must be < tof_slow_m")
        if r.tag_size_m <= 0:
            raise ConfigError(f"{where}.rover.tag_size_m must be positive")
        if self.target.mode not in ("none", "tag", "color"):
            raise ConfigError(f"{where}.target.mode must be none|tag|color")
        for name, wp in self.waypoints.items():
            if not (0 <= wp[0] <= self.width_m and 0 <= wp[1] <= self.height_m):
                raise ConfigError(f"{where}.waypoints.{name}: {wp[:2]} outside the room")
        for route, names in self.routes.items():
            for n in names:
                if n not in self.waypoints:
                    raise ConfigError(f"{where}.routes.{route}: unknown waypoint '{n}'")
        self._validate_semantics(where)

    def _validate_semantics(self, where: str) -> None:
        s = self.semantics
        if self.destination.standoff_m < 0:
            raise ConfigError(f"{where}.destination.standoff_m must be >= 0")
        _check_choice(s.model.kind, ("fake", "local", "remote"), f"{where}.semantics.model.kind")
        if s.model.timeout_s <= 0:
            raise ConfigError(f"{where}.semantics.model.timeout_s must be > 0")
        m = s.model
        if m.kind in ("local", "remote") and not m.labels:
            raise ConfigError(f"{where}.semantics.model.labels must be non-empty "
                              f"for kind {m.kind!r} (a real model needs a prompt)")
        for name, value in (("box_threshold", m.box_threshold),
                            ("text_threshold", m.text_threshold)):
            if not 0.0 < value < 1.0:
                raise ConfigError(f"{where}.semantics.model.{name} must be in (0, 1)")
        if not 0.0 < m.input_scale <= 1.0:
            raise ConfigError(f"{where}.semantics.model.input_scale must be in (0, 1]")
        if m.image_shortest_edge < 64:
            raise ConfigError(f"{where}.semantics.model.image_shortest_edge must be >= 64")
        if m.image_longest_edge < m.image_shortest_edge:
            raise ConfigError(f"{where}.semantics.model.image_longest_edge must be "
                              f">= image_shortest_edge")
        if not 1 <= m.jpeg_quality <= 100:
            raise ConfigError(f"{where}.semantics.model.jpeg_quality must be in [1, 100]")
        if m.jpeg_max_px < 0:
            raise ConfigError(f"{where}.semantics.model.jpeg_max_px must be >= 0")
        if not re.fullmatch(r"(auto|cpu|cuda(:\d+)?)", m.device):
            raise ConfigError(f"{where}.semantics.model.device must be auto|cpu|cuda[:N]")
        for i, fx in enumerate(s.model.fixtures):
            w = f"{where}.semantics.model.fixtures[{i}]"
            if not fx.label:
                raise ConfigError(f"{w}.label must not be empty")
            if fx.w <= 0 or fx.h <= 0:
                raise ConfigError(f"{w}: w and h must be > 0")
            if not (0.0 <= fx.score <= 1.0):
                raise ConfigError(f"{w}.score must be within [0, 1]")
        _check_choice(s.project.point, ANCHOR_POINTS, f"{where}.semantics.project.point")
        for label, point in s.project.point_by_label.items():
            _check_choice(point, ANCHOR_POINTS,
                          f"{where}.semantics.project.point_by_label[{label!r}]")
        if s.project.probe_px < 0:
            raise ConfigError(f"{where}.semantics.project.probe_px must be >= 0")
        for name, value, lo, hi in (("audit_period_s", s.audit_period_s, 1e-9, None),
                                    ("max_age_s", s.max_age_s, 1e-9, None),
                                    ("min_interval_s", s.min_interval_s, 0.0, None),
                                    ("failure_cooldown_s", s.failure_cooldown_s, 0.0, None),
                                    ("ema_alpha", s.ema_alpha, 1e-9, 1.0),
                                    ("dedupe_iou", s.dedupe_iou, 0.0, 1.0),
                                    ("dedupe_center_m", s.dedupe_center_m, 0.0, None),
                                    ("min_confidence", s.min_confidence, 0.0, 1.0),
                                    ("roi_min_confidence", s.roi_min_confidence, 0.0, 1.0),
                                    ("match_radius_m", s.match_radius_m, 1e-9, None),
                                    ("move_threshold_m", s.move_threshold_m, 1e-9, None),
                                    ("min_label_score", s.min_label_score, 0.0, 1.0),
                                    ("ambiguity_epsilon", s.ambiguity_epsilon, 0.0, None)):
            if value < lo or (hi is not None and value > hi):
                raise ConfigError(f"{where}.semantics.{name} out of range: {value}")
        if s.max_passes_per_min < 1:
            raise ConfigError(f"{where}.semantics.max_passes_per_min must be >= 1")
        if s.vanish_passes < 1:
            raise ConfigError(f"{where}.semantics.vanish_passes must be >= 1")
        if s.max_misses < s.vanish_passes:
            raise ConfigError(f"{where}.semantics.max_misses must be >= vanish_passes "
                              f"({s.max_misses} < {s.vanish_passes})")
        w = self.sweep
        _check_choice(w.sensor.kind, ("tof",), f"{where}.sweep.sensor.kind")
        if not (0.0 < w.sensor.min_range_m < w.sensor.max_range_m):
            raise ConfigError(f"{where}.sweep.sensor: need 0 < min_range_m < max_range_m")
        if not (-90.0 <= w.sensor.start_deg < w.sensor.end_deg <= 90.0):
            raise ConfigError(f"{where}.sweep.sensor: need -90 <= start_deg < end_deg <= 90")
        if not float(w.sensor.step_deg).is_integer() or not 1 <= w.sensor.step_deg <= 180:
            raise ConfigError(f"{where}.sweep.sensor.step_deg must be an integer in [1, 180]")
        if not float(w.sensor.rate_hz).is_integer() or not 1 <= w.sensor.rate_hz <= 50:
            raise ConfigError(f"{where}.sweep.sensor.rate_hz must be an integer in [1, 50]")
        if not 20 <= w.sensor.budget_ms <= 1000:
            raise ConfigError(f"{where}.sweep.sensor.budget_ms must be in [20, 1000]")
        if w.match.coarse_step_m <= 0 or w.match.fine_step_m <= 0 or w.match.yaw_step_deg <= 0:
            raise ConfigError(f"{where}.sweep.match: steps must be > 0")
        if w.match.min_beams < 1:
            raise ConfigError(f"{where}.sweep.match.min_beams must be >= 1")
        if not 0.0 < w.match.inlier_m < 1.0:
            raise ConfigError(f"{where}.sweep.match.inlier_m must be in (0, 1)")
        _check_choice(self.confirmation.backend, ("none", "sweep"),
                      f"{where}.confirmation.backend")
        if self.confirmation.tolerance_m <= 0:
            raise ConfigError(f"{where}.confirmation.tolerance_m must be > 0")

    # ------------------------------------------------------------- accessors
    def waypoint(self, name: str) -> tuple[float, float, float | None]:
        if name not in self.waypoints:
            raise ConfigError(f"unknown waypoint '{name}'")
        wp = self.waypoints[name]
        return wp[0], wp[1], (wp[2] if len(wp) > 2 else None)

    def to_dict(self) -> dict:
        out = asdict(self)
        # Native JSON types for the config round-trip.
        return json.loads(json.dumps(out))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")


def _section(typ: type, data: dict, where: str, strict: bool = False):
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected an object")
    known = {f.name for f in fields(typ)}
    if strict:
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    hints = get_type_hints(typ)
    kwargs: dict[str, Any] = {}
    for f in fields(typ):
        if f.name not in data:
            continue
        value = data[f.name]
        hint = hints.get(f.name)
        if isinstance(hint, type) and is_dataclass(hint) and isinstance(value, dict):
            kwargs[f.name] = _section(hint, value, f"{where}.{f.name}", strict=strict)
        elif _is_dataclass_list(hint) and isinstance(value, list):
            inner = hint.__args__[0]
            kwargs[f.name] = [
                _section(inner, v, f"{where}.{f.name}[{i}]", strict=strict)
                if isinstance(v, dict) else v
                for i, v in enumerate(value)
            ]
        else:
            kwargs[f.name] = value
    obj = typ(**kwargs)
    # Type coercion + per-field validation for the numeric knobs.
    for f in fields(typ):
        v = getattr(obj, f.name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            setattr(obj, f.name, float(v) if isinstance(v, float) else v)
    return obj


def _is_dataclass_list(hint) -> bool:
    args = getattr(hint, "__args__", ())
    return (getattr(hint, "__origin__", None) is list and bool(args)
            and isinstance(args[0], type) and is_dataclass(args[0]))


def _check_choice(value: str, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise ConfigError(f"{where}: must be one of {allowed}, got {value!r}")


ANCHOR_POINTS = ("bbox_bottom_center", "bbox_center", "centroid")


def _waypoints(data: dict, where: str) -> dict[str, list[float]]:
    if not isinstance(data, dict):
        raise ConfigError(f"{where}.waypoints: expected an object of name -> [x, y]")
    out: dict[str, list[float]] = {}
    for name, v in data.items():
        out[name] = _num_list(v, 2, f"{where}.waypoints.{name}", 3)
    return out
