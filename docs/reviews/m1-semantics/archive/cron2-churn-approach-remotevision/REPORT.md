# Cron run 20260928-2057 — jev-rover semantics verification (hourly job #2)

Window: 2026-09-28 20:57 → 21:35 UTC (2026-09-29 02:27 → 03:05 IST).
Repo seen: master `9c33ec0` (unchanged; worktree clean; origin HEAD identical).
PR #1 `review/m1-semantics-audit` (`bec1d91`): **still open** — only owner action is a title
rename (`renamed` event 2026-09-28 19:57:51Z, the cosmetic count fix); no merge yet → R2 still parked.

## Selected work
No master delta ⇒ took the top three open verification items: **V3** (multi-pass churn),
**V4** (approach_point × planner inflation), **V5** (RemoteVision threat model).
Three subagents ran in parallel; reports + scripts archived in this directory.

## Results (all three completed; disk-first reports written)
- **w4a — V3 churn** (`w4a-churn.md`, `w4a-churn-sim.py`, tables + results json): real
  `SemanticStore.merge` on the PR tip, 100 seeds × 10 passes @ 60 s. Phantom-alive pinning
  **CONFIRMED** (recurring conf-0.3 hit → 0 vanish events, object pinned alive to t=540).
  Mover churn boundary `d = α·R = 0.2 m/pass`; ≥0.8 m/pass → one new ID every pass, `moved`
  signal lost; R=0.3 unsafe under σ=0.10 noise. Recommended hysteresis patch (carry
  radius + 0.7 confidence decay) measured: d=0.5 → 0 vanish, d=0.8 IDs 12→5.9, phantom
  vanishes at pass 2. Also: S3 resurrection re-matches the same ID with **no `appeared`
  event** (event-log gap). `vanish_passes=2` confirmed best.
- **w4b — V4 approach×planner** (`w4b-approach-planner.md`, sim/pipeline/analysis scripts +
  results): stall **CONFIRMED** whenever the destination object is in the occupancy grid.
  Blocked radius = obj_r + ~0.235 m (inflation 0.22 → 4-cell dilation) > standoff 0.35 for
  any obj_r ≳ 0.11 m. `Planner.plan` substitutes a free cell via `free_near` but then splices
  the raw goal back (`control.py:128`); Jev `hold_course` parks at 0.318 m < 0.30 latch →
  permanent standstill (658 map_brake trips). Safe rule: `standoff ≥ obj_r + 0.35` →
  0.65–0.75 m for 0.6–0.8 m objects; alternative fix F1 (caller-side goal projection)
  verified clean (0.045 m, 0 trips). Latent defect today (`--find` resolves+prints only);
  bites at M2 routing. Patch 11 presence confirmed; suite 80/80 on tip.
- **w4c — V5 RemoteVision threat model** (`w4c-remotevision-threat.md`, prototype + mock +
  driver + transcript): loopback mock-server prototype through the real worker/runner
  **73/73 checks pass**. Timeout/500/malformed/schema/refused all isolated (clean error,
  worker alive, cooldown armed, control loop never blocked). Token hygiene verified
  (env-only, header-only, absent from all artifacts). Gaps: endpoint **is serialized** in
  config save; no `timeout_s` upper bound; `urllib` honors env proxies. 9 M2 acceptance
  additions (A1–A9) incl. sentinel-token test.

## Spot-checks run by the cron itself (before trusting the summaries)
- Independent probe (`scratch/w4-verify/probe_phantom.py`) against PR-tip merge: object
  pinned alive, `last_seen_s=540`, conf frozen 0.9, 0 vanish events — matches w4a.
- w4b sim re-run: reproduced blocked radii 0.535/0.637 m, approach blocked, reach=False at
  standoff 0.35, clean ≥0.6, Jev standstill 0.318 m / 658 trips — matches.
- w4c driver re-run (with env token): exit 0, **73/73** — matches.
- PR #1 API: `state=open`, `merged=false`, `mergeable_state=clean`.

## Edits made
- LEDGER.md: State block PR line updated; findings bullets added; cron pass #2 section appended.
- BACKLOG.md: V3/V4/V5 → [x]; added I6 (churn/hysteresis patch), I7 (approach-standoff fix),
  I8 (M2 remote acceptance pack). R2 unchanged (waits for owner/merge).

## Next run guidance
- Watch for owner action on PR #1 / master move → R2 becomes the priority (delta verification).
- Otherwise next open verification items: V6 (sweep payload spec), V7 (cross-session persistence),
  V9 (latency-budget invariant test), V8, V11 (errata application), I1/I2/I3/I4.