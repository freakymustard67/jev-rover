"""Depth calibration: analytic pinhole+plane fixtures, plane fit, mapping metrics."""
import numpy as np
import pytest

from depth import (calibrate_from_depth, depth_overlay, floor_polygon_px,
                   focal_px_from_hfov, median_depth)

#: A synthetic camera: 1.6 m above the floor, pitched 25 deg down, hfov 65 deg.
#: Ground truth is exact - every pixel's depth comes from ray-plane intersection
#: with the TRUE plane, so the fit and the mapping have something to be wrong
#: against.
CAM = dict(w=1280, h=720, hfov=65.0, pitch_deg=25.0, cam_h=1.6)


def synthetic_camera(**overrides) -> dict:
    c = dict(CAM)
    c.update(overrides)
    w, h, hfov = c["w"], c["h"], c["hfov"]
    f = focal_px_from_hfov(hfov, w)
    cx, cy = w / 2.0, h / 2.0
    th = np.radians(c["pitch_deg"])
    n = np.array([0.0, -np.sin(th), -np.cos(th)])     # room-up in camera coords
    off = -c["cam_h"]                                 # plane: n . X = offset
    ys, xs = np.mgrid[0:h, 0:w]
    r = np.stack([(xs - cx) / f, (ys - cy) / f, np.ones_like(xs, float)], axis=-1)
    t = off / (r @ n)
    c.update(f=f, cx=cx, cy=cy, n=n, off=off,
             depth=np.where(t > 0, t, np.nan).astype(np.float32))
    return c


def test_clean_recovery():
    cam = synthetic_camera()
    res = calibrate_from_depth(cam["depth"], cam["hfov"])
    assert res is not None
    assert np.linalg.norm(res.plane.normal - cam["n"]) < 0.02
    assert abs(res.plane.height_m - cam["cam_h"]) < 0.03
    assert res.plane.inlier_ratio > 0.9
    assert res.plane.residual_median_m < 0.01
    assert abs(res.f_px - cam["f"]) < 1e-6


def test_noise_and_outlier_robustness():
    cam = synthetic_camera()
    rng = np.random.default_rng(1)
    d = cam["depth"] * (1 + rng.normal(0, 0.03, cam["depth"].shape))
    d = d + rng.normal(0, 0.02, d.shape)
    d[300:450, 400:700] -= 0.6                        # a table 0.6 m closer
    res = calibrate_from_depth(d.astype(np.float32), cam["hfov"], tol_m=0.06)
    assert res is not None
    assert abs(res.plane.height_m - cam["cam_h"]) < 0.08
    assert np.linalg.norm(res.plane.normal - cam["n"]) < 0.08
    assert 0.4 < res.plane.inlier_ratio < 0.95
    assert res.mapping.floor_size_m[0] > 1.0 and res.mapping.floor_size_m[1] > 0.5


def test_mapping_preserves_metric_distances():
    """The homography and the true plane must agree on distances (orthonormal frame)."""
    cam = synthetic_camera()
    res = calibrate_from_depth(cam["depth"], cam["hfov"])
    px = np.array([[300.0, 650.0], [900.0, 400.0]])
    r = np.stack([(px[:, 0] - cam["cx"]) / cam["f"], (px[:, 1] - cam["cy"]) / cam["f"],
                  np.ones(2)], axis=1)
    t = cam["off"] / (r @ cam["n"])
    truth = float(np.linalg.norm((r * t[:, None])[0] - (r * t[:, None])[1]))
    p = np.hstack([px, np.ones((2, 1))]) @ res.mapping.homography.T
    p = p[:, :2] / p[:, 2:3]
    got = float(np.linalg.norm(p[0] - p[1]))
    assert abs(got - truth) < 0.01, (got, truth)


def test_floor_polygon_and_overlay():
    cam = synthetic_camera()
    res = calibrate_from_depth(cam["depth"], cam["hfov"])
    poly = floor_polygon_px(res.plane, (cam["h"], cam["w"]))
    assert poly is not None and 4 <= len(poly) <= 12
    assert poly[:, 0].min() >= 0 and poly[:, 0].max() < cam["w"]
    assert poly[:, 1].min() >= 0 and poly[:, 1].max() < cam["h"]

    frame = np.full((cam["h"], cam["w"], 3), 90, np.uint8)
    img = depth_overlay(frame, cam["depth"], res)
    assert img.shape[0] == cam["h"] and img.shape[1] > cam["w"]


def test_garbage_depth_returns_none():
    assert calibrate_from_depth(np.zeros((720, 1280), np.float32), 65.0) is None
    assert calibrate_from_depth(np.full((720, 1280), np.nan, np.float32), 65.0) is None
    # a constant-depth image is a frontal plane (a wall), not a floor
    assert calibrate_from_depth(np.full((720, 1280), 1.0, np.float32), 65.0) is None


def test_median_depth_and_focal():
    a = np.ones((4, 4), np.float32)
    assert np.allclose(median_depth([a, a * 3, a * 2]), 2.0)
    assert abs(focal_px_from_hfov(90.0, 1000) - 500.0) < 1e-9
    with pytest.raises(ValueError):
        focal_px_from_hfov(0.0, 100)
