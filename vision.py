"""Real vision adapters for the semantic layer (M2).

Two adapters behind the existing ``VisionModel`` seam:

* ``LocalVision`` — open-vocabulary detector on this host via ``transformers``
  (default model: MM-GDINO-T, Apache-2.0). ``torch``/``transformers`` are
  imported inside ``_ensure()``, so the default suite and ``semantics.py``
  never require them; the load happens on the semantics worker thread.
* ``RemoteVision`` — one JSON POST per pass to a LAN endpoint speaking the
  contract below. ``tools/vision_server.py`` implements exactly this contract
  by wrapping ``LocalVision``, so switching local <-> remote is a config-only
  change (``semantics.model.kind`` + ``endpoint``).

Contract notes:
* ``Detection.bbox_px`` is always in the pixel space of the frame handed to
  ``infer()`` — full-res homography space (``Perception.frame_h``). Adapters
  that downscale internally (``input_scale``, ``jpeg_max_px``) rescale boxes
  back before returning.
* Colour verification is stage 2 and deliberately not implemented here: M2
  prompts class nouns only (``["mat", "box", ...]``), and ``canonical_label``
  maps returned phrases back onto the configured vocabulary so colour words
  cannot silently invent labels.
* No secrets here: the remote bearer token comes from an env var at call time.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

import cv2
import numpy as np

from config import VisionModelConfig
from semantics import MAX_DETECTIONS, Detection

#: Local default per the owner's D1 decision (m2-design.md §2.1).
DEFAULT_LOCAL_MODEL = "openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det"

#: CPU profile measured for this host (see the smoke tool's recorded numbers).
CPU_SHORTEST_EDGE = 400
CPU_LONGEST_EDGE = 666

Transport = Callable[[str, bytes, dict, float], "tuple[int, bytes]"]


class RemoteVisionError(RuntimeError):
    """Remote adapter failure: transport, HTTP status or malformed payload."""


# ------------------------------------------------------------------- prompts

def build_prompt(labels: list[str], *, attributes: bool = False) -> list[str]:
    """Canonical prompt phrases: lowercase, whitespace-collapsed, class nouns.

    With ``attributes`` also emit the bare class token of a multi-word label
    (FG-OVD-style hygiene; matches the processor's own merging behaviour).
    """
    out: list[str] = []
    for label in labels:
        phrase = " ".join(str(label).strip().lower().split())
        if not phrase:
            continue
        out.append(phrase)
        if attributes:
            cls = phrase.split()[-1]
            if cls != phrase:
                out.append(cls)
    return out


def merge_prompt(phrases: list[str]) -> str:
    """The serialized prompt form the GroundingDino processor expects."""
    return ". ".join(phrases) + "."


def canonical_label(phrase: str, labels: list[str]) -> str | None:
    """Map a returned phrase onto the configured vocabulary, or None.

    Exact (normalised) match first; otherwise the longest configured label whose
    tokens are all present in the phrase ("blue mat" -> "mat"). Returning None
    keeps the store vocabulary exactly the configured one.
    """
    def norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    p = norm(phrase)
    for lab in labels:
        if norm(lab) == p:
            return lab
    ptoks = set(p.split())
    best: tuple[str, int] | None = None
    for lab in labels:
        ltoks = norm(lab).split()
        if ltoks and all(t in ptoks for t in ltoks):
            if best is None or len(ltoks) > best[1]:
                best = (lab, len(ltoks))
    return best[0] if best else None


def rescale_boxes(boxes, scale: float) -> list[tuple[int, int, int, int]]:
    """Pixel boxes in a scaled image -> boxes in the original frame."""
    inv = 1.0 / scale if scale else 1.0
    out = []
    for x0, y0, x1, y1 in boxes:
        out.append((int(round(x0 * inv)), int(round(y0 * inv)),
                    int(round(x1 * inv)), int(round(y1 * inv))))
    return out


# -------------------------------------------------------------------- local

class LocalVision:
    """Open-vocabulary detector on this box. Lazy load, worker-thread only."""

    def __init__(self, cfg: VisionModelConfig):
        self.cfg = cfg
        self.model_id = cfg.model_id or DEFAULT_LOCAL_MODEL
        self.name = f"local:{self.model_id.rsplit('/', 1)[-1]}"
        self.load_s: float | None = None
        self.unknown_phrases = 0
        self.malformed_entries = 0
        self._model = None
        self._processor = None
        self._torch = None
        self._device = ""
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # -- lifecycle (called on the semantics worker thread) ------------------
    def warmup(self) -> None:
        self._ensure()
        if self.cfg.warmup:
            size = max(32, min(self.cfg.image_shortest_edge, 64))
            self.infer(np.zeros((size, size, 3), np.uint8),
                       labels=self.cfg.labels or ["object"])

    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]:
        self._ensure()
        labels = list(labels or self.cfg.labels)
        if not labels:
            return []
        img, scale = self._prepare_image(frame)
        phrases = build_prompt(labels, attributes=self.cfg.prompt_attributes)
        if not phrases:
            return []
        inputs = self._processor(
            images=img,
            text=[merge_prompt(phrases)],
            size={"shortest_edge": self.cfg.image_shortest_edge,
                  "longest_edge": self.cfg.image_longest_edge},
            return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        result = self._post_process(outputs, inputs, img)
        return self._to_detections(result, labels, scale)

    def _post_process(self, outputs, inputs, img: np.ndarray) -> dict:
        """post_process_grounded_object_detection across transformers versions."""
        kwargs = {
            "threshold": self.cfg.box_threshold,
            "text_threshold": self.cfg.text_threshold,
            "target_sizes": [(img.shape[0], img.shape[1])],
        }
        input_ids = inputs.get("input_ids") if hasattr(inputs, "get") else None
        processor = self._processor
        try:
            if input_ids is not None:
                return processor.post_process_grounded_object_detection(
                    outputs, input_ids=input_ids, **kwargs)[0]
            return processor.post_process_grounded_object_detection(outputs, **kwargs)[0]
        except TypeError:
            # transformers 5.x dropped input_ids for models that carry the text
            # tokens on the outputs; retry without it before giving up.
            return processor.post_process_grounded_object_detection(outputs, **kwargs)[0]

    def _to_detections(self, result: dict, labels: list[str], scale: float) -> list[Detection]:
        boxes = result.get("boxes")
        scores = result.get("scores")
        texts = result.get("text_labels")
        if texts is None:
            texts = result.get("labels")
        dets: list[Detection] = []
        if boxes is None or scores is None:
            return dets
        texts = list(texts) if texts is not None else [""] * len(boxes)
        for box, score, phrase in zip(np.asarray(boxes).tolist(),
                                      np.asarray(scores).tolist(), texts):
            label = canonical_label(phrase, labels)
            if label is None:
                self.unknown_phrases += 1
                continue
            x0, y0, x1, y1 = rescale_boxes([box], scale)[0]
            dets.append(Detection(label, (x0, y0, x1, y1), float(score)))
        return dets[:MAX_DETECTIONS]

    def _prepare_image(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        scale = float(self.cfg.input_scale or 1.0)
        img = frame
        if scale != 1.0:
            img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB), scale

    def _resolve_device(self, torch) -> str:
        if self.cfg.device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return self.cfg.device

    def _ensure(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if self._load_error is not None:
                raise RuntimeError(self._load_error)
            try:
                import torch
                from transformers import (AutoModelForZeroShotObjectDetection,
                                          AutoProcessor)
            except ImportError as e:                    # optional deps stay optional
                self._load_error = (
                    "local vision needs torch+transformers: pip install -r "
                    f"requirements-vision.txt, or set semantics.model.kind=remote ({e})")
                raise RuntimeError(self._load_error) from e
            t0 = time.time()
            try:
                self._torch = torch
                self._device = self._resolve_device(torch)
                if self._device == "cpu":
                    torch.set_num_threads(min(4, os.cpu_count() or 1))
                self._processor = AutoProcessor.from_pretrained(self.model_id)
                self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
                    self.model_id).to(self._device).eval()
                if self.cfg.half and self._device.startswith("cuda"):
                    self._model = self._model.half()
                self.load_s = time.time() - t0
            except Exception as e:                      # cached: one attempt per process
                self._load_error = f"{type(e).__name__}: {e}"
                raise RuntimeError(self._load_error) from e


# ------------------------------------------------------------------- remote

def _urllib_transport(url: str, body: bytes, headers: dict, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:                 # non-2xx is data, not a crash
        return int(e.code), e.read()


class RemoteVision:
    """One JSON POST per pass; the endpoint contract is documented in the
    module docstring and implemented by ``tools/vision_server.py``."""

    def __init__(self, cfg: VisionModelConfig, transport: Transport | None = None):
        self.cfg = cfg
        self.endpoint = cfg.endpoint
        self.name = f"remote:{cfg.endpoint or '(env)'}"
        self.last_ms: float | None = None
        self.unknown_phrases = 0
        self.malformed_entries = 0
        self._transport = transport or _urllib_transport
        self._request_id = 0

    def warmup(self) -> None:
        pass                                            # nothing to load locally

    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]:
        labels = list(labels or self.cfg.labels)
        endpoint = self.cfg.endpoint or os.environ.get("JEV_ROVER_VISION_ENDPOINT", "")
        if not endpoint:
            raise RemoteVisionError(
                "remote vision needs semantics.model.endpoint or JEV_ROVER_VISION_ENDPOINT")
        if not labels:
            return []
        token = os.environ.get(self.cfg.auth_env, "")
        b64, scale = self._encode(frame)
        self._request_id += 1
        body = json.dumps({
            "protocol": 1,
            "request_id": f"p{self._request_id:04d}",
            "image": {"format": "jpeg", "b64": b64, "width": int(frame.shape[1]),
                      "height": int(frame.shape[0]), "scale": round(scale, 4)},
            "labels": labels,
            "thresholds": {"box": self.cfg.box_threshold, "text": self.cfg.text_threshold},
            "timeout_s": self.cfg.timeout_s,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        t0 = time.time()
        try:
            status, data = self._transport(endpoint, body, headers, self.cfg.timeout_s)
        except TimeoutError:
            raise                                       # per contract: timeout stays a timeout
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(str(e.reason)) from e
            raise RemoteVisionError(f"{type(e).__name__}: {e}") from e
        except OSError as e:
            raise RemoteVisionError(f"{type(e).__name__}: {e}") from e
        self.last_ms = (time.time() - t0) * 1000.0
        if status != 200:
            raise RemoteVisionError(f"HTTP {status}: {data[:200]!r}")
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as e:
            raise RemoteVisionError(f"malformed JSON from {endpoint}: {e}") from e
        return self._to_detections(payload.get("detections", []), labels, scale)

    def _encode(self, frame: np.ndarray) -> tuple[str, float]:
        scale = float(self.cfg.input_scale or 1.0)
        img = frame
        if scale != 1.0:
            img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if self.cfg.jpeg_max_px:
            longest = max(img.shape[:2])
            if longest > self.cfg.jpeg_max_px:
                s = self.cfg.jpeg_max_px / float(longest)
                img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                scale *= s
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if not ok:
            raise RemoteVisionError("JPEG encode failed")
        return base64.b64encode(buf.tobytes()).decode("ascii"), scale

    def _to_detections(self, entries: list, labels: list[str], scale: float) -> list[Detection]:
        dets: list[Detection] = []
        if not isinstance(entries, list):
            raise RemoteVisionError("payload 'detections' is not a list")
        for entry in entries[:MAX_DETECTIONS]:
            try:
                label = canonical_label(entry["label"], labels)
                if label is None:
                    self.unknown_phrases += 1
                    continue
                x0, y0, x1, y1 = rescale_boxes([entry["bbox_px"][:4]], scale)[0]
                dets.append(Detection(label, (x0, y0, x1, y1),
                                      float(entry.get("score", 1.0))))
            except (KeyError, TypeError, ValueError):
                self.malformed_entries += 1
        return dets


# ------------------------------------------------------------------ factory

def make_vision(cfg: VisionModelConfig):
    """Adapter factory for the real kinds; ``fake`` stays in semantics.py."""
    if cfg.kind == "local":
        return LocalVision(cfg)
    if cfg.kind == "remote":
        return RemoteVision(cfg)
    raise ValueError(f"not a real adapter: {cfg.kind!r}")
