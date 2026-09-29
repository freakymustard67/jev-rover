# Open-vocabulary detection landscape for jev-rover M2 — verified 2026-09-29

All numbers below were pulled from primary sources with curl on 2026-09-29 (HF model API, repo raw READMEs, arXiv HTML/API). Items marked [derived] are my extrapolations, not measured.

## Ranked shortlist
1. **Grounding DINO family, MM-GDINO-T weights** — default LocalVision detector.
2. **YOLOE / YOLOE-26 (Ultralytics)** — fast path + CPU / 4 GB fallback.
3. **SAM 3 / SAM 3.1 (Meta)** — accuracy+attribute path, best as RemoteVision.

## 1. Grounding DINO / MM-Grounding-DINO-T
- Weights (ungated, checked): https://huggingface.co/api/models/IDEA-Research/grounding-dino-tiny (model.safetensors 689.4 MB ≈172 M params, apache-2.0, lastModified 2024-05-12); -base 933.4 MB ≈233 M; MM-GDINO-T https://huggingface.co/api/models/openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det (apache-2.0, arch MMGroundingDinoForObjectDetection, lastModified 2025-07-23).
- Quality: GDINO-T 48.4 COCO zero-shot, 28.8 LVIS-minival (Grounding DINO 1.5 paper Table 5, https://arxiv.org/html/2405.10300v2); MM-GDINO-T beats GDINO-T with identical backbones: 50.4 vs 48.4 (O365+GoldG), 50.6 vs 48.4 with V3Det (https://raw.githubusercontent.com/open-mmlab/mmdetection/main/configs/mm_grounding_dino/README.md). GDINO paper evaluates "referring expression comprehension for objects specified with attributes" (https://arxiv.org/abs/2303.05499).
- Latency (A100): 9.4 FPS PyTorch / 42.6 FPS TensorRT at 800×1333 (GD1.5 paper Table 5). [derived] ≈3 FPS PyTorch on a 6–8 GB laptop part ⇒ ~0.3–0.7 s/pass; 1280 px ~2–3× slower.
- VRAM [derived]: ≤1 GB fp32 weights; 800–1333 px Swin-T activations dominate → OK on 6–8 GB, 640–800 px at 4 GB.
- Integration: transformers `AutoModelForZeroShotObjectDetection`; official prompt format `"chair . person . dog ."` with box/text thresholds (https://raw.githubusercontent.com/IDEA-Research/GroundingDINO/main/README.md).
- Issues: small/thin objects missed at 800 px; colour words can be ignored (attribute marginalization, https://arxiv.org/abs/2605.18023).

## 2. YOLOE / YOLOE-26 (Ultralytics)
- Docs: https://docs.ultralytics.com/models/yoloe/ — YOLOE-26n/s/m/l params 3.9/10.7/21.3/25.5 M, 6.1/21.9/70.6/89.0 GFLOPs, zero-shot text-prompt mAP 24.7/30.8/35.4/37.8.
- YOLOE paper (v8-S 305.8 FPS T4 TensorRT; 12 M params): https://arxiv.org/abs/2503.07465, README table https://raw.githubusercontent.com/THU-MIG/yoloe/main/README.md
- Weights: https://huggingface.co/jameslahm/yoloe (AGPL-3.0); license AGPL-3.0 (https://raw.githubusercontent.com/ultralytics/ultralytics/main/LICENSE).
- Integration: `model.set_classes([...])`; first call downloads CLIP text encoder (mobileclip2_b.ts ≈254 MB for YOLOE-26) — cache it for offline rover runs. Prompt-free `*-seg-pf.pt` rejects set_classes.
- Issues: MobileCLIP text encoder → weakest attribute binding of the three; strong on small objects/speed; CPU-capable.

## 3. SAM 3 / SAM 3.1
- Paper: https://arxiv.org/abs/2511.16719 — ~850 M params (450 M vision + 300 M text + 100 M detector/tracker); 30 ms/image on H200 (100+ objects) [derived: expect ~1–3 s on a 6–8 GB laptop GPU].
- Weights: https://huggingface.co/api/models/facebook/sam3 (3.44 GB fp32 ckpt, **gated=manual**), SAM 3.1 https://huggingface.co/api/models/facebook/sam3.1 (released 2026-03-27, https://raw.githubusercontent.com/facebookresearch/sam3/main/README.md).
- License: custom SAM License (royalty-free, non-exclusive, non-transferable; ITAR/military end-use restrictions; publication acknowledgement) https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE
- Prompt style includes colour phrases ("yellow school bus", "large orange and white boat") → best attribute behaviour. Requires py3.12 + CUDA 12.6+, flash-attn-3, authenticated download.
- transformers support: Sam3Model/Sam3Processor (https://huggingface.co/docs/transformers/main/en/model_doc/sam3).

## Runners-up
- YOLO-Worldv2 (AGPL-3.0/GPL-3.0; 47.4/42.7/37.4 FPS PyTorch A100 @640 S/M/L, GD1.5 Table 5; paper 35.4 AP @52 FPS V100 https://arxiv.org/abs/2401.17270) — superseded by YOLOE; still the fastest AP-per-FLOP text-prompt option in ultralytics.
- OWLv2 base-patch16-ensemble: apache-2.0, 620 MB, HF ungated; 2023-era: OWL-ViT-L 42.2 COCO vs GDINO-T 48.4 in GD1.5 Table 5. https://arxiv.org/abs/2306.09683
- OmDet-Turbo-Tiny: apache-2.0, transformers 4.45+, 42.5 COCO zero-shot, 21.5 FPS PyTorch / 140 FPS TRT A100 @640. https://huggingface.co/omlab/omdet-turbo-swin-tiny-hf , https://raw.githubusercontent.com/om-ai-lab/OmDet/main/README.md
- YOLO-UniOW S/M/L: GPL-3.0, weights 291–383 MB, 26.2/31.8/34.6 LVIS-minival AP, 98.3/86.2/64.8 FPS V100; mmdetection install path. https://raw.githubusercontent.com/THU-MIG/YOLO-UniOW/main/README.md
- Florence-2 base-ft/large-ft: MIT, 0.23 B/0.77 B, ungated; model card documents OD/dense-region-caption/phrase-grounding/OCR but **no** open-vocabulary detection prompt → use as crop-level colour/detail verifier. https://huggingface.co/microsoft/Florence-2-base-ft
- **DINO-X and Grounding DINO 1.5/1.6 Pro/Edge: no downloadable weights.** No HF repos exist (HF search "grounding-dino-1.5"/"dinox" = empty, 2026-09-29); official repos are API SDKs against DeepDataSpace with token purchase: https://github.com/IDEA-Research/DINO-X-API , https://github.com/IDEA-Research/Grounding-DINO-1.5-API (1.6 Pro blog: 55.4 AP COCO / 57.7 AP LVIS-minival). GD 1.5 Edge: 75.2 FPS TensorRT / 36.2 AP LVIS-minival per https://arxiv.org/abs/2405.10300 — not usable locally.

## Colour/attribute mitigations (ranked by ROI)
1. **Detect class, verify colour from crop** — query "mat ." then score each crop with a CLIP classifier (pattern from Detic, apache-2.0: https://github.com/facebookresearch/Detic , https://arxiv.org/abs/2201.02605) or Florence-2 region-to-category; final score = det × P(colour).
2. **Fix the CLIP head, not localization** — the fine-grained bottleneck is the CLIP matching head; one linear projection on frozen features fixes much of it ("a linear projection is enough for fine-grained matching"): https://arxiv.org/abs/2404.03539
3. **Attribute activation, training-free** — HA-FGOVD: LLM highlights attribute tokens + token-mask composition on frozen OVD models, SOTA on FG-OVD: https://arxiv.org/abs/2409.16136 ; DSAA: attribute prefix adapter + attribute-aware encoding, non-invasive into BERT-based OVD models: https://arxiv.org/abs/2605.18023
4. **Evaluate/tune prompts on FG-OVD** — https://arxiv.org/abs/2311.17518 (fine-grained OVD benchmark) and 2025 fine-grained-prompt task/dataset: https://arxiv.org/abs/2503.14862
5. **Prompt hygiene** — separate colour and object tokens (`blue mat . blue . mat .`), lowercase, " . " separators (GDINO README); add hard negatives ("grey mat .") — attribute words get marginalized when the category dominates (DSAA).
6. **Deterministic colour naming** from the crop in HSV/Lab (nearest colour name) as an auditable tie-breaker; no training, no per-site drift.
7. **Fine-tune** last resort: linear-probe or freeze-backbone fine-tune on annotated rover frames; YOLOE-26 linear probing/full-tuning recipes exist in the docs; keep colour as a separate head to avoid category domination.

## Notable 2025–2026 papers (not deployable today)
DSAA (2605.18023), HA-FGOVD (2409.16136), Dynamic-DINO (2507.17436, ICCV'25, code https://github.com/wengminghe/Dynamic-DINO), VL-DINO (2606.11546), ProCal (2607.01759), DetRefiner (2605.10190), SAM 3/3.1 (2511.16719).
