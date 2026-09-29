"""RemoteVision: request shape, scaling, secrets, error mapping (stub transport)."""
import base64
import json

import cv2
import numpy as np
import pytest

from config import VisionModelConfig
from vision import RemoteVision, RemoteVisionError


class StubTransport:
    def __init__(self, status: int = 200, body: bytes = b"{}", exc: Exception | None = None):
        self.status, self.body, self.exc = status, body, exc
        self.calls: list = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body.decode()), dict(headers), timeout))
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


def _cfg(**kw) -> VisionModelConfig:
    base = dict(kind="remote", endpoint="http://vision.local/detect", labels=["mat"],
                timeout_s=8.0)
    base.update(kw)
    return VisionModelConfig(**base)


def test_request_shape_jpeg_and_rescaling():
    frame = np.zeros((200, 400, 3), np.uint8)
    body = json.dumps({"model": "stub", "detections": [
        {"label": "mat", "bbox_px": [50, 25, 150, 75], "score": 0.9}]}).encode()
    tr = StubTransport(200, body)
    v = RemoteVision(_cfg(jpeg_max_px=200), transport=tr)
    dets = v.infer(frame)

    url, req, headers, timeout = tr.calls[0]
    assert url == "http://vision.local/detect" and timeout == 8.0
    assert req["protocol"] == 1 and req["labels"] == ["mat"]
    assert req["thresholds"] == {"box": 0.30, "text": 0.25}
    assert headers["Content-Type"] == "application/json"

    raw = base64.b64decode(req["image"]["b64"])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and max(img.shape[:2]) == 200, "JPEG must be downscaled"
    assert req["image"]["scale"] == 0.5
    assert req["image"]["width"] == 400 and req["image"]["height"] == 200

    assert dets[0].label == "mat" and dets[0].bbox_px == (100, 50, 300, 150)
    assert dets[0].score == pytest.approx(0.9)


def test_canonicalisation_and_unknown_dropped():
    body = json.dumps({"detections": [
        {"label": "blue mat", "bbox_px": [0, 0, 10, 10], "score": 0.8},
        {"label": "llama", "bbox_px": [0, 0, 10, 10], "score": 0.8}]}).encode()
    v = RemoteVision(_cfg(), transport=StubTransport(200, body))
    dets = v.infer(np.zeros((50, 50, 3), np.uint8))
    assert [d.label for d in dets] == ["mat"]
    assert v.unknown_phrases == 1


def test_detections_capped_at_64():
    entries = [{"label": "mat", "bbox_px": [0, 0, 10, 10], "score": 0.5} for _ in range(100)]
    v = RemoteVision(_cfg(), transport=StubTransport(200, json.dumps({"detections": entries}).encode()))
    assert len(v.infer(np.zeros((10, 10, 3), np.uint8))) == 64


def test_missing_endpoint_names_both_sources(monkeypatch):
    monkeypatch.delenv("JEV_ROVER_VISION_ENDPOINT", raising=False)
    v = RemoteVision(_cfg(endpoint=""), transport=StubTransport())
    with pytest.raises(RemoteVisionError, match="endpoint"):
        v.infer(np.zeros((10, 10, 3), np.uint8))


def test_endpoint_from_env_fallback(monkeypatch):
    monkeypatch.setenv("JEV_ROVER_VISION_ENDPOINT", "http://env.local/v1")
    tr = StubTransport(200, b'{"detections": []}')
    v = RemoteVision(_cfg(endpoint=""), transport=tr)
    v.infer(np.zeros((10, 10, 3), np.uint8))
    assert tr.calls[0][0] == "http://env.local/v1"


def test_token_comes_from_env_and_never_reaches_the_body(monkeypatch):
    monkeypatch.setenv("JEV_TEST_TOKEN", "sekrit-token")
    tr = StubTransport(200, b'{"detections": []}')
    v = RemoteVision(_cfg(auth_env="JEV_TEST_TOKEN"), transport=tr)
    v.infer(np.zeros((10, 10, 3), np.uint8))
    url, req, headers, timeout = tr.calls[0]
    assert headers["Authorization"] == "Bearer sekrit-token"
    assert "sekrit-token" not in json.dumps(req)


def test_http_error_mapping():
    v = RemoteVision(_cfg(), transport=StubTransport(502, b"bad gateway"))
    with pytest.raises(RemoteVisionError, match="HTTP 502"):
        v.infer(np.zeros((10, 10, 3), np.uint8))


def test_malformed_json_and_entries():
    v = RemoteVision(_cfg(), transport=StubTransport(200, b"not json"))
    with pytest.raises(RemoteVisionError, match="malformed JSON"):
        v.infer(np.zeros((10, 10, 3), np.uint8))

    body = json.dumps({"detections": [
        {"nope": 1},                                    # missing label/bbox
        {"label": "mat", "bbox_px": [1, 2], "score": 0.5},   # short bbox
        {"label": "mat", "bbox_px": [1, 2, 3, 4], "score": 0.5}]}).encode()
    v = RemoteVision(_cfg(), transport=StubTransport(200, body))
    dets = v.infer(np.zeros((10, 10, 3), np.uint8))
    assert len(dets) == 1 and v.malformed_entries == 2


def test_timeout_propagates():
    v = RemoteVision(_cfg(), transport=StubTransport(exc=TimeoutError("no answer")))
    with pytest.raises(TimeoutError):
        v.infer(np.zeros((10, 10, 3), np.uint8))
