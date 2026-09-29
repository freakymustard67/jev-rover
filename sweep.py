"""ToF sweep matcher + physical confirmation (M4 host side; off by default).

Ports the validated algorithm from ``tools/sim/tof_sim.py``: grid search over
poses against a world model, robust loss with residuals capped at 0.5 m, and
**desmear** - each beam is predicted from the pose advanced along the candidate's
arc by the assumed commanded (v, w) over that beam's sampling time in seconds.

Desmear is mandatory when sweeping while moving (``sweep-validation.md``):
without it, driving 0.45 m/s through a 1 s sweep gives ~24 cm median position
error and 7% success; with it, ~3 cm and 100%, robust to +-25% motion-estimate
error. Stationary sweeps are tag-grade (2-5 cm) without desmear.

Nothing here touches the network or the control loop: ``tools/smoke_scan.py``
and future M4 consumers feed it scans and world models. Everything is
config-gated (``sweep.enabled`` defaults to false) and hermetic - ``RectWorld``
mirrors the simulator's room exactly, so tests need no hardware.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from config import SweepMatchConfig
from scan_payload import NO_SAMPLE_ANGLE, STATUS_ABORTED, STATUS_TIMEOUT, ScanAssembly
from scene import SemanticObject, SweepConfirmation, wrap_deg

#: Statuses the host accepts as a distance measurement (spec v1 §2.1: 0 is the
#: only valid RangeStatus; 4/7/8/14 are phase-wrap / out-of-bounds garbage).
VALID_STATUS = 0


# ------------------------------------------------------------------ beams

@dataclass
class ScanBeams:
    """One sweep reduced to what the matcher needs."""

    bearings_deg: np.ndarray          # relative to the heading at scan start
    ranges_m: np.ndarray              # NaN where invalid / dropped
    t_s: np.ndarray                   # seconds since scan start per beam (desmear)

    def valid_mask(self) -> np.ndarray:
        return np.isfinite(self.ranges_m) & (self.ranges_m > 0.0)

    @property
    def n_total(self) -> int:
        return int(len(self.bearings_deg))

    @property
    def n_valid(self) -> int:
        return int(self.valid_mask().sum())

    @property
    def duration_s(self) -> float:
        return float(np.max(self.t_s)) if len(self.t_s) else 0.0

    def median_range_m(self) -> float:
        vals = self.ranges_m[self.valid_mask()]
        return float(np.median(vals)) if len(vals) else float("nan")


def beams_from_assembly(assembly: ScanAssembly, *, max_range_m: float = 4.0) -> ScanBeams:
    """ScanAssembly -> ScanBeams: status/angle filtering, mm -> m, desmear timing.

    Timing per sample: ``t_us`` when the firmware sent it (spec v1 §2.1: ms since
    scan start, despite the field name), else ``t0_ms + k * period_us`` from the
    chunk header. If a firmware sends no timing at all, the beam's index is used
    as a fraction of one second - desmear is then only approximate and the
    matcher says so via ``desmeared`` off (see ``SweepMatcher.match``).
    """
    bearings: list[float] = []
    ranges: list[float] = []
    times_ms: list[float | None] = []
    for idx in sorted(assembly.chunks):
        chunk = assembly.chunks[idx]
        for k, sample in enumerate(chunk.samples):
            bearings.append(float(sample.angle_dd))
            ok = (sample.angle_dd != NO_SAMPLE_ANGLE
                  and sample.status == VALID_STATUS
                  and 0 < sample.range_mm <= int(max_range_m * 1000))
            ranges.append(sample.range_mm / 1000.0 if ok else float("nan"))
            if sample.t_us is not None:
                times_ms.append(float(sample.t_us))
            elif chunk.period_us:
                times_ms.append(chunk.t0_ms + k * chunk.period_us / 1000.0)
            else:
                times_ms.append(None)

    known = [t for t in times_ms if t is not None]
    if len(known) >= 2:
        t0 = min(known)
        t_s = np.array([(t - t0) / 1000.0 if t is not None
                        else i / max(1, len(times_ms) - 1) for i, t in enumerate(times_ms)])
    else:
        n = max(1, len(times_ms) - 1)
        t_s = np.array([i / n for i in range(len(times_ms))])
    return ScanBeams(bearings_deg=np.asarray(bearings, dtype=float),
                     ranges_m=np.asarray(ranges, dtype=float),
                     t_s=np.clip(t_s, 0.0, None))


# ------------------------------------------------------------ world models

def _ray_aabb(ox, oy, dx, dy, boxes: np.ndarray) -> np.ndarray:
    """Ray vs axis-aligned rectangles (port of tof_sim.ray_aabb).

    ox..dy broadcastable to (N,1); boxes (M,4). Returns entry distance (N,M),
    inf when no forward hit.
    """
    x0, y0, x1, y1 = (boxes[None, :, 0], boxes[None, :, 1],
                      boxes[None, :, 2], boxes[None, :, 3])
    with np.errstate(divide="ignore", invalid="ignore"):
        tx1 = (x0 - ox) / dx
        tx2 = (x1 - ox) / dx
        ty1 = (y0 - oy) / dy
        ty2 = (y1 - oy) / dy
    tmin = np.maximum(np.minimum(tx1, tx2), np.minimum(ty1, ty2))
    tmax = np.minimum(np.maximum(tx1, tx2), np.maximum(ty1, ty2))
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin > 1e-9)
    return np.where(hit, tmin, np.inf)


def _advance(cands: np.ndarray, motion, t_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Candidate poses advanced along their arc by (v, w) for t_s seconds."""
    x, y, yaw_deg = cands[:, 0], cands[:, 1], cands[:, 2]
    if motion is None:
        return x, y, yaw_deg
    v, w_deg = motion
    yaw = np.deg2rad(yaw_deg)
    if abs(w_deg) < 1e-12:
        return x + v * t_s * np.cos(yaw), y + v * t_s * np.sin(yaw), yaw_deg
    w = np.deg2rad(w_deg)
    th = yaw + w * t_s
    radius = v / w
    return (x + radius * (np.sin(th) - np.sin(yaw)),
            y - radius * (np.cos(th) - np.cos(yaw)),
            np.rad2deg(th))


