"""Opt-in real DepthModel smoke (needs torch + transformers + torchvision).

    JEV_ROVER_REALVISION=1 .venv/bin/python -m pytest tests/test_depth_real.py -m realvision -q

This validates the model plumbing (load, metric output shape, finite values) and
measures latency; it makes no accuracy claim - the calibration limits are
documented in depth.py and the README.
"""
import os

import numpy as np
import pytest

pytestmark = pytest.mark.realvision

pytest.importorskip("torch", reason="realvision needs torch")
pytest.importorskip("transformers", reason="realvision needs transformers")
pytest.importorskip("torchvision", reason="DepthAnything's processor needs torchvision")

from config import RoomConfig  # noqa: E402
from depth import DepthModel  # noqa: E402
from synthetic import SyntheticRoom  # noqa: E402

from conftest import ROOT  # noqa: E402


@pytest.mark.skipif(os.environ.get("JEV_ROVER_REALVISION") != "1",
                    reason="set JEV_ROVER_REALVISION=1 to run the real depth model")
def test_depth_model_runs_metric_shaped():
    cfg = RoomConfig.load(ROOT / "config" / "room.synthetic.json")
    frame = SyntheticRoom(cfg).render()
    model = DepthModel()
    depth = model.infer(frame)
    assert depth.shape == frame.shape[:2], (depth.shape, frame.shape)
    assert np.isfinite(depth).all()
    assert depth.min() > 0.0
    assert model.load_s is not None and model.load_s > 0
    assert model.latencies_s and model.latencies_s[-1] > 0
    print(f"\n[depth-real] load {model.load_s:.1f}s  "
          f"infer {model.latencies_s[-1]:.1f}s  "
          f"median {float(np.median(depth)):.2f} m")
