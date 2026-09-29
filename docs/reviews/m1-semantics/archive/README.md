# M1 semantics — complete research archive

The full raw record of the read-only research campaign behind the M1 “semantics layer” review (`../REVIEW.md`, PR #1). Two main waves plus six hourly cron passes, run 2026-09-28 → 2026-09-29. **No code was modified by any of it** — everything ran in scratch clones and the repo was verified clean throughout.

## How to read this

- Start with `../REVIEW.md` (distilled verdict, defects, decisions) and the fix package in PR #1.
- This archive is the evidence behind it: per-wave and per-pass reports, scripts, raw outputs, and prototype patches.
- Reports are **self-reports** by the research agents; key claims were spot-checked or re-derived by the supervising session, and wave 2's meta-review corrected earlier overstatements (see `wave2-verification/w2d/meta-review.md`).

## Contents

| Dir | What |
|---|---|
| `wave1-deep-dive/` | 8 parallel audit reports: plan↔code claims, threading/snapshot seam, store heuristics, destination/Jev, open-vocab vision landscape, ToF sweep + height math, tests/acceptance, repo/CI coherence (+ evidence). |
| `wave2-verification/` | 7 executable claim demos against the real code, the 12-fix package + transcripts + per-fix evidence, the M2 design note, the adversarial meta-review, and the reviewer `--trace` fix addendum. |
| `cron1-fixpack-independent-reverify/` | Independent re-verification of the fix pack (fresh clones; suite 80/80 at the PR tip, nothing unverified on the branch) + V2: the 6 KB state bound is exceeded on the realistic path (6125 B @ 10 objects). |
| `cron2-churn-approach-remotevision/` | V3 churn / phantom-pinning confirmed; V4 approach×planner stall confirmed (658-trip latch-fail); V5 RemoteVision threat model + loopback suite 73/73. |
| `cron3-sweep-persistence-viz/` | V6 sweep payload spec v1 + watchdog ground truth + `link.py` binary-chunk crash; V7 persistence gaps + gated-autoload design; V8 viz overlay prototype (+ canvas PNG evidence). |
| `cron4-latency-ranking-probe/` | V9 latency budget (stale-drop coupling confirmed) + loop-rate invariant tests; I1 ranking prototype (34/51 → 51/51) + patch; I2 probe hardening analysis + staged recommendation. |
| `cron5-digest-churnfix-approachfix/` | I5 state digest prototype (−38%: 5236 B @ 20 objects); I6 churn/pinning fix; I7 approach-standoff fix (projection + standoff rule). |
| `cron6-m15-stack-scanlink-remoteacceptance/` | I14 the M1.5 stacked series (applies cleanly after PR #1; 93 passed); I9 scan-link crash fix + `scan_payload.py` codec; I8 RemoteVision acceptance pack (43 tests, suite 123). |
| `research-state/` | `LEDGER.md` + `BACKLOG.md` + the consolidated review, snapshotted when the campaign was paused (2026-09-29 09:33 IST). |

## Prototype patches

Adoption-pending prototypes ship as `*.patch` / `*.diff` files inside the pass directories (i1 ranking, i5 digest, i6 churn, i7 approach, i9 scan link, i8 remote acceptance, i14 combined). They are proposals with verification evidence — **not applied** on this branch. Adoption decisions and ordering live in `research-state/BACKLOG.md` (items I10–I17; decisions D4/D5).

## Omissions

Datasheet PDFs, scratch clones/venvs, oversized raw JSON result dumps, and one duplicate archive copy are omitted; the reports cite what they used.
