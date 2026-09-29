## Open-vocabulary detection landscape for jev-rover M2 (verified 2026-09-29)

All weights/numbers verified today via HF model API, repo raw READMEs, arXiv HTML/API. `[derived]` = extrapolation, not measurement.

### Ranked shortlist
1. **Grounding DINO family, MM-GDINO-T weights** — best open-weights accuracy + attribute handling, Apache-2.0, one-call transformers API, sub-second-to-1s/pass class. Default LocalVision detector.
2. **YOLOE / YOLOE-26 (Ultralytics)** — 4–25 M params, near-CNN speed, CPU/4 GB fallback, `set_classes()`; AGPL-3.0 and weakest attribute binding.
3. **SAM 3 / SAM 3.1 (Meta)** — prompts handle colours ("yellow school bus"), boxes+scores+masks; ~850 M params, gated weights, custom license → best as RemoteVision.

### Comparison

**Grounding DINO / MM-Grounding-DINO-T — #1**
- Weights ungated, checked today: `IDEA-Research/grounding-dino-tiny` 689 MB fp32 (≈172 M params), `-base` 933 MB (≈233 M), `openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det` (arch `MMGroundingDinoForObjectDetection`, 2025-07-23). All Apache-2.0; mmdetection checkpoints also public.
- Quality: GDINO-T 48.4 COCO zero-shot / 28.8 LVIS-minival AP; **MM-GDINO-T 50.4 vs 48.4** (O365+GoldG, same backbone). GDINO paper evaluates referring expressions "specified with attributes".
- Latency (A100, 800×1333): 9.4 FPS PyTorch / 42.6 FPS TensorRT (GD1.5 Table 5). [derived] ≈3 FPS PyTorch on a 6–8 GB laptop GPU ⇒ 0.3–0.7 s/pass; ~2–3× at 1280 px — fine for "seconds per pass".
- VRAM [derived]: ≤1 GB fp32 weights; 800–1333 px Swin-T activations fit 6–8 GB; 640–800 px at 4 GB.
- Integration: `AutoModelForZeroShotObjectDetection`; prompts lowercase, `" . "`-separated, box/text thresholds; output = phrase+box+score → maps 1:1 to `infer(frame, labels)`.
- Issues: misses small/thin objects at 800 px (tile/upscale); colour words silently ignored (attribute marginalization, DSAA).

**YOLOE / YOLOE-26 — #2 (fast path)**
- YOLOE-26n/s/m/l: 3.9/10.7/21.3/25.5 M params, 6.1/21.9/70.6/89.0 GFLOPs, text-prompt mAP 24.7/30.8/35.4/37.8; YOLOE-v8-S 305.8 FPS T4 TensorRT.
- License: AGPL-3.0 (repo + weights). Internal use fine; shipping needs Ultralytics Enterprise license.
- Integration: `model.set_classes([...])`; first call downloads CLIP text encoder (`mobileclip2_b.ts`, ≈254 MB) — pre-cache for the rover. Prompt-free `*-seg-pf.pt` rejects `set_classes()`.
- Quality: MobileCLIP text encoder → weakest attribute binding; strong on small objects, runs on CPU.

**SAM 3 / SAM 3.1 — #3 (accuracy / remote)**
- ~850 M params (450 M vision + 300 M text + 100 M detector); 30 ms/image H200 for 100+ objects [derived: ~1–3 s/pass on laptop GPU]; fp32 ckpt 3.44 GB.
- Concept prompts are colour-bearing noun phrases by design → best attribute behaviour; returns boxes + scores + masks (transformers `Sam3Model`/`Sam3Processor`).
- Blockers: HF checkpoints **gated=manual**, custom SAM License (non-transferable; ITAR/military restrictions; publication acknowledgement), py3.12 + CUDA 12.6+, flash-attn-3. SAM 3.1 released 2026-03-27.

**Runners-up**
- YOLO-Worldv2 (AGPL/GPL): 47.4/42.7/37.4 FPS PyTorch A100 @640 (S/M/L); 35.4 AP @52 FPS V100 — superseded by YOLOE, same `set_classes()` API.
- OWLv2 base-ensemble (Apache-2.0, 620 MB): trivial path but 2023-era — OWL-ViT-L 42.2 COCO vs GDINO-T 48.4 in the same table.
- OmDet-Turbo-Tiny (Apache-2.0, in transformers since 4.45): 42.5 COCO zero-shot, 21.5 FPS PyTorch / 140 FPS TRT A100 @640.
- YOLO-UniOW S/M/L (GPL-3.0, 291–383 MB): 26.2/31.8/34.6 LVIS-minival AP, 98.3/86.2/64.8 FPS V100; heavier mmdetection path.
- Florence-2 base/large-ft (MIT; 0.23/0.77 B): card documents OD/phrase-grounding/OCR but **no** OVD prompt → crop-level verifier, not detector.
- **DINO-X and Grounding DINO 1.5/1.6 Pro/Edge: weights NOT downloadable** — no HF repos exist (searches empty today); official repos are API SDKs against DeepDataSpace with paid tokens.

