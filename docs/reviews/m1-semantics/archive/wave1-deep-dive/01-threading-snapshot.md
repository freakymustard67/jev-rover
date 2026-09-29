All evidence gathered. Final review summary follows.

---

## Verdict

The committed M1 implementation (`f352f61`) faithfully realizes plan C4 + §4, and the concurrency model is **sound for the single-writer/multi-reader pattern it claims**: no data races, no double-submission, no lost results. Two real gaps: a **frame-space ambiguity** the seam inherits (raw vs undistorted) that will bite M2, and **main-thread disk I/O on merge**. Everything else is measured-negligible.

## Measured copy costs (real code, real configs, this host)

Both rooms are 6.4×3.6 m, cell 0.05 m → grid 128×72 = 9,216 cells; dtypes verified in `OccupancyGrid.__init__`: `float32` both arrays.

| item | room.example | room.synthetic |
|---|---|---|
| `grid_log_odds` copy | 36,864 B (1.9 µs) | same |
| `grid_last_seen` copy | 36,864 B (1.8 µs) | same |
| `polygon_px` copy (float32, 4 pts) | 32 B | 32 B |
| `floor_lab` copy (float64×3) | 24 B | 24 B |
| **`semantic_context()` total** | **73,784 B, 10.8 µs** | 73,784 B, 12.4 µs |
| **`frame.copy()` per pass** | **6,220,800 B (5.93 MiB), ~0.55 ms** | 2,764,800 B (2.64 MiB), ~0.42 ms |

Homography is shared by reference (144 B, not copied — mutation-free by convention). Per pass the main thread pays ~6.3 MB / ~0.6 ms once; at the configured 4 passes/min that's ≤25 MB/min and 0.6 ms in one 66 ms frame (≈1%), amortized ~0.04%. **Acceptable; the copy is not a problem.** `np.copyto` into a preallocated buffer measured no win (10.1 vs 10.3 GB/s), so that "optimization" is pointless. `semantic_context` is built only at offer time (guarded), not per frame; per-frame cost is `runner.snapshot(t)` — cheap dataclass copies.

## Correctness vs plan C4/§4

**Verified true:**
- Processing frame is 0.5-scaled (`proc_scale=0.5`, `homography_small = for_scale`) while tag pose and all semantics use full-res `self.homography`; §4's "FULL-RES bbox" convention is therefore correct and the 6 MB copy is required by that convention.
- Snapshot is complete, array copies are disjoint from live state (`color_lab` — the only field mutated live by `adapt` — is copied); `frame.copy()` is load-bearing (frame continues to be drawn on after offer).
- Tests confirm the discipline: worker/projection/integration 19/19 pass; repo tree stayed clean.

**Gaps found (ranked):**
1. **Frame-space ambiguity (real, M2-blocking).** `run.py:294` offers the **raw pre-undistort** frame; `Perception.process` rebinds locally after `cv2.undistort`, and `calibrate.py cmd_floor` clicks on raw frames despite its docstring saying "re-run floor on undistorted frames". So with `camera.intrinsics` set, perception's pose/floor work in undistorted space while the H clicked in raw space; the worker gets raw space. M1 is self-consistent only because `FakeVision.from_world` generates bboxes through the same H. `process()` doesn't expose the frame it used, so run.py *can't* offer the right one today. Pin this in M2.
2. **Main-thread disk I/O.** `store.merge` (called inside `runner.poll`, on the loop) does `latest.json` serialize + `os.replace` + `events.jsonl` append. Small (<6 KB) but it's the one blocking op in the perception block; move into the worker thread if store grows.
3. `close()` joins with `timeout=1.0` while M2 passes take seconds — returns with the thread still finishing (daemon, benign; parametrize).
4. **Plan C4's "FakeVision only reads the frame shape, so tests need no real pixels" is inaccurate.** `FakeVision.infer` checks only `ndim`, but `project_detections → height_suspect` reads `frame[y,x]` pixels whenever `floor_lab` is set (and `maybe_pass` requires it), so any test asserting `height_suspect=False` needs floor-coloured pixels. Worker tests dodge this (zeros frame, no such assertion); integration tests correctly use rendered frames. Doc fix.
5. Minor: cooldown refusals aren't counted in `skipped`; a pass_id is consumed even if `offer` fails (harmless gap); `_out`'s eviction path is unreachable under single-in-flight (belt-and-braces, fine).

## Race verdict (queue size 1 + pass_id)

- **No poll/offer race exists as built**: both are main-thread-only; `_inflight` is main-thread state; the worker only pushes to `_out`. Single in-flight is doubly enforced (`_inflight` guard + `_q` maxsize=1). Contract should assert "offer = main thread only" for future callers.
- **Result finishing between maybe_pass checks**: refused for one frame via `skipped["inflight"]` — correct, never double-submits; run.py polls before offering, so no delay in M1.
- **Staleness**: loop clock on both sides (`t` at poll vs `t_submit`) — consistent, immune to NTP steps; discards by age-at-poll (a better rule than the plan's literal `t_done − t_submit`); `t_done_wall` unused. Correct.
- **pass_id N−1-after-N discard is unreachable defense-in-depth** as built, not a live race path.
- numpy copies release the GIL; sides read disjoint memory → no locks needed. Correct.

## Ranked improvements

1. Pin the frame-space contract (make `process()` expose its used frame, or document raw-space + gate M2 on it) — correctness.
2. Move merge/persist off the control loop.
3. Parametrize `close()` join timeout.
4. For M2 CPU-bound detectors only: offer half-res frame + `for_scale` H (1.48 MiB copy, 4× less detector input).
5. Doc fixes: C4 pixels claim, "main thread offers only", cooldown counter.

**Artifacts:** `/home/freakymustard/.hermes/cache/scratch/taskA2/{measure.py,measure_frames.py}`. No repo/`/tmp/opencode` writes; repo tree clean; tests run read-only (scratch TMPDIR, no cache/bytecode).