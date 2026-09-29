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
| **M2** | Real vision adapter + manual trigger, per `../reviews/m1-semantics/m2-design.md` | 🔜 next — adapter blocked on D1; routing/driving scope pending D5 |
| **M3** | Trigger scheduler (mission start, Jev-uncertainty, audits) + Jev state compaction | planned |
| **M4** | Sweep hardware: servo + ToF, `sweep.py` matcher (desmear required), object confirmation | planned — sim-validated |

**Open owner decisions (recorded here; not resolved by M1.5):** D1 — vision default
(MM-GDINO-T vs YOLOE vs remote placement); D3 — audit cadence (the M1.5
`--semantics-once` = mission-start-only interpretation is live, pending
confirmation); D4/D5 — probe staging and M2 scope (routing/driving in or after
M2). See `../reviews/m1-semantics/REVIEW.md` §6 and
`../reviews/m1-semantics/archive/research-state/BACKLOG.md` (I10–I17).

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
