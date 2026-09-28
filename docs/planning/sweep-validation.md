# Sweep validation — marker-less localization with a 180° ToF sweep

**Question:** before buying hardware — can a servo-swept ToF sensor localize the
rover well enough to *replace the AprilTag on the rover*, at least as a
position/heading anchor?

**Answer: yes**, including while driving and turning, provided the matcher
implements **desmear** (models the commanded motion during the sweep).

Simulator: [`../tools/sim/tof_sim.py`](../tools/sim/tof_sim.py) — numpy-only,
no repo imports. Reproduce with
`.venv/bin/python tools/sim/tof_sim.py`.

---

## Model

- **Sensor**: VL53L1X-class ToF — 4 m range, noise ≈ 1.5 cm + 1% of range,
  5% dropouts, 2% outliers, 25° beam (approximated by central ray + noise).
- **Environment**: this project's synthetic room — 6.4 × 3.6 m boundary
  (0.15–6.25 × 0.15–3.45 m roam area) with the four furniture blocks from
  `synthetic.py`. Rays hit whichever of walls/furniture is nearest.
- **Sweep**: **front-facing 180°** (−90°…+90° relative to heading) — the range a
  standard hobby servo can actually do. 91 beams at 2°, or 31/46 beams at
  6°/4° for faster sweeps.
- **Matcher**: grid search over poses (coarse global for bootstrap, local
  ±0.3 m / ±20° around the prior for tracking), robust loss (residuals capped
  at 0.5 m; miss/hit mismatches penalized). With `desmear`, each beam is
  predicted from the pose advanced along the candidate's arc by the assumed
  commanded (v, w) for that beam's sampling time.
- **Prior for tracking**: truth + noise ≈ 5 cm / 3° (stands in for
  camera-blob position + dead-reckoned heading).

## Results (30 random poses each)

### Bootstrap and stationary

| Scenario | pos p50/p90 | yaw p50/p90 | success |
|---|---|---|---|
| global search, no prior, 4° beams | 8.2 / 116.4 cm | 3.1 / 15.0° | 77% |
| stationary, 91 beams | 2.4 / 4.0 cm | 1.2 / 2.3° | 100% |
| stationary, 46 beams | 3.3 / 6.4 cm | 1.5 / 2.2° | 100% |
| stationary, 31 beams | 2.5 / 5.5 cm | 1.1 / 2.3° | 100% |

### Moving, without desmear (naive matcher)

| Scenario | pos p50/p90 | yaw p50/p90 | success |
|---|---|---|---|
| drive 0.45 m/s, 1.0 s sweep | 24.0 / 28.9 cm | 4.7 / 10.7° | 7% |
| drive 0.45 m/s, 0.5 s sweep | 12.9 / 16.4 cm | 3.2 / 7.4° | 70% |
| turn 30°/s, 1.0 s sweep | 9.1 / 13.2 cm | 15.5 / 18.9° | 37% |

### With desmear (commanded (v, w) modeled per beam)

| Scenario (1 s sweep) | pos p50/p90 | yaw p50/p90 | success |
|---|---|---|---|
| drive 0.45 m/s | 2.7 / 5.6 cm | 1.2 / 2.2° | 100% |
| drive, +25% speed error | 6.4 / 10.1 cm | 1.5 / 3.1° | 100% |
| pivot in place 30°/s | 2.2 / 4.6 cm | 1.3 / 2.2° | 100% |
| drive 0.3 + turn 20°/s | 3.8 / 7.3 cm | 1.4 / 2.3° | 100% |
| drive + turn, +10% v / +15% w error | 3.2 / 5.5 cm | 1.4 / 3.1° | 100% |
| hard pivot 60°/s | 3.2 / 5.7 cm | 1.2 / 2.0° | 100% |
| pivot 30°/s, 4° beams | 2.7 / 6.1 cm | 1.4 / 2.0° | 100% |

## Conclusions → requirements for M4

1. **Desmear is mandatory** for sweeping while driving/turning. Without it,
   turning alone corrupts heading (15°+). With it, results are near
   stationary-grade and robust to ±25% motion-estimate error.
2. **A paused/stationary sweep is tag-grade** (2–5 cm, ~1–2°) — the premium
   confirmation source and the reason marker-less operation is credible.
3. **Front 180° is sufficient** — no 360° hardware. Relocalization = "scan
   turn": spin in place while sweeping (validated at 60°/s).
4. **Bootstrap needs help** (77% global success with a half-view): use a
   two-pose init, a scan turn, or a coarse camera/semantics fix first.
5. **No mandatory pauses after startup**: sweeps run in the background during
   driving; pauses only for bootstrap and for object-confirmation scans,
   which fit naturally into waypoint stops.

See `semantics-layer-proposal.md` §10 for how this plugs into the semantic
layer (`sweep.confirmations`, `confirm_objects`, contradiction policy).
