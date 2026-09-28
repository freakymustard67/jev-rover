"""Geometry conventions are where robot bugs live. Pin them down."""
import math

import numpy as np
import pytest

from scene import rover_frame, sector_name, summarize_rays, wrap_deg


def test_rover_frame_cardinal_directions():
    # Facing +x (yaw 0): ahead is +x, left is +y.
    fwd, left, bearing, rng = rover_frame(1.0, 0.0, 0.0)
    assert (fwd, left, bearing) == (1.0, 0.0, 0.0)
    fwd, left, bearing, rng = rover_frame(0.0, 1.0, 0.0)
    assert abs(fwd) < 1e-9 and left == 1.0 and bearing == 90.0
    # Facing +y (yaw 90): ahead is +y, left is -x.
    fwd, left, bearing, rng = rover_frame(0.0, 1.0, 90.0)
    assert abs(fwd - 1.0) < 1e-9 and abs(left) < 1e-9 and abs(bearing) < 1e-9
    fwd, left, bearing, rng = rover_frame(1.0, 0.0, 90.0)
    assert abs(fwd) < 1e-9 and left == -1.0 and bearing == -90.0


def test_wrap_deg():
    assert wrap_deg(0) == 0
    assert wrap_deg(190) == -170
    assert wrap_deg(-190) == 170
    assert wrap_deg(180) == 180


def test_sector_names_cover_left_to_right():
    assert sector_name(0) == "ahead"
    assert sector_name(45) == "right"
    assert sector_name(-45) == "left"
    assert sector_name(-120) == "far_left"     # clamped into range
    assert sector_name(120) == "far_right"


def test_summarize_rays_reports_blockage_and_limits():
    angles = list(np.arange(-90.0, 90.01, 15.0))
    ranges = [5.0] * len(angles)
    ranges[angles.index(0.0)] = 0.4
    limits = ["unseen"] * len(angles)
    limits[angles.index(0.0)] = "obstacle"
    out = summarize_rays(angles, ranges, limits=limits)
    ahead = next(s for s in out["sectors"] if s["name"] == "ahead")
    assert ahead["blocked"] is True
    assert ahead["free_m"] == 0.4
    assert ahead["limit"] == "obstacle"
    assert out["nearest_m"] == 0.4
    assert out["clear_ahead_m"] == 0.4


def test_free_runs_split_at_the_nose():
    angles = list(np.arange(-90.0, 90.01, 10.0))
    ranges = [3.0 if abs(a) >= 20 else 0.3 for a in angles]
    out = summarize_rays(angles, ranges)
    assert out["free_runs_deg"], "expected free runs on both sides"
    assert out["widest_run_deg"] > 20.0
    for lo, hi, clr in out["free_runs_deg"]:
        assert lo < 0 and hi <= 0 or lo >= 0 or (lo <= 0 <= hi and (lo == -0.001 or hi == 0.001))
