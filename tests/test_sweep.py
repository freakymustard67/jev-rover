"""Sweep matcher, desmear and confirmation against the simulator's own model.

``tools/sim/tof_sim.py`` (numpy-only, in-repo) is used as BOTH the scan
generator and the geometry, so the production matcher is validated against the
exact noise model that produced ``docs/planning/sweep-validation.md``.
"""
import math
import sys

import numpy as np
import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools" / "sim"))
import tof_sim as sim  # noqa: E402

from config import SweepMatchConfig  # noqa: E402
from scan_payload import (STATUS_TIMEOUT, ScanAssembly, ScanChunk,  # noqa: E402
                          ScanSample)
from scene import SemanticObject  # noqa: E402
from sweep import (GridWorld, RectWorld, ScanBeams, SweepMatcher,  # noqa: E402
                   beams_from_assembly, confirm_objects)

REL2 = np.arange(-90.0, 90.0 + 1e-9, 2.0)     # 91 beams
REL6 = np.arange(-90.0, 90.0 + 1e-9, 6.0)     # 31 beams


def _world() -> RectWorld:
    return RectWorld(sim.OBST, max_range_m=sim.MAX_RANGE)


def _beams(pose, rel_deg, rng, v=0.0, w=0.0, sweep_s=1.0) -> ScanBeams:
    meas = sim.simulate_sweep(pose, rel_deg, rng, v=v, w_deg_s=w, sweep_s=sweep_s)
    t_s = sweep_s * np.arange(len(rel_deg)) / max(1, len(rel_deg) - 1)
    return ScanBeams(bearings_deg=np.asarray(rel_deg, float), ranges_m=meas, t_s=t_s)


def _err(res, pose):
    return math.hypot(res.x - pose[0], res.y - pose[1]), abs(sim.wrap(res.yaw_deg - pose[2]))


def test_stationary_31_beams_local_tracking():
    rng = np.random.default_rng(3)
    matcher = SweepMatcher(_world(), SweepMatchConfig())
    errors = []
    for _ in range(12):
        pose = sim.random_free_pose(rng)
        beams = _beams(pose, REL6, rng)
        prior = pose + np.array([rng.normal(0, 0.05), rng.normal(0, 0.05), rng.normal(0, 3)])
        res = matcher.match(beams, prior=tuple(prior))
        assert res is not None and res.mode == "local"
        errors.append(_err(res, pose)[0])
    assert float(np.median(errors)) < 0.08, errors
    assert float(np.percentile(errors, 90)) < 0.15, errors


def test_desmear_beats_naive_while_driving():
    """The headline: 1 s drive at 0.45 m/s, 91 beams, same scans both ways."""
    rng = np.random.default_rng(4)
    desmear_matcher = SweepMatcher(_world(), SweepMatchConfig(), desmear=True)
    naive_matcher = SweepMatcher(_world(), SweepMatchConfig(), desmear=False)
    naive, desmeared = [], []
    for _ in range(6):
        pose = sim.random_free_pose(rng)
        beams = _beams(pose, REL2, rng, v=0.45, sweep_s=1.0)
        prior = pose + np.array([rng.normal(0, 0.05), rng.normal(0, 0.05), rng.normal(0, 3)])
        res_naive = naive_matcher.match(beams, prior=tuple(prior), motion=(0.45, 0.0))
        res_des = desmear_matcher.match(beams, prior=tuple(prior), motion=(0.45, 0.0))
        assert res_naive is not None and res_naive.desmeared is False
        assert res_des is not None and res_des.desmeared is True
        naive.append(_err(res_naive, pose)[0])
        desmeared.append(_err(res_des, pose)[0])
    med_naive, med_des = float(np.median(naive)), float(np.median(desmeared))
    assert med_des < 0.10, desmeared
    assert med_naive > 0.15, naive
    assert med_des < 0.5 * med_naive, (med_des, med_naive)


def test_desmear_tolerates_motion_estimate_error():
    rng = np.random.default_rng(5)
    matcher = SweepMatcher(_world(), SweepMatchConfig(), desmear=True)
    errors = []
    for _ in range(4):
        pose = sim.random_free_pose(rng)
        beams = _beams(pose, REL2, rng, v=0.45, sweep_s=1.0)
        prior = pose + np.array([rng.normal(0, 0.05), rng.normal(0, 0.05), rng.normal(0, 3)])
        res = matcher.match(beams, prior=tuple(prior), motion=(0.45 * 1.25, 0.0))  # +25%
        assert res is not None
        errors.append(_err(res, pose)[0])
    assert float(np.median(errors)) < 0.15, errors


def test_global_search_finds_the_basin_without_a_prior():
    rng = np.random.default_rng(6)
    matcher = SweepMatcher(_world(), SweepMatchConfig())
    errors = []
    for _ in range(3):
        pose = sim.random_free_pose(rng)
        beams = _beams(pose, REL6, rng)
        res = matcher.match(beams, prior=None)
        assert res is not None and res.mode == "global"
        errors.append(_err(res, pose)[0])
    assert min(errors) < 0.20, errors           # the search reaches the right basin
    assert all(e < 1.5 for e in errors), errors  # and never blows up


