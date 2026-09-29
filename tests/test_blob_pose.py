"""Blob pose: background subtraction -> floor position + motion heading."""
from dataclasses import replace

import cv2
import numpy as np
import pytest

from config import RoomConfig
from perception import BlobPoseEstimator, Homography, Perception
from scene import Pose, wrap_deg
from synthetic import SyntheticRoom

from conftest import ROOT


def _H() -> Homography:
    # 1280x720 px <-> 6.4x3.6 m, y up in world (the synthetic room's mapping)
    return Homography([[0, 0], [1280, 0], [1280, 720], [0, 720]],
                      [[0, 3.6], [6.4, 3.6], [6.4, 0], [0, 0]])


def _scene(blob_x: float, blob_y: float, size=(720, 1280)):
    bg = np.full((*size, 3), 130, np.uint8)
    frame = bg.copy()
    cv2.rectangle(frame, (int(blob_x - 20), int(blob_y - 30)),
                  (int(blob_x + 20), int(blob_y)), (25, 25, 25), -1)
    return bg, frame


def test_static_reference_position_and_uncertainty():
    bg, frame = _scene(600, 400)
    est = BlobPoseEstimator(_H(), background_bgr=bg)
    fix = est.update(frame, 0.0)
    assert fix is not None
    expected = _H().img_to_world(np.array([[600.0, 400.0]]))[0]
    assert abs(fix.x - expected[0]) < 0.03 and abs(fix.y - expected[1]) < 0.03
    assert fix.area_px > 800
    assert fix.heading_uncertain is True, "one frame: no motion, heading unknown"


def test_heading_from_motion_then_held_while_still():
    bg, _ = _scene(600, 400)
    est = BlobPoseEstimator(_H(), background_bgr=bg)
    for i in range(12):                                   # ~0.4 m/s in +world-x
        _, frame = _scene(600 + 8 * i, 400)
        fix = est.update(frame, i * 0.1)
    assert fix is not None and fix.heading_deg is not None
    assert abs(wrap_deg(fix.heading_deg - 0.0)) < 20.0, fix.heading_deg
    assert fix.heading_uncertain is False
    assert fix.speed_mps > 0.2

    _, still = _scene(600 + 8 * 11, 400)
    for j in range(14):                                   # stop for 1.4 s
        fix = est.update(still, 1.2 + j * 0.1)
    assert fix is not None
    assert fix.heading_uncertain is True, "stationary: the heading is held, not measured"
    assert fix.heading_deg is not None, "...and the last heading is retained"


def test_oversized_foreground_is_rejected():
    bg, _ = _scene(600, 400)
    est = BlobPoseEstimator(_H(), background_bgr=bg)
    everything = np.full_like(bg, 20)
    assert est.update(everything, 0.0) is None


def test_mog2_path_waits_for_warmup():
    _, frame = _scene(600, 400)
    est = BlobPoseEstimator(_H(), background_bgr=None, warmup_frames=5)
    for i in range(3):
        assert est.update(frame, i * 0.1) is None
    assert not est.warm


def test_perception_blob_mode_tracks_the_synthetic_rover(synth_cfg, tmp_path):
    cfg = replace(synth_cfg, rover=replace(synth_cfg.rover, pose_source="blob"))
    syn = SyntheticRoom(cfg, draw_rover=False)
    bg_path = tmp_path / "empty_room.png"
    assert cv2.imwrite(str(bg_path), syn.render())
    cfg = replace(cfg, floor=replace(cfg.floor, background=str(bg_path)))
    syn.draw_rover = True
    syn.rover = Pose(3.0, 1.8, 0.0)
    perc = Perception(cfg)
    scene = None
    for i in range(6):
        scene = perc.process(syn.render(), i / 14.0)
    assert scene.quality.pose_source in ("blob", "dead_reckon"), scene.quality
    assert abs(scene.pose.x - 3.0) < 0.3 and abs(scene.pose.y - 1.8) < 0.3
    assert scene.quality.heading_uncertain is True
    assert scene.quality.blob_area_px is not None and scene.quality.blob_area_px > 100

    # drive +x for ~0.8 s: the heading comes back
    for i in range(6, 16):
        syn.step(0.5, 0.0, 1 / 14.0)
        scene = perc.process(syn.render(), i / 14.0)
    assert scene.quality.pose_source == "blob"
    assert scene.quality.heading_uncertain is False, scene.quality
    assert abs(wrap_deg(scene.pose.yaw_deg)) < 25.0, scene.pose
    assert scene.quality.pose_speed_mps is not None and scene.quality.pose_speed_mps > 0.2


def test_pose_source_config_validates(synth_cfg):
    raw = synth_cfg.to_dict()
    raw["rover"]["pose_source"] = "blob"
    cfg = RoomConfig.from_dict(raw)
    assert cfg.rover.pose_source == "blob"
    raw["rover"]["pose_source"] = "magic"
    from config import ConfigError
    with pytest.raises(ConfigError):
        RoomConfig.from_dict(raw)


def test_quality_fields_serialize(synth_cfg):
    from scene import PerceptionQuality, Scene
    scene = Scene(t=1.0)
    scene.quality.heading_uncertain = True
    scene.quality.blob_area_px = 1234
    scene.quality.pose_speed_mps = 0.0
    d = scene.to_dict()
    assert d["quality"]["heading_uncertain"] is True
    assert d["quality"]["blob_area_px"] == 1234
    assert PerceptionQuality().pose_source == "tag"