class RectWorld:
    """Analytic ray-vs-rectangles model (exact; mirrors the simulator's room)."""

    def __init__(self, rects, max_range_m: float = 4.0):
        self.rects = np.asarray(rects, dtype=float).reshape(-1, 4)
        self.max_range_m = float(max_range_m)

    def bounds(self) -> tuple[float, float, float, float]:
        return (float(self.rects[:, 0].min()), float(self.rects[:, 1].min()),
                float(self.rects[:, 2].max()), float(self.rects[:, 3].max()))

    def ranges_for(self, cands: np.ndarray, bearings_deg: np.ndarray, *,
                   motion=None, t_s: np.ndarray | None = None) -> np.ndarray:
        cands = np.atleast_2d(np.asarray(cands, dtype=float))
        out = np.full((len(cands), len(bearings_deg)), np.inf)
        for b, bearing in enumerate(bearings_deg):
            frac = 0.0 if t_s is None else float(np.asarray(t_s)[b])
            px, py, yaw = _advance(cands, motion, frac)
            ang = np.deg2rad(yaw + bearing)
            t = _ray_aabb(px[:, None], py[:, None],
                          np.cos(ang)[:, None], np.sin(ang)[:, None], self.rects)
            tmin = t.min(axis=1)
            out[:, b] = np.where(tmin <= self.max_range_m, tmin, np.inf)
        return out


class GridWorld:
    """Ray-march against an occupancy-grid snapshot (the live room model).

    Slower than ``RectWorld`` but needs no furniture list: it uses exactly the
    grid perception maintains. Beams are marched on a shared distance grid, so
    the search stays vectorized.
    """

    def __init__(self, log_odds: np.ndarray, cell_m: float, *,
                 occupied_thr: float = 0.42, max_range_m: float = 4.0,
                 step_m: float | None = None):
        self.cell_m = float(cell_m)
        self.occ = np.asarray(log_odds) > occupied_thr
        self.h, self.w = self.occ.shape
        self.max_range_m = float(max_range_m)
        self.step_m = float(step_m if step_m is not None else max(0.04, cell_m))

    def bounds(self) -> tuple[float, float, float, float]:
        return 0.0, 0.0, self.w * self.cell_m, self.h * self.cell_m

    def ranges_for(self, cands: np.ndarray, bearings_deg: np.ndarray, *,
                   motion=None, t_s: np.ndarray | None = None) -> np.ndarray:
        cands = np.atleast_2d(np.asarray(cands, dtype=float))
        n = len(cands)
        steps = max(2, int(self.max_range_m / self.step_m))
        out = np.full((n, len(bearings_deg)), np.inf)
        for b, bearing in enumerate(bearings_deg):
            frac = 0.0 if t_s is None else float(np.asarray(t_s)[b])
            x, y, yaw = _advance(cands, motion, frac)
            ang = np.deg2rad(yaw + bearing)
            ux, uy = np.cos(ang), np.sin(ang)
            found = np.zeros(n, dtype=bool)
            for k in range(1, steps + 1):
                d = k * self.step_m
                ix = ((x + ux * d) / self.cell_m).astype(np.int64, copy=False)
                iy = ((y + uy * d) / self.cell_m).astype(np.int64, copy=False)
                inside = (ix >= 0) & (ix < self.w) & (iy >= 0) & (iy < self.h)
                np.clip(ix, 0, self.w - 1, out=ix)
                np.clip(iy, 0, self.h - 1, out=iy)
                hit = self.occ[iy, ix] & inside & ~found
                if hit.any():
                    out[hit, b] = d
                    found |= hit
                    if found.all():
                        break
        return out


