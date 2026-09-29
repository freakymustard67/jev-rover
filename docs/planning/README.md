# Planning package

This folder holds the design and planning artifacts behind the project's current
state, so anyone (human or agent) can reconstruct **why** the code looks the way
it does — not just what it does.

## Documents

| File | What it is |
|---|---|
| `semantics-layer-proposal.md` | Full design: a *triggered semantic layer* (vision model on demand) + *physical sweep confirmation*, on top of the existing geometric perception. Includes scene-schema extensions, trigger catalogue, milestones M1–M4, and the simulation evidence for the sweep. |
| `m1-plan.md` | The implementation plan for M1 as executed, including the conflicts it found in the proposal (and their resolutions), the owner-approved decisions, and the measured findings folded in. |
| `sweep-validation.md` | Simulation evidence for marker-less localization: a 180° ToF sweep on a servo, scan matching, and desmear. Written to decide whether to build the hardware. |
| `prototype/` | Pre-implementation validation scripts (projection, merge/diff, destination ranking) and their tests. These measurements (e.g. anchor bias for flat objects, raw-displacement motion rule) are reflected in `semantics.py`. |
| `../reviews/m1-semantics/REVIEW.md` | Independent deep review of M1: adjudicated behavioral claims, confirmed defects, the 12-fix package, and owner decisions D1–D5. |
| `../reviews/m1-semantics/m2-design.md` | M2 design note: frame-space contract, LocalVision/RemoteVision design, corrected acceptance commands, test strategy. |
| `../reviews/m1-semantics/archive/` | Complete raw research archive (waves 1–2 + six cron passes; evidence, transcripts, unapplied prototype patches). |
| `../tools/sim/tof_sim.py` | The sweep simulator itself (numpy-only, no repo imports). Reproduce: `.venv/bin/python tools/sim/tof_sim.py`. |

## Status

