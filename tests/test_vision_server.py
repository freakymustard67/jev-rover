"""vision_server: the RemoteVision contract over loopback, with a stub model."""
import base64
import json
import sys
import threading
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest

from semantics import Detection

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import vision_server  # noqa: E402


class StubVision:
    name = "stub:test"

    def __init__(self):
        self.cfg = type("C", (), {"box_threshold": 0.30, "text_threshold": 0.25})()
        self.calls = []

    def warmup(self):
        return None

    def infer(self, frame, *, labels=None):
        self.calls.append({"shape": frame.shape, "labels": labels,
                           "box": self.cfg.box_threshold, "text": self.cfg.text_threshold})
        return [Detection("mat", (10, 20, 30, 40), 0.9)]


@pytest.fixture
def server():
    vision = StubVision()
    service = vision_server.VisionService(vision, ["mat"])
    httpd = vision_server.ThreadingHTTPServer(
        ("127.0.0.1", 0), vision_server.make_handler(service, token="sekrit"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, vision
    httpd.shutdown()
    httpd.server_close()


def _post(port, payload, token="sekrit"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}/detect",
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _encode(frame) -> str:
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def test_roundtrip_and_thresholds(server):
    httpd, vision = server
    port = httpd.server_address[1]
    frame = np.zeros((100, 200, 3), np.uint8)
    status, body = _post(port, {"protocol": 1, "request_id": "p1",
                                "image": {"format": "jpeg", "b64": _encode(frame)},
                                "labels": ["mat"],
                                "thresholds": {"box": 0.12, "text": 0.34}})
    assert status == 200
    assert body["model"] == "stub:test"
    assert body["detections"] == [{"label": "mat", "bbox_px": [10, 20, 30, 40],
                                   "score": 0.9}]
    assert vision.calls[0]["labels"] == ["mat"]
    assert vision.calls[0]["box"] == 0.12 and vision.calls[0]["text"] == 0.34
    assert vision.cfg.box_threshold == 0.30, "per-request thresholds must be restored"


def test_health_and_unknown_path(server):
    httpd, _ = server
    port = httpd.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
        assert resp.status == 200 and json.loads(resp.read())["ok"] is True
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    assert e.value.code == 404


def test_auth_required_and_rejected(server):
    httpd, _ = server
    port = httpd.server_address[1]
    for token in ("", "wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(port, {"protocol": 1, "image": {"b64": ""}}, token=token)
        assert e.value.code == 401


def test_bad_requests_are_four_hundreds(server):
    httpd, _ = server
    port = httpd.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(port, {"protocol": 2, "image": {"b64": ""}})
    assert e.value.code == 400                      # unsupported protocol
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(port, {"protocol": 1, "image": {"b64": "!!!not-base64!!!"}})
    assert e.value.code == 400                      # undecodable image
    status, body = _post(port, {"protocol": 1,
                                "image": {"b64": _encode(np.zeros((8, 8, 3), np.uint8))},
                                "labels": []})
    assert status == 200 and body["detections"][0]["label"] == "mat", \
        "an empty request label list falls back to the server defaults"


def test_no_labels_anywhere_is_400():
    service = vision_server.VisionService(StubVision(), [])
    httpd = vision_server.ThreadingHTTPServer(
        ("127.0.0.1", 0), vision_server.make_handler(service, token=""))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(httpd.server_address[1],
                  {"protocol": 1,
                   "image": {"b64": _encode(np.zeros((8, 8, 3), np.uint8))},
                   "labels": []}, token="")
        assert e.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