def grid_world_from_context(ctx, *, max_range_m: float = 4.0) -> GridWorld:
    """GridWorld from a ``perception.SemanticContext`` snapshot."""
    return GridWorld(ctx.grid_log_odds, ctx.cell_m,
                     occupied_thr=ctx.occupied_thr, max_range_m=max_range_m)


# ----------------------------------------------------------------- matcher

@dataclass
class MatchResult:
    x: float
    y: float
    yaw_deg: float
    pos_sigma_m: float            # residual-based scale, NOT a covariance
    yaw_sigma_deg: float
    confidence: float             # inlier fraction x coverage
    rmse_m: float
    n_valid: int
    n_total: int
    mode: str                     # global | local
    desmeared: bool

    def as_dict(self) -> dict:
        return {"x": round(self.x, 3), "y": round(self.y, 3),
                "yaw_deg": round(self.yaw_deg, 1),
                "pos_sigma_m": round(self.pos_sigma_m, 3),
                "yaw_sigma_deg": round(self.yaw_sigma_deg, 2),
                "confidence": round(self.confidence, 3),
                "rmse_m": round(self.rmse_m, 3),
                "n_valid": self.n_valid, "n_total": self.n_total,
                "mode": self.mode, "desmeared": self.desmeared}


class SweepMatcher:
    """Coarse-to-fine pose search; desmear when a motion prior is supplied."""

    def __init__(self, world, cfg: SweepMatchConfig, *, desmear: bool = False):
        self.world = world
        self.cfg = cfg
        self.desmear = bool(desmear)
        self.min_beams = int(cfg.min_beams)
        self.inlier_m = float(cfg.inlier_m)

    # -- internals ----------------------------------------------------------
    def _score(self, meas: np.ndarray, exp: np.ndarray) -> np.ndarray:
        """Robust loss per candidate: |residual| capped at 0.5 m; a measured beam
        with no expected return (or vice versa) costs 0.5 (port of tof_sim.score)."""
        valid = np.isfinite(meas)
        if not valid.any():
            return np.zeros(exp.shape[0])
        e = exp[:, valid]
        r = np.abs(e - meas[valid])
        r = np.where(np.isfinite(e), np.minimum(r, 0.5), 0.5)
        return r.sum(axis=1)

    def _candidates(self, prior, dx, dy, dyaw, step, yaw_step) -> np.ndarray:
        xs = np.arange(-dx, dx + 1e-9, step) + prior[0]
        ys = np.arange(-dy, dy + 1e-9, step) + prior[1]
        yaws = np.arange(-dyaw, dyaw + 1e-9, yaw_step) + prior[2]
        X, Y, Z = np.meshgrid(xs, ys, yaws, indexing="ij")
        return np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)

    def _search(self, beams: ScanBeams, cands: np.ndarray, motion,
                meas: np.ndarray) -> np.ndarray:
        exp = self.world.ranges_for(cands, beams.bearings_deg, motion=motion,
                                    t_s=beams.t_s)
        return cands[int(np.argmin(self._score(meas, exp)))]

    def _refine(self, beams: ScanBeams, cand: np.ndarray, motion,
                meas: np.ndarray) -> np.ndarray:
        fine = self._candidates(cand, 0.3, 0.3, 20.0, self.cfg.fine_step_m, 5.0)
        return self._search(beams, fine, motion, meas)

    def _global(self, beams: ScanBeams, motion, meas: np.ndarray) -> np.ndarray:
        x0, y0, x1, y1 = self.world.bounds()
        xs = np.arange(x0 + 0.15, x1 - 0.15 + 1e-9, self.cfg.coarse_step_m)
        ys = np.arange(y0 + 0.15, y1 - 0.15 + 1e-9, self.cfg.coarse_step_m)
        yaws = np.arange(0.0, 360.0, 30.0)
        X, Y, Z = np.meshgrid(xs, ys, yaws, indexing="ij")
        cands = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
        return self._search(beams, cands, motion, meas)

    # -- public -------------------------------------------------------------
    def match(self, beams: ScanBeams, prior: tuple[float, float, float] | None = None,
              motion: tuple[float, float] | None = None) -> MatchResult | None:
        """Recover (x, y, yaw).

        ``prior`` enables the local (tracking) search; ``motion`` = commanded
        (v_mps, w_deg_s) during the sweep, used for desmear when
        ``cfg.desmear`` is set. Returns None with too few valid beams.
        """
        if beams.n_valid < self.min_beams:
            return None
        use_motion = motion if (self.desmear and motion is not None) else None
        meas = np.where(beams.valid_mask(), beams.ranges_m, np.nan)

        if prior is None:
            if beams.duration_s <= 0.05:
                # No usable timing: a moving desmear would be invented, so refuse it.
                use_motion = None
            cand = self._global(beams, use_motion, meas)
            cand = self._refine(beams, cand, use_motion, meas)
            mode = "global"
        else:
            cand = np.asarray(prior, dtype=float)
            for _ in range(2):
                cand = self._refine(beams, cand, use_motion, meas)
            mode = "local"

        exp = self.world.ranges_for(cand[None, :], beams.bearings_deg,
                                    motion=use_motion, t_s=beams.t_s)[0]
        valid = np.isfinite(meas)
        res = np.abs(exp[valid] - meas[valid])
        res = np.where(np.isfinite(exp[valid]), np.minimum(res, 0.5), 0.5)
        rmse = float(np.sqrt(np.mean(res ** 2))) if len(res) else float("inf")
        inliers = float(np.mean(res <= self.inlier_m)) if len(res) else 0.0
        coverage = beams.n_valid / max(1, beams.n_total)
        confidence = inliers * (0.4 + 0.6 * coverage)
        median_range = beams.median_range_m()
        yaw_sigma = (math.degrees(rmse / max(0.5, median_range)) * 2.0
                     if math.isfinite(median_range) else 180.0)
        return MatchResult(
            x=float(cand[0]), y=float(cand[1]), yaw_deg=float(wrap_deg(cand[2])),
            pos_sigma_m=max(0.01, rmse),
            yaw_sigma_deg=max(0.5, min(30.0, yaw_sigma)),
            confidence=float(max(0.0, min(1.0, confidence))),
            rmse_m=rmse, n_valid=beams.n_valid, n_total=beams.n_total,
            mode=mode, desmeared=use_motion is not None)