def _grid_world() -> GridWorld:
    cell, w, h = 0.05, 6.4, 3.6
    occ = np.zeros((int(h / cell), int(w / cell)), np.float32)
    for x0, y0, x1, y1 in sim.OBST:
        ix0, ix1 = max(0, int(x0 / cell)), min(occ.shape[1], int(np.ceil(x1 / cell)))
        iy0, iy1 = max(0, int(y0 / cell)), min(occ.shape[0], int(np.ceil(y1 / cell)))
        occ[iy0:iy1, ix0:ix1] = 1.0
    return GridWorld(occ, cell, occupied_thr=0.42, max_range_m=sim.MAX_RANGE, step_m=cell)


def test_grid_world_matches_against_the_occupancy_snapshot():
    rng = np.random.default_rng(8)
    matcher = SweepMatcher(_grid_world(), SweepMatchConfig())
    pose = np.array([2.0, 2.6, 45.0])
    beams = _beams(pose, REL6, rng)
    prior = pose + np.array([0.06, -0.04, 2.0])
    res = matcher.match(beams, prior=tuple(prior))
    assert res is not None
    perr, yerr = _err(res, pose)
    assert perr < 0.20, (perr, yerr)            # 5 cm cells + noise
    assert yerr < 8.0


def test_beams_from_assembly_filters_scales_and_times():
    samples = (
        ScanSample(0, 1234, 0, t_us=0),
        ScanSample(6, 0, STATUS_TIMEOUT, t_us=33),
        ScanSample(12, 500, 4, t_us=66),          # RangeStatus 4: dropped
        ScanSample(-128, 0, 0, t_us=99),          # no sample
        ScanSample(18, 3210, 0, t_us=132),
    )
    chunk = ScanChunk(scan_id=7, chunk_idx=0, chunk_count=1, first_idx=0,
                      t0_ms=0, period_us=33000, samples=samples)
    beams = beams_from_assembly(ScanAssembly(scan_id=7, chunk_count=1, chunks={0: chunk}))
    assert beams.n_total == 5 and beams.n_valid == 2
    assert beams.ranges_m[0] == pytest.approx(1.234)
    assert np.isnan(beams.ranges_m[1]) and np.isnan(beams.ranges_m[3])
    assert beams.t_s[4] == pytest.approx(0.132)
    assert beams.duration_s == pytest.approx(0.132)


def test_beams_without_per_sample_time_use_the_chunk_header():
    chunk = ScanChunk(scan_id=1, chunk_idx=0, chunk_count=1, first_idx=0,
                      t0_ms=1000, period_us=20000,
                      samples=(ScanSample(0, 1000, 0), ScanSample(2, 1000, 0)))
    beams = beams_from_assembly(ScanAssembly(scan_id=1, chunk_count=1, chunks={0: chunk}))
    assert beams.t_s[0] == pytest.approx(0.0)
    assert beams.t_s[1] == pytest.approx(0.020)


def _clean_beams(pose, world, step_deg=2.0) -> ScanBeams:
    bearings = np.arange(-90.0, 90.0 + 1e-9, step_deg)
    ranges = world.ranges_for(np.array([pose]), bearings)[0]
    return ScanBeams(bearings_deg=bearings, ranges_m=ranges,
                     t_s=np.linspace(0.0, 1.0, len(bearings)))


def test_confirm_objects_outcomes():
    world = _world()
    pose = (2.0, 2.0, 0.0)
    beams = _clean_beams(pose, world)
    r0 = float(beams.ranges_m[np.argmin(np.abs(beams.bearings_deg))])

    on_surface = SemanticObject(id="obj_0001", label="box", x=2.0 + r0, y=2.0,
                                confidence=0.9)
    nearer = SemanticObject(id="obj_0002", label="ball", x=2.0 + r0 - 0.5, y=2.0,
                            confidence=0.9)
    behind = SemanticObject(id="obj_0003", label="charger", x=1.0, y=2.0, confidence=0.9)
    far = SemanticObject(id="obj_0004", label="shelf", x=7.5, y=2.0, confidence=0.9)

    out = {c.object_id: c for c in confirm_objects(beams, pose,
                                                   [on_surface, nearer, behind, far],
                                                   tolerance_m=0.15)}
    assert out["obj_0001"].result == "confirmed"
    assert abs(out["obj_0001"].range_err_m) <= 0.15
    assert out["obj_0002"].result == "contradicted"
    assert out["obj_0002"].range_err_m > 0.3          # measured beyond the object
    assert out["obj_0003"].result == "absent"          # behind the 180-degree sweep
    assert out["obj_0004"].result == "absent"          # beyond sensor range


def test_confirm_after_a_matched_sweep():
    """End to end: simulate -> match -> confirm an object on a real surface."""
    rng = np.random.default_rng(9)
    world = _world()
    matcher = SweepMatcher(world, SweepMatchConfig())
    pose = np.array([3.0, 2.0, 0.0])
    beams = _beams(pose, REL6, rng)
    prior = pose + np.array([0.05, -0.05, 2.0])
    res = matcher.match(beams, prior=tuple(prior))
    assert res is not None and res.confidence > 0.5

    # put an object on the surface the forward beams actually hit
    forward = beams.valid_mask() & (np.abs(beams.bearings_deg) <= 8.0)
    r_forward = float(np.median(beams.ranges_m[forward]))
    obj = SemanticObject(id="obj_0009", label="box",
                         x=res.x + r_forward * math.cos(math.radians(res.yaw_deg)),
                         y=res.y + r_forward * math.sin(math.radians(res.yaw_deg)),
                         confidence=0.9)
    out = confirm_objects(beams, (res.x, res.y, res.yaw_deg), [obj], tolerance_m=0.15)
    assert out[0].result == "confirmed", out