| Milestone | Scope | State |
|---|---|---|
| **M1** | Offline semantic skeleton: schema, `semantics.py` (FakeVision, projection, merge/diff, destinations), worker with budgets, tests | ✅ shipped (`--semantics fake --find "<label>"`) |
| **M1.5** | Fix package from the independent M1 deep review (PR #1, merged 2026-09-29): distance-ordered merge assignment, frame-resolution guard, probe/dedupe/label hardening, tri-state `--semantics` + wired `--semantics-once`, `--trace` fix | ✅ shipped (80 tests) |
| **M2** | Phase 1: real vision adapters + find-side live testing (owner decision D5). `LocalVision` (MM-GDINO-T, D1) + `RemoteVision` + `tools/vision_server.py`; frame-space contract landed earlier | 🔄 adapters shipped; live find-side acceptance pending the camera stream |
| **M3** | Trigger scheduler (mission start, Jev-uncertainty, audits) + Jev state compaction | planned |
| **M4** | Sweep hardware: servo + ToF, `sweep.py` matcher (desmear required), object confirmation | 🔄 host side landed (`sweep.py` matcher + `confirm_objects`, spec-v1 host codec, bench + fake rover, hermetic tests); firmware scan mode written but **hardware-unverified**; servo/ToF bench this evening |

**Owner decisions (settled 2026-09-29):** **D1** — MM-GDINO-T (Apache-2.0,
`transformers>=4.55`) is the local vision default; switching local↔remote is a
config-only change (`semantics.model.kind` + `endpoint`). **D5** — M2 phase 1 is
adapters + find-side live testing only; routing/driving to a resolved
destination is phase 2, after find-side acceptance (dry-run/mock, no `--arm`).
**D3/D4** — the M1.5 behaviour stands: the `--semantics-once` audit-cadence
interpretation and the probe staging are unchanged. Historical detail:
`../reviews/m1-semantics/REVIEW.md` §6.

### M2 local-vision measurements (CPU, this host, 2026-09-29)

MM-GDINO-T, `transformers` 5.17, CPU-only torch 2.14, one pass per profile
after warm-up, same 640×480 photo fixture (cat/couch/remote control detected in
all runs):

| processor size (shortest/longest) | pass latency | peak RSS |
|---|---|---|
| 400 / 666 (CPU profile) | ~18.9 s | 1282 MB |
| 800 / 1333 (default) | ~35.6 s | 2149 MB |

Weight load ≈ 5.6–6.1 s once per process; warm-up adds one forward pass.
Zero-shot detection on the synthetic renderer's flat top-down rectangles is not
reliable at sane thresholds (0 detections at ≥0.30) — the opt-in `realvision`
test asserts pipeline liveness there, and ≥1 detection on a real photo via
`JEV_ROVER_REALVISION_IMAGE`. Reproduce with:

```bash
JEV_ROVER_REALVISION=1 .venv/bin/python -m pytest tests/test_vision_local_real.py -m realvision -q
.venv/bin/python tools/smoke_semantics.py --config config/room.json --source camera \
    --kind local --labels "mat,box" --shortest-edge 400 --longest-edge 666
```

### Live acceptance status (camera-only path, 2026-09-29)

`config/room.json` points at `/dev/video10`, uses the MM-GDINO-T CPU profile
(`max_age_s: 120`) and `rover.pose_source: "blob"`. Calibration is now
camera-only and non-interactive: `calibrate.py depth` writes the floor plane +
pixel→floor homography + floor polygon (with quality metrics and an overlay),
and `calibrate.py bg` captures the empty-room reference the blob pose subtracts.
**Blocked only on the tablet camera stream** (`/dev/video10` has no producer).
Once it is up:

```bash
.venv/bin/python calibrate.py bg    --config config/room.json   # rover absent from the frame
.venv/bin/python calibrate.py depth --config config/room.json   # ~30 s: 5 frames x ~6 s CPU
.venv/bin/python calibrate.py check --config config/room.json   # belief map must line up
.venv/bin/python run.py --config config/room.json --source camera \
    --mission patrol --seconds 60 --semantics local --find "<object>"
```

The run records `semantics.median_ms` and the Jev judgment stats in
`runs/summary_*.json`; one HUD frame is saved from the `--video` mp4. Depth-model
measurement on this host: load 1.7 s, **5.8 s per frame** (5-frame median ≈ 30 s
per calibration); MM-GDINO-T pass ~19 s at the 400/666 profile.

**Synthetic stand-in, recorded 2026-09-29** (the camera command fails fast and
cleanly while `/dev/video10` has no producer: `cannot open camera source`, with a
pointer to `--source synthetic`). Same stack, synthetic room, `--semantics local`
with the CPU profile: warm-up **25.4 s**, pass **median 15.9 s**, 20 Jev calls /
0 errors / median latency 0.33 s / 72k tokens, patrol **16 goals / 18.95 m /
7.8% stuck**; HUD frames extracted from `runs/acceptance_local.mp4`
(`/tmp/opencode/acceptance_{patrol,late}.png`).

**Named limits (test-grade, by design):** Depth-Anything-V2-Metric-Indoor-Small
has ~5–15% indoor scale error and frame-to-frame wobble; the focal comes from
`camera.hfov_deg`, not intrinsics; the room frame is camera-derived. The M4
sweep-ruler replaces the mapping without interface changes. Routing/driving to a
resolved destination stays phase 2 (D5).

## Design invariants (do not break)

- ML never sits in the control loop; expensive passes are triggered and budgeted.
- Geometry is the coordinate system; semantics attach meaning to it; the sweep
  confirms physically. Disagreement is a first-class signal.
- Existing Jev questions, budgets, planner, reflex and watchdog semantics are
  untouched by the semantic layer.
- No secrets in the repo; tokens come from the environment only.

## How to verify the current state

```bash
.venv/bin/python -m pytest tests/ -q          # 80 tests, no network
.venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
    --mission patrol --seconds 20 --no-jev --semantics fake --find "blue mat"
```
