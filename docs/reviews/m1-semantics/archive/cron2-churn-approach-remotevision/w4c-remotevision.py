"""W4C prototype: RemoteVision adapter (the M2 seam) - stdlib HTTP, env-only secrets.

Implements the VisionModel protocol (semantics.py:64-68 on PR tip bec1d91). Every
failure raises (RemoteVisionError / clean exception) so SemanticsWorker._run's
blanket except (semantics.py:542-547) engages the degrade path: PassResult(error)
-> SemanticsRunner.poll sets failure_cooldown_s (semantics.py:625-627).

Contract:
  * endpoint: env JEV_VISION_ENDPOINT wins, else constructor arg. Never in logs.
  * token:    env JEV_VISION_TOKEN, read at CALL time, sent only as an
              `Authorization: Bearer` header. Never stored, never logged, never
              serialized; error text is scrubbed defensively.
  * POST JSON: {"protocol":1,"request_id":...,"labels":[...],
                "image_b64":...,"width":W,"height":H}
    (maps onto the nested `image` object of docs/reviews/m1-semantics/m2-design.md
    section 2.4; schema reconciliation is an M2 task, see report)
  * response: {"detections":[{"label":str,"bbox_px":[x0,y0,x1,y1],"score":float}]}
    bbox_px is in pixels of the image that was sent. Malformed entries are
    skipped and counted (self.last_skipped); a response where EVERY entry is
    invalid, or a top-level schema violation, raises (fail loud, not silent-empty).

Loopback only by design of the caller: this prototype is exercised against a
127.0.0.1 mock (w4c_mockserver.py).
"""
from __future__ import annotations

import base64
import json
import math
import os
import socket
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np

from semantics import MAX_DETECTIONS, Detection

ENDPOINT_ENV = "JEV_VISION_ENDPOINT"
TOKEN_ENV = "JEV_VISION_TOKEN"
PROTOCOL = 1
MAX_RESPONSE_BYTES = 1024 * 1024          # 1 MiB response cap
MAX_FRAME_PX = 8_000_000                  # local sanity cap on what we encode


class RemoteVisionError(RuntimeError):
    """Any remote-adapter failure. Deliberately scrubbed of the token."""


def _scrub(text: str) -> str:
    """Defense in depth: the token must never appear in any error text."""
    tok = os.environ.get(TOKEN_ENV, "")
    if tok:
        text = text.replace(tok, "***")
    return text


class RemoteVision:
    """HTTP vision adapter. `name` is static - it is serialized into artifacts."""

    def __init__(self, endpoint: str = "", timeout_s: float = 10.0, *,
                 jpeg_quality: int = 85, name: str = "remote-vision-w4c-v0",
                 opener=None):
        self._endpoint_cfg = endpoint
        self.timeout_s = float(timeout_s)
        self.jpeg_quality = int(jpeg_quality)
        self.name = name                  # MUST NOT embed endpoint/token (artifacts)
        self.last_skipped = 0
        self._opener = opener or urllib.request.urlopen

    # ------------------------------------------------------------------ util
    def _endpoint(self) -> str:
        return (os.environ.get(ENDPOINT_ENV, "") or self._endpoint_cfg).strip()

    @staticmethod
    def _encode_jpeg(frame: np.ndarray, quality: int) -> bytes:
        if frame is None or frame.ndim != 3:
            raise RemoteVisionError("expected a BGR frame")
        h, w = frame.shape[:2]
        if w * h > MAX_FRAME_PX:
            raise RemoteVisionError(f"frame too large to encode: {w}x{h}")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RemoteVisionError("JPEG encode failed")
        return buf.tobytes()

    # ----------------------------------------------------------------- infer
    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]:
        url = self._endpoint()
        if not url:
            raise RemoteVisionError(
                f"remote vision endpoint is not set (env {ENDPOINT_ENV} or config)")
        h, w = frame.shape[:2]
        jpeg = self._encode_jpeg(frame, self.jpeg_quality)
        payload = {
            "protocol": PROTOCOL,
            "request_id": uuid.uuid4().hex[:12],
            "labels": [str(x) for x in (labels or [])],
            "image_b64": base64.b64encode(jpeg).decode("ascii"),
            "width": w,
            "height": h,
            "format": "jpeg",
            "timeout_ms": int(round(self.timeout_s * 1000)),
        }
        headers = {"Content-Type": "application/json"}
        token = os.environ.get(TOKEN_ENV, "")
        if token:
            # Header only. Never in the URL (endpoint is config-visible and may be
            # logged/committed) and never in the body (bodies get captured).
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                status = int(getattr(resp, "status", 200))
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as e:                    # 4xx/5xx
            raise RemoteVisionError(_scrub(f"HTTP {e.code}: {e.reason}")) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            reason = getattr(e, "reason", e)
            raise RemoteVisionError(_scrub(
                f"transport error: {type(reason).__name__}: {reason}")) from None
        if not (200 <= status < 300):
            raise RemoteVisionError(f"HTTP {status}")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RemoteVisionError(f"response exceeds {MAX_RESPONSE_BYTES} byte cap")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RemoteVisionError(_scrub(f"malformed JSON: {type(e).__name__}")) from None
        if not isinstance(doc, dict) or not isinstance(doc.get("detections"), list):
            raise RemoteVisionError("schema: expected {'detections': [...]}")
        entries = doc["detections"][:MAX_DETECTIONS]
        dets: list[Detection] = []
        skipped = 0
        for item in entries:
            parsed = _parse_entry(item, w, h)
            if parsed is None:
                skipped += 1
                continue
            dets.append(parsed)
        self.last_skipped = skipped
        if entries and not dets:
            # All-invalid is a protocol failure, not an empty scene. (m2-design
            # section 2.4 says skip+count; this prototype fails loud instead - a
            # recommendation for the real adapter, see report.)
            raise RemoteVisionError(f"all {skipped} detection entries malformed")
        return dets


def _parse_entry(item, w: int, h: int) -> Detection | None:
    """Validate one detection entry; None = malformed (skipped, counted)."""
    if not isinstance(item, dict):
        return None
    label = item.get("label")
    bbox = item.get("bbox_px")
    score = item.get("score", 1.0)
    if not isinstance(label, str) or not label.strip():
        return None
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in bbox)):
        return None
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if x1 <= x0 or y1 <= y0:                                   # degenerate box
        return None
    pad = 8.0                                                  # rounding tolerance
    if x0 < -pad or y0 < -pad or x1 > w + pad or y1 > h + pad:  # outside sent image
        return None
    if (not isinstance(score, (int, float)) or isinstance(score, bool)
            or not math.isfinite(score)):
        return None
    score = min(1.0, max(0.0, float(score)))
    return Detection(label, (int(round(x0)), int(round(y0)),
                             int(round(x1)), int(round(y1))), score)
