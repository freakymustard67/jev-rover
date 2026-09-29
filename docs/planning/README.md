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
| **M4** | Sweep hardware: servo + ToF, `sweep.py` matcher (desmear required), object confirmation | planned — sim-validated |

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