# ----------------------------------------------------------- confirmation

def confirm_objects(beams: ScanBeams, pose: tuple[float, float, float],
                    objects: list[SemanticObject], *,
                    tolerance_m: float = 0.15, margin_deg: float = 6.0,
                    min_beams: int = 2,
                    max_range_m: float = 4.0) -> list[SweepConfirmation]:
    """Physical check of semantic objects against a matched sweep.

    confirmed    - measured range within tolerance of the object's distance
    contradicted - range differs beyond tolerance (nearer = an occluder or a
                   different object; farther = nothing there)
    absent       - no valid beams in the bearing window, or out of sensor range
    """
    out: list[SweepConfirmation] = []
    x, y, yaw = pose
    valid = beams.valid_mask()
    br = beams.bearings_deg[valid]
    rr = beams.ranges_m[valid]
    for obj in objects:
        key = obj.id or obj.label
        dx, dy = obj.x - x, obj.y - y
        bearing = wrap_deg(math.degrees(math.atan2(dy, dx)) - yaw)
        expected = math.hypot(dx, dy)
        if expected > max_range_m or expected < 0.05:
            out.append(SweepConfirmation(key, "absent", None))
            continue
        delta = (br - bearing + 180.0) % 360.0 - 180.0
        window = np.abs(delta) <= margin_deg
        if int(window.sum()) < min_beams:
            out.append(SweepConfirmation(key, "absent", None))
            continue
        measured = float(np.median(rr[window]))
        err = measured - expected
        out.append(SweepConfirmation(
            key, "confirmed" if abs(err) <= tolerance_m else "contradicted",
            round(err, 3)))
    return out