### Colour-attribute mitigations (ranked)
1. **Detect class → verify colour from crop**: query "mat .", score crops with a small CLIP classifier (Detic pattern, Apache-2.0) or Florence-2 region-to-category; final = det_score × P(colour). Detector-agnostic.
2. **Fix the CLIP matching head, not localization**: a linear projection on frozen features is enough for fine-grained matching; localization contributes marginally (2404.03539).
3. **Attribute activation, training-free**: HA-FGOVD (LLM-highlighted attribute tokens + token-mask composition, frozen models, FG-OVD SOTA); DSAA (attribute prefix adapter, non-invasive).
4. **Measure on FG-OVD** before/after; 2025 fine-grained-prompt task/dataset adds stronger prompts.
5. **Prompt hygiene**: keep attribute and class tokens separate/repeated ("blue mat . blue . mat ."), lowercase, " . " separators, add colour hard negatives ("grey mat .").
6. **Deterministic colour naming** (HSV/Lab nearest colour) as auditable tie-breaker.
7. **Fine-tune last**: YOLOE-26 has linear-probe/full-tuning recipes; keep colour as a separate head.

### Verification notes
- This box has **no GPU** (4 cores, 5 GB RAM) → no on-hardware latency measurement; laptop figures are labelled extrapolations from A100/T4/V100.
- `web_search`/`web_extract` 403'd; everything fetched with `curl` (HF API, GitHub raw, arXiv API/HTML; DDG HTML for discovery).

### Citations
- https://huggingface.co/IDEA-Research/grounding-dino-tiny · https://huggingface.co/IDEA-Research/grounding-dino-base · https://huggingface.co/openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det · https://raw.githubusercontent.com/open-mmlab/mmdetection/main/configs/mm_grounding_dino/README.md · https://raw.githubusercontent.com/IDEA-Research/GroundingDINO/main/README.md · https://arxiv.org/abs/2303.05499 · https://arxiv.org/abs/2405.10300 · https://github.com/IDEA-Research/Grounding-DINO-1.5-API · https://www.deepdataspace.com/blog/Grounding-DINO-1.6-Pro
- https://github.com/IDEA-Research/DINO-X-API · https://arxiv.org/abs/2411.14347
- https://docs.ultralytics.com/models/yoloe/ · https://arxiv.org/abs/2503.07465 · https://raw.githubusercontent.com/THU-MIG/yoloe/main/README.md · https://huggingface.co/jameslahm/yoloe · https://raw.githubusercontent.com/ultralytics/ultralytics/main/LICENSE · https://docs.ultralytics.com/models/yolo-world/ · https://arxiv.org/abs/2401.17270 · https://raw.githubusercontent.com/AILab-CVC/YOLO-World/master/LICENSE
- https://arxiv.org/abs/2511.16719 · https://huggingface.co/facebook/sam3 · https://huggingface.co/facebook/sam3.1 · https://raw.githubusercontent.com/facebookresearch/sam3/main/README.md · https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE · https://huggingface.co/docs/transformers/main/en/model_doc/sam3
- https://huggingface.co/google/owlv2-base-patch16-ensemble · https://arxiv.org/abs/2306.09683 · https://huggingface.co/omlab/omdet-turbo-swin-tiny-hf · https://raw.githubusercontent.com/om-ai-lab/OmDet/main/README.md · https://arxiv.org/abs/2403.06892 · https://raw.githubusercontent.com/THU-MIG/YOLO-UniOW/main/README.md · https://huggingface.co/leonnil/yolo-uniow · https://arxiv.org/abs/2412.20645 · https://huggingface.co/microsoft/Florence-2-base-ft · https://arxiv.org/abs/2311.06242
- Mitigations/newer: https://arxiv.org/abs/2201.02605 (Detic) · https://github.com/facebookresearch/Detic · https://arxiv.org/abs/2404.03539 · https://arxiv.org/abs/2409.16136 (HA-FGOVD) · https://arxiv.org/abs/2605.18023 (DSAA) · https://arxiv.org/abs/2311.17518 (FG-OVD) · https://arxiv.org/abs/2503.14862 · https://arxiv.org/abs/2507.17436 (Dynamic-DINO) · https://arxiv.org/abs/2606.11546 · https://arxiv.org/abs/2607.01759 · https://arxiv.org/abs/2605.10190

**Artifacts:** `/home/freakymustard/.hermes/cache/scratch/taskA5/final_report.md` (990 words incl. citations), plus raw evidence in `landscape.md`, fetched READMEs/HTML (yoloe/uniow/gdino/sam3/gd15 HTML, mmdetection configs, arXiv XML). **Issue:** no local GPU, so latency figures are primary-source A100/T4/V100 numbers plus clearly-labelled extrapolation; all "downloadable today" claims were checked against the live HF API on 2026-09-29.