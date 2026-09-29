"""Opt-in real LocalVision smoke. Needs torch + transformers + cached weights.

    JEV_ROVER_REALVISION=1 .venv/bin/python -m pytest tests/test_vision_local_real.py -m realvision -q

Measured reality (2026-09-29, transformers 5.17, CPU, this host): the model
detects photographic objects reliably (a COCO photo gives cat/couch at
0.66-0.73) but NOT the synthetic renderer's flat top-down rectangles at sane
thresholds. So the synthetic-frame test asserts "the adapter runs end to end",
and the strong >=1-detection assertion is gated on JEV_ROVER_REALVISION_IMAGE
pointing at a real photo (with JEV_ROVER_REALVISION_LABELS, default "cat,couch").
"""
import os

import numpy as np
import pytest

pytestmark = pytest.mark.realvision

pytest.importorskip("torch", reason="realvision needs torch")
pytest.importorskip("transformers", reason="realvision needs transformers")

from config import RoomConfig  # noqa: E402
from perception import Perception  # noqa: E402
from synthetic import SyntheticRoom  # noqa: E402
from vision import LocalVision  # noqa: E402

from conftest import ROOT  # noqa: E402


@pytest.mark.skipif(os.environ.get("JEV_ROVER_REALVISION") != "1",
                    reason="set JEV_ROVER_REALVISION=1 to run the real model")
def test_local_adapter_runs_on_a_synthetic_frame():
    cfg = RoomConfig.load(ROOT / "config" / "room.synthetic.json")
    cfg.semantics.model.kind = "local"
    cfg.semantics.model.labels = ["table", "box"]
    cfg.semantics.model.image_shortest_edge = 400
    cfg.semantics.model.image_longest_edge = 666
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    for i in range(4):
        frame = syn.render()
        perc.process(frame, i / 15.0)

    vision = LocalVision(cfg.semantics.model)
    vision.warmup()
    dets = vision.infer(perc.frame_h, labels=cfg.semantics.model.labels)
    assert isinstance(dets, list)
    assert vision.load_s is not None and vision.load_s > 0


@pytest.mark.skipif(not os.environ.get("JEV_ROVER_REALVISION_IMAGE"),
                    reason="set JEV_ROVER_REALVISION_IMAGE to a photo with matching labels")
def test_local_adapter_detects_in_a_real_photo():
    import cv2

    image = cv2.imread(os.environ["JEV_ROVER_REALVISION_IMAGE"])
    assert image is not None, "JEV_ROVER_REALVISION_IMAGE must be readable"
    labels = [s.strip() for s in os.environ.get("JEV_ROVER_REALVISION_LABELS",
                                                "cat,couch").split(",")]
    cfg = RoomConfig.load(ROOT / "config" / "room.synthetic.json")
    cfg.semantics.model.kind = "local"
    cfg.semantics.model.labels = labels
    vision = LocalVision(cfg.semantics.model)
    dets = vision.infer(image, labels=labels)
    assert dets, f"no detections for {labels}"
    assert all(0.0 <= d.score <= 1.0 for d in dets)
    assert all(isinstance(v, int) for d in dets for v in d.bbox_px)
