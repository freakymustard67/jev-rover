# wave2 / w2c — final report

**Task:** write the M2 design note (frame-space contract fix, LocalVision/RemoteVision approach
grounded in the verified open-vocab research, corrected acceptance + test strategy). Design only —
no repo changes.

**Deliverable:** `wave2/w2c/m2-design.md` (643 lines, ~35.6 KB) — full design with code sketches,
config keys, exact edit points and rationale.

## What was done

* Read the wave-1 reports (01/02/03/04/06/00/07) and the repo files (`perception.py`, `run.py`,
  `calibrate.py`, `semantics.py`, `config.py`, `viz.py`, `tactics.py`, tests, configs, planning
  docs) at commit `9c33ec0`.
* Re-verified every fact the note leans on, read-only, in a scratch clone:
  * **Acceptance command defect reproduced** — `--semantics fake --semantics-once --find "blue mat"`
    exits with `--mission goto needs --waypoint NAME`; the corrected command
    (`--mission patrol --no-jev`, synthetic) was run and its exact output captured:
    `[find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92 object=obj_0001; approach (3.13,1.53) standoff=0.35 m`
    plus `semantics.passes=1, errors=0, median_ms=0.6`.
  * **Frame-space offset measured** — K=(800,640,360), dist=(−0.15, 0.05, 0.001, 0.001, 0):
    raw→undistorted corners move **46–88 px** (0 px at centre) on 1280×720; enough to invalidate
    bbox→floor projection.
  * **Model facts re-checked via HF API / transformers source** — openmmlab-community
    `mm_grounding_dino_tiny_o365v1_goldg_v3det`: ungated, Apache-2.0, `model.safetensors` =
    692,015,412 B (~689 MB); `mm-grounding-dino` maps to `GroundingDinoProcessor` in
    `processing_auto.py`; the processor itself lowercases and merges candidate labels as
    `". ".join(...) + "."`; image processor `size` default `{shortest_edge: 800, longest_edge: 1333}`;
    `outputs.input_ids` exists for post-processing; `transformers>=4.55` is the first release with
    MM-GDINO (model merged 2025-08-01, 4.55.0 on 2025-08-05).
  * **Host reality** — 4 cores, 5.7 GB RAM (3.1 GB free), no GPU, 17.4 GB disk, no torch in the
    venv ⇒ the local CPU profile (`image_shortest_edge=400/666`) is part of the design, with
    `remote` as the escape hatch.
  * **Suite baseline** — `pytest tests/` = 67 passed; 78 collected by default (11 prototype tests
    ride along from `docs/`).

## Key design decisions in the note

1. **Frame-space fix:** expose `Perception.frame_h` (the post-undistort frame `process()` used) and
   hand *that* to `maybe_pass` and `Renderer.draw`; `calibrate.py cmd_floor` gains a testable
   `_homography_space()` helper. Rejected hoisting undistort into `run.py` (silent-misuse
   precondition / double-undistort risk) and rejected returning the frame through `Scene`
   (serialised into the Jev state).
2. **Adapters:** new `vision.py` (lazy torch/transformers import), `build_vision` grows
   `local`/`remote`; LocalVision = MM-GDINO-T, lazy load + warm-up on the worker thread, cached
   load failure + existing cooldown, lowercase `" . "`-separated prompts via a `build_prompt`
   helper, class-only prompts by default with staged colour verification; RemoteVision = one JSON
   POST (base64 JPEG + labels + thresholds + timeout) → detections JSON, token from env only,
   injectable transport for hermetic tests. Seam-level half-res (`for_scale` H) specified but
   deferred with rationale; the processor `size` is the real CPU lever.
3. **Runner interplay:** `prewarm()` so the first real pass isn't dropped as stale, new
   `skipped["cooldown"]` counter, parametrised `close()` timeout, `--semantics-pass` manual
   trigger, stats additions.
4. **Acceptance/tests:** corrected command + verified output; `tools/smoke_semantics.py` sketch;
   default suite stays synthetic+FakeVision and hermetic; real-model tests opt-in via a
   `realvision` pytest marker; `pytest.ini` with `testpaths = tests`.

## Files

* created: `/home/freakymustard/.hermes/cache/scratch/wave2/w2c/m2-design.md`
* created: `/home/freakymustard/.hermes/cache/scratch/wave2/w2c/final_report.md` (this file)
* scratch only: `w2c/clone/` (scratch repo copy used for the acceptance run, incl.
  `clone/w2c_accept.log` and `clone/runs/summary_20260929-004511.json`), probe outputs,
  downloaded processor/config files
* **repo untouched** — no writes to `/home/freakymustard/jev-rover` or `/tmp/opencode`.

## Issues / caveats

* No GPU on this box, so local-model latency is unmeasured; the note requires the smoke tool to
  measure before any live acceptance (stated explicitly).
* The plan's M2 acceptance line says "routed to" while this commission scopes M2 to adapter +
  manual trigger; the note flags routing/driving as decision item 8.
* `--semantics-once` is inert in the current code; the note proposes either wiring it (M3) or
  removing it from docs, and drops it from the corrected command.
