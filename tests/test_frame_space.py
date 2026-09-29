"""Frame-space contract (m2-design.md §1): consumers get `Perception.frame_h`.

Without intrinsics, `frame_h` IS the raw capture; with intrinsics configured it
is the undistorted frame, and the semantics worker must receive that space —
the raw capture is not the space the homography, polygon and probe live in.
"""
import json
import time
from dataclasses import replace

import cv2
import numpy as np
import pytest

import calibrate
from config import RoomConfig
from perception import Perception
from semantics import SemanticsRunner
from synthetic import SyntheticRoom

from conftest import ROOT


def _cfg() -> RoomConfig:
    return RoomConfig.load(ROOT / "config" / "room.synthetic.json")


def _intrinsics_file(tmp_path) -> str:
    path = tmp_path / "camera.json"
    path.write_text(json.dumps({
        "camera_matrix": [[800.0, 0.0, 640.0], [0.0, 800.0, 360.0], [0.0, 0.0, 1.0]],
        "distortion": [0.0, 0.0, 0.0, 0.0, 0.0],
        "resolution": [1280, 720],
    }))
    return str(path)


def test_frame_h_identity_without_intrinsics():
    cfg = _cfg()
    perc = Perception(cfg)
    raw = SyntheticRoom(cfg).render()
    perc.process(raw, 0.0)
    assert perc.frame_h is raw, "no intrinsics -> the raw capture is homography space"


def test_frame_h_is_the_undistorted_frame(tmp_path, monkeypatch):
    cfg = _cfg()
    cfg.camera.intrinsics = _intrinsics_file(tmp_path)
    sentinel = np.full((720, 1280, 3), 7, np.uint8)
    monkeypatch.setattr(cv2, "undistort", lambda frame, K, dist, *a, **kw: sentinel)
    perc = Perception(cfg)
    perc.process(np.zeros((720, 1280, 3), np.uint8), 0.0)
    assert perc.frame_h is sentinel


class _RecordingVision:
    name = "recording"

    def __init__(self):
        self.seen = []

    def infer(self, frame, *, labels=None):
        self.seen.append(np.array(frame))
        return []


def test_worker_receives_homography_space(tmp_path, monkeypatch):
    """The run.py call pattern: maybe_pass(..., perception.frame_h)."""
    cfg = _cfg()
    cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=str(tmp_path)))
    cfg.camera.intrinsics = _intrinsics_file(tmp_path)
    sentinel = np.full((720, 1280, 3), 7, np.uint8)
    monkeypatch.setattr(cv2, "undistort", lambda frame, K, dist, *a, **kw: sentinel)
    perc = Perception(cfg)
    perc.process(np.zeros((720, 1280, 3), np.uint8), 0.0)

    vision = _RecordingVision()
    runner = SemanticsRunner(cfg, "frametest", vision)
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), perc.frame_h, force=True)
        for _ in range(100):
            if vision.seen:
                break
            time.sleep(0.01)
        assert vision.seen, "worker never ran"
        seen = vision.seen[0]
        assert seen.shape == sentinel.shape
        assert seen[0, 0, 0] == 7 and seen[719, 1279, 2] == 7, \
            "worker must receive homography space, not the raw capture"
    finally:
        runner.close()


def test_calibrate_homography_space(tmp_path, monkeypatch):
    cfg = _cfg()
    frame = np.zeros((10, 10, 3), np.uint8)
    assert calibrate._homography_space(frame, cfg) is frame, "no intrinsics -> identity"

    cfg.camera.intrinsics = _intrinsics_file(tmp_path)
    out = np.full((10, 10, 3), 9, np.uint8)
    monkeypatch.setattr(cv2, "undistort", lambda f, K, d, *a, **kw: out)
    assert calibrate._homography_space(frame, cfg) is out

    cfg.camera.intrinsics = str(tmp_path / "missing.json")
    with pytest.raises(SystemExit):
        calibrate._homography_space(frame, cfg)
