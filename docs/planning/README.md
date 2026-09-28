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
| `../tools/sim/tof_sim.py` | The sweep simulator itself (numpy-only, no repo imports). Reproduce: `.venv/bin/python tools/sim/tof_sim.py`. |

## Status

| Milestone | Scope | State |
|---|---|---|
| **M1** | Offline semantic skeleton: schema, `semantics.py` (FakeVision, projection, merge/diff, destinations), worker with budgets, tests | ✅ shipped (`--semantics fake --find "<label>"`) |
| **M2** | Real vision adapter (open-vocabulary detector), live-camera testing, driving to a resolved destination | 🔜 next |
| **M3** | Trigger scheduler (mission start, Jev-uncertainty, audits) + Jev state compaction | planned |
| **M4** | Sweep hardware: servo + ToF, `sweep.py` matcher (desmear required), object confirmation | planned — sim-validated |

## Design invariants (do not break)

- ML never sits in the control loop; expensive passes are triggered and budgeted.
- Geometry is the coordinate system; semantics attach meaning to it; the sweep
  confirms physically. Disagreement is a first-class signal.
- Existing Jev questions, budgets, planner, reflex and watchdog semantics are
  untouched by the semantic layer.
- No secrets in the repo; tokens come from the environment only.

## How to verify the current state

```bash
.venv/bin/python -m pytest tests/ -q          # 67 tests, no network
.venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
    --semantics fake --semantics-once --find "blue mat" --seconds 5
```
