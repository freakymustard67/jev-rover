"""Minimal HTTP server exposing LocalVision in the RemoteVision contract.

One JSON POST per request; this is the server side of `vision.RemoteVision`:

    POST /detect
    {"protocol": 1, "request_id": "p0001",
     "image": {"format": "jpeg", "b64": "<base64>", "width": 1280, "height": 720,
               "scale": 0.5},
     "labels": ["mat", "box"],
     "thresholds": {"box": 0.30, "text": 0.25},
     "timeout_s": 8.0}

    200
    {"model": "local:mm_grounding_dino_tiny_o365v1_goldg_v3det", "ms": 412,
     "detections": [{"label": "mat", "bbox_px": [412, 288, 690, 470], "score": 0.87}]}

`bbox_px` is in the pixel space of the image that was SENT (the server never
rescales); the client multiplies by 1/scale. The model is loaded and warmed up
at startup so the first real request is not paying a 30 s cold load.

Second machine, install + serve:

    git clone <repo> && cd jev-rover
    uv venv .venv --python python3
    uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python .venv/bin/python -r requirements-vision.txt
    .venv/bin/python tools/vision_server.py --host 0.0.0.0 --port 8080 --labels "mat,box,ball"

Auth: if JEV_ROVER_VISION_TOKEN is set, every request must carry
`Authorization: Bearer <token>` (checked in constant time). Binding a
non-loopback address without a token prints a loud warning; the token is never
logged and never echoed.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from config import RoomConfig
from vision import LocalVision

MAX_BODY = 16 * 1024 * 1024            # 16 MB: a 1080p JPEG is ~1 MB at q85


class VisionService:
    """Serialized LocalVision wrapper: thresholds per request, one inference at a time."""

    def __init__(self, vision, labels: list[str] | None = None):
        self.vision = vision
        self.labels = labels or []
        self._lock = threading.Lock()
        self.requests = 0
        self.errors = 0

    def detect(self, b64: str, labels: list[str], thresholds: dict) -> list[dict]:
        raw = np.frombuffer(base64.b64decode(b64), np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("image did not decode")
        with self._lock:
            box = float(thresholds.get("box", self.vision.cfg.box_threshold))
            text = float(thresholds.get("text", self.vision.cfg.text_threshold))
            old_box, old_text = self.vision.cfg.box_threshold, self.vision.cfg.text_threshold
            self.vision.cfg.box_threshold, self.vision.cfg.text_threshold = box, text
            try:
                dets = self.vision.infer(frame, labels=labels)
            finally:
                self.vision.cfg.box_threshold, self.vision.cfg.text_threshold = old_box, old_text
            self.requests += 1
        return [{"label": d.label, "bbox_px": [int(v) for v in d.bbox_px],
                 "score": round(float(d.score), 4)} for d in dets]


def make_handler(service: VisionService, token: str = ""):
    class Handler(BaseHTTPRequestHandler):
        server_version = "jev-rover-vision/1"

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            return hmac.compare_digest(header.encode(), expected.encode())

        def do_GET(self):                                  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True, "model": service.vision.name,
                                 "requests": service.requests})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):                                 # noqa: N802
            if self.path != "/detect":
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self._send(413, {"error": f"body length {length} out of range"})
                    return
                req = json.loads(self.rfile.read(length))
                if int(req.get("protocol", 0)) != 1:
                    self._send(400, {"error": "unsupported protocol"})
                    return
                labels = [str(l) for l in req.get("labels", [])] or service.labels
                if not labels:
                    self._send(400, {"error": "no labels"})
                    return
                t0 = time.time()
                dets = service.detect(str(req["image"]["b64"]), labels,
                                      req.get("thresholds") or {})
                self._send(200, {"model": service.vision.name,
                                 "ms": round((time.time() - t0) * 1000.0, 1),
                                 "detections": dets})
            except ValueError as e:                         # bad request content
                service.errors += 1
                self._send(400, {"error": str(e)[:200]})
            except Exception as e:                          # never leak internals
                service.errors += 1
                self._send(500, {"error": f"{type(e).__name__}: {e}"[:200]})

        def log_message(self, fmt, *args):                  # keep stdout quiet/clean
            if os.environ.get("JEV_ROVER_VISION_LOG"):
                super().log_message(fmt, *args)

    return Handler


def build_service(args) -> VisionService:
    cfg = RoomConfig.load(args.config)
    cfg.semantics.model.kind = "local"
    if args.labels:
        cfg.semantics.model.labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    if args.model_id:
        cfg.semantics.model.model_id = args.model_id
    if args.shortest_edge:
        cfg.semantics.model.image_shortest_edge = args.shortest_edge
    if args.longest_edge:
        cfg.semantics.model.image_longest_edge = args.longest_edge
    if not cfg.semantics.model.labels:
        raise SystemExit("--labels (or semantics.model.labels in the config) is required")
    print(f"[vision-server] loading {cfg.semantics.model.model_id or 'default'} "
          f"(labels: {', '.join(cfg.semantics.model.labels)}) ...", flush=True)
    vision = LocalVision(cfg.semantics.model)
    t0 = time.time()
    vision.warmup()
    print(f"[vision-server] ready in {time.time() - t0:.1f}s "
          f"(weights load {vision.load_s:.1f}s)", flush=True)
    return VisionService(vision, cfg.semantics.model.labels)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config/room.synthetic.json")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--labels", default=None, help="comma-separated default labels")
    p.add_argument("--model-id", default=None)
    p.add_argument("--shortest-edge", type=int, default=None)
    p.add_argument("--longest-edge", type=int, default=None)
    args = p.parse_args(argv)

    token = os.environ.get("JEV_ROVER_VISION_TOKEN", "")
    service = build_service(args)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(service, token))
    advertised = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"[vision-server] serving on http://{advertised}:{args.port}/detect "
          f"(health: /health)", flush=True)
    if args.host not in ("127.0.0.1", "localhost") and not token:
        print("[vision-server] WARNING: bound to a non-loopback address without "
              "JEV_ROVER_VISION_TOKEN; anyone on the LAN can use the model", flush=True)
    print(f"[vision-server] client config: \"kind\": \"remote\", "
          f"\"endpoint\": \"http://{advertised}:{args.port}/detect\"", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[vision-server] stopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
