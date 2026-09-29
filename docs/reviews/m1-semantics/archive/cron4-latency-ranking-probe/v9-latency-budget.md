# V9 — latency-budget realism (jev-rover semantics layer)

**Verdict summary:** worker-thread insulation **CONFIRMED** (a 5 s `infer()` never stalls the 15 Hz tick loop; worst per-tick semantics cost ≈ 1.4 ms vs 66.7 ms period); per-offer main-thread cost **MEASURED** (`frame.copy()` 0.39 ms, full submit 0.49 ms, merge 0.74 ms); stale-drop coupling **CONFIRMED** (>30 s latency ⇒ every result dropped, `store.passes` stays 0, no map, no store files, **no warning anywhere** — only a count in `stats()` at process exit); budgets **CONFIRMED as documented** (one-in-flight, `min_interval_s` ≥ 2.0, 4/rolling-60 s, forced offers still budget-blocked); fail-cooldown path on master **CONFIRMED UNCOUNTED** (fixed only in unmerged PR #1 commit `ed9ddde`); loop-rate invariant pytest module **DELIVERED and GREEN** (6 tests, full clone suite 73 passed).

- Master tested: `9c33ec06afcc644c23deef76447b4a2370de31c1` ("docs: planning package …").
- Scratch clone: `/home/freakymustard/.hermes/cache/scratch/v9/clone` (writes confined there; `/home/freakymustard/jev-rover` untouched, read-only).
- Python: `/home/freakymustard/jev-rover/.venv/bin/python` = 3.11.16, pytest 9.1.1. Host: 4 cores, Debian kernel 6.12.107, host under variable background load (cron/agents) — every timing claim below is either paired/interleaved or repeated ≥3×; the noisy 1280x720 24 s battery is reported with its spread and not used for the headline claim.
- Raw logs + scripts: `runs/20260928-2342/v9-evidence/` (see index at the bottom).

---

## Method (common)

`v9_harness.py` (scratch clone) builds the shipped synthetic pipeline — `RoomConfig.load(config/room.synthetic.json)`, `SyntheticRoom` + `Perception` warmed with 6 frames (same pattern as `tests/test_semantics_integration.py:12-20`) — then runs a tick loop paced at 15 Hz (period 66.7 ms) shaped like `run.py`: render a frame per tick (the `--source synthetic` frame source), and at 15 Hz do `perception.process` + `runner.poll` + offer + `runner.snapshot` (run.py:286-297); pacing mirrors run.py:347-350. The stub detector `SleepVision(L)` sleeps L seconds in `infer()` then returns two fixtures ("blue mat", "red box") so passes actually merge. Resolutions: **1280x720** (shipped config) and **640x360** (half scale: `synthetic.px_per_m=100`, camera + homography + floor polygon scaled by 0.5). Offer patterns: `eager` (a `maybe_pass` attempt every tick — worst case) and `paced` (attempt at t≥1.0 then ≥ every `min_interval_s` — run.py/M3-scheduler-like).

---

## Claim 1 — worker-thread insulation: CONFIRMED

### 640x360 — 15 Hz is reachable there; detector latency makes **zero** difference

| L (s) | achieved Hz (all reps) | median Hz | max inter-tick lag (ms) | semantics-only block max (ms) |
|---|---|---|---|---|
| off (baseline) | 15.0, 15.0, 14.985, 14.964, 15.0, 14.992 | 14.996 | 0.11–24.83 | — |
| 0.05 | 15.0, 15.0, 14.998, 15.0, 15.0 | 15.000 | 3.58–6.76 | 1.0–8.5 |
| 0.5 | 15.0 | 15.000 | 0.08 | — |
| 2.0 | 14.998, 15.0, 15.0 | 15.000 | 0.06–4.34 | 1.0–1.5 |
| **5.0** | **14.874, 14.898, 15.0, 15.0, 15.0** | **15.000** | **3.73–23.06** | **1.30–1.47** |

With a 5 s detector in flight for the entire loop, cadence = 99.2–100.0 % of the semantics-off baseline (eager and paced runs both). Jitter stays in the tensor of the baseline itself (max lag 23 ms vs 24.8 ms baseline; period 66.7 ms).

### 1280x720 (shipped config) — cadence is host/render-bound, not semantics-bound

24 s eager runs: baseline 11.75 / 12.762 / 12.881 Hz; L=5.0: 13.476 / 13.037 / 11.837; L=2.0: 13.008 / 13.329 / 13.378; L=0.05: 10.477 / 11.941 / 10.413; L=0.5: 10.975 / 7.931 / 10.233. There is **no ordering by L** — the dips coincide with load windows (dt stdev up to 42 ms, block max up to 170 ms in those runs). Interleaved 8 s A/B (busy→idle→busy→idle, 8 reps each): baseline 13.631–14.374 Hz, L=5.0 13.399–14.993 Hz; **semantics-only block max 1.22–1.43 ms per tick** across all L=5 runs (p50 0.05 ms, p99 ≤0.65 ms) while the 5 s pass was in flight.

Why 15 Hz is not reached at 720p even with semantics off: micro-benchmarks (300 reps) show `SyntheticRoom.render()` mean **40.45 ms** (p50 40.69) and `perception.process` mean **38.96 ms** (p50 38.47) — the harness's synthetic frame generator alone (~40 ms) is a test artifact that the real camera path does not pay; `process` alone (~38 ms p50) still fits the 66.7 ms budget. Loop block p50 at 720p (perception + semantics per tick) was ~34–41 ms across all 8 s runs. At 640x360: render 11.24 ms, process 12.06 ms.

**Claim 1 verdict:** the worker thread + queue-size-1 design (`semantics.py:413-479`) insulates the loop exactly as intended: `maybe_pass` only enqueues (`semantics.py:430-436`, copy measured below), the detector's latency is invisible to the main thread, and the worst measured per-tick semantics cost (1.4 ms) is 2 % of the tick period.

---

## Claim 2 — main-thread per-offer / per-tick costs: MEASURED

1280x720, 300 reps unless noted (mean / p95 / max, ms; µs where noted):

| operation (where) | mean | p95 | max |
|---|---|---|---|
| `frame.copy()` — **semantics.py:523** | **0.388 ms** | 0.525 | 0.566 |
| `maybe_pass` full submit path (copy + `PassRequest` + `queue.put_nowait` + bookkeeping; 300/300 accepted) | **0.492 ms** | 0.563 | 0.807 |
| `maybe_pass` gated, pass in flight (semantics.py:503-505) | 11.2 µs | — | 74.9 µs |
| `poll()` empty / in-flight (semantics.py:532-536) | 1.77 µs | — | 10.2 µs |
| `snapshot()` no map (semantics.py:547-548) | 0.18 µs | — | 2.2 µs |
| `snapshot()` with map, 2 objects | 26.8 µs | 26.7 | — |
| `poll()` consuming a finished pass: `store.merge` + `latest.json` write (v9_merge_iso.py, 30 reps) | **0.744 ms** | 0.907 | 1.032 |
| `perception.semantic_context(t)` (perception.py:704-722) — caller-side, per offer | 10.8 µs | 11.1 | 27.4 µs |
| for reference, 640x360: copy 0.032 ms, submit 0.237 ms, merge 0.648 ms | | | |

Per successful offer the main thread pays ≈ **0.5 ms** (copy 0.39 + queue put/bookkeeping 0.10), i.e. 0.7 % of a 66.7 ms tick; once per completed pass it additionally pays ≈ 0.75 ms for the merge. All other per-tick costs are µs-scale.

---

## Claim 3 — STALE-DROP COUPLING: CONFIRMED (all four sub-claims)

Real-time run (`v9_harness.py stale --scale 0.5 --L 31 --seconds 34`), `max_age_s=30.0` (config.py:142), offer at t=1.031: worker completes the 31 s pass (`median_ms` 31000.6), the next poll at t≈32.1 hits `t - res.t_submit > max_age_s` (semantics.py:542):

- `store_passes = 0`, `worker_completed = 1`, `stale_dropped = 1`, `snapshot = None`, **store dir completely empty** (no `*_latest.json`, no `events.jsonl`), all `skipped` counters 0.
- 3 repeats of the same branch with caller-timestamp injection (real sleep 0.2 s, poll at t=32.0, age 31.0 s): all `merged=false, store_passes=0, stale_dropped=1, snapshot=false, store_files=[]`.
- The one-in-flight slot is released before the staleness check (semantics.py:537-538 precede 542-544), so a dropped pass does not wedge the runner (also asserted in the new test).

**No user-visible warning/log of the root cause:** `grep -n "print(\|logging\|warn" /home/freakymustard/jev-rover/semantics.py` → no output (the module has no `logging` import at all). `stale_dropped` exists only in `stats()` (semantics.py:566), which run.py emits **only at process exit** in `runs/summary_*.json` (run.py:377, 380-382). During the run nothing is printed, nothing is written to the event log (the store's writer is only reached via `merge`, semantics.py:359+). The stale run's stdout contained only harness prints.

Where a signal could go (concrete anchors):
1. `semantics.py:542-544` — record the age on the runner (e.g. `self.last_stale = {"t": t, "age_s": t - res.t_submit}`) next to the existing `stale_dropped += 1`; add a `last_stale_age_s` field in `stats()` (semantics.py:556-569) so the existing summary surface (run.py:377) carries the root cause, not just a count.
2. `run.py:290-297` — the perception-tick site that calls `runner.poll(t)`: compare `runner.stale_dropped` before/after and print/log a one-line `[semantics] pass dropped stale (age X s > max_age_s=30)` once per increment.
3. `semantics.py:359+` `_append_events` — the per-room `events.jsonl` already exists; a `kind="stale_drop"` line would make drops durable without new files (needs the age passed through the store call).
4. `run.py:324-326` `--log` JSONL — could add a `"semantics"` record per merge/drop so post-hoc analysis sees the event.

---

## Claim 4 — budget behavior: CONFIRMED as documented; docs/code deltas noted

Config (config.py:141-142,150-151): `max_passes_per_min=4`, `max_age_s=30.0`, `min_interval_s=2.0`, `failure_cooldown_s=10.0`. 24 s eager runs at 1280x720:

| L (s) | pass cadence (s) | passes/24 s | merge times (s) | interval | budget | inflight | no_context |
|---|---|---|---|---|---|---|---|
| 0.05 | 2.031–2.059 | 4 | 0.07/2.1/4.1/6.2 | 84–94 | 154–199 | 0 | 0 |
| 0.5 | 1.992–2.076 | 4 | 0.5/2.5/4.6/6.5 | 34–82 | 144–160 | 9–27 | 0 |
| 2.0 | 2.053–2.057 | 4 | 2.0/4.0/6.1/8.2 | 0–1 | 207–213 | 101–109 | 0 |
| 5.0 | 5.011–5.053 | 4 | 5.0/10.1/15.1/20.1 | 0 | 41–44 | 240–268 | 0 |

- Cadence = max(`min_interval_s`, latency) + ≤1 tick quantization (min_interval is a lower bound; observed 2.03–2.09 s when interval-bound, ~5.05 s when latency-bound).
- Exactly **4 passes complete**, then the rolling-60 s budget (semantics.py:516-520) refuses every further offer until the window slides; with a 5 s detector the counter mix shifts to `inflight` (one-in-flight gate, semantics.py:503-505) — matching the documented behavior.
- Paced 640x360 runs (attempt every 2 s, 12 s): L=0.05 → 4 passes then `budget=2` (attempts at t≈9,11 blocked); L=5 → `passes=1`, `inflight=4`, `budget=0` (2nd accepted attempt still in flight at run end). `no_context` = 0 in all loop runs; the counter is covered by `tests/test_semantics_worker.py:123-129`.
- Forced offers bypass `interval` but **not** the budget (semantics.py:513 vs 516-520) — verified by test (`maybe_pass(12.0)` and `maybe_pass(12.1, force=True)` both refused, `budget=2`).

Docs vs code: m1-plan.md:233 lists guards including `enabled`; `maybe_pass` has no `enabled` check — run.py gates construction instead (run.py:242-247). m1-plan.md:237 promises `stats()["skipped_budget"]`; the code exposes a nested `skipped` dict with 4 keys (semantics.py:496, 567). The budget semantics themselves match m1-plan.md:338-339.

**Fail-cooldown uncounted on master: CONFIRMED.** Harness run: `BoomVision` error → `poll(1.01)` returns None, `errors=1`, `fail_cooldown_until=6.01`; `maybe_pass(2.0, force=True)` → False **with the whole `skipped` dict unchanged** (before == after), `stale_dropped`/`store.passes` unchanged; accepted again after expiry. semantics.py:511-512 returns without a counter (and the cooldown gate sits **after** the inflight/no_context gates, before interval/budget — so a cooldown refusal is invisible in the counter set). The unmerged PR #1 branch (fetched read-only into the scratch clone: `git fetch origin review/m1-semantics-audit`) fixes exactly this in commit `ed9ddde` "semantics: count cooldown refusals in skipped stats" (branch `semantics.py:596-597` adds `skipped["cooldown"]`), plus commit `2fe280d`/others in the same series — not part of master.

---

## Claim 5 — the loop-rate invariant pytest module: DELIVERED, GREEN

`tests/test_semantics_loop_rate.py` in the scratch clone (also copied to evidence). Six tests, all passing:

1. `test_loop_cadence_insulated_from_busy_5s_detector` — shipped 1280x720 config: baseline vs busy-5 s loops (4 s each, paced offers); asserts the detector was in flight the whole time (`worker.started==1`, `completed==0`, `skipped.inflight≥1`), cadence ≥80 % of the semantics-off baseline (m1-plan's promised invariant), and p95 inter-tick gap ≤ baseline·1.2 + 10 ms.
2. `test_loop_cadence_absolute_target_with_busy_5s_detector` — 640x360: asserts achieved ≥ 80 % of the 15 Hz target **and** ≥80 % of baseline with the 5 s detector busy.
3. `test_budgets_one_inflight_interval_and_per_minute` — one-in-flight refusal counted; `min_interval_s` refusal counted; forced offer passes interval; 4 offers then `budget` refuses (forced and unforced).
4. `test_fail_cooldown_blocks_uncounted` — cooldown refusal moves no counter; accepted after expiry.
5. `test_pass_completes_and_merges` — pass merges (`passes==1`, object projected, `latest.json`/`events.jsonl` written).
6. `test_stale_result_beyond_max_age_is_dropped_without_a_map` — age 31 > `max_age_s` 30 → `poll` None, `stale_dropped==1`, `store.passes==0`, `snapshot` None, no store file, slot freed.

Validation (scratch clone): `pytest -v tests/test_semantics_loop_rate.py` → **6 passed in 21.28 s**; full suite `pytest -q tests/` → **73 passed in 34.29 s** (67 pre-existing + 6 new; no interference). Raw outputs in evidence.

Suggested integration note for the dispatcher: this file lands in `tests/` of the repo (PR #1 shape); it needs no network and no hardware.

---

## Caveats / honesty notes

- The 1280x720 24 s battery ran while the host had background load; the L=0.05/0.5 dips (7.9–11.9 Hz) are load artifacts (their runs also show 2–3× larger dt stdev), which is why the insulation claim rests on the 640x360 absolute numbers + the interleaved 720p A/B + the micro-benchmarks.
- The 15 Hz target is unreachable at 1280x720 with `--source synthetic` on this host **because `SyntheticRoom.render()` alone averages 40.5 ms** — a harness artifact; the real camera read was not measured here.
- The stale branch is exercised in two ways: one real-time 31 s-latency run (34 s wall) and three caller-timestamp injections; both exercise semantics.py:542-544 unchanged.
- `min_interval_s` observed as a lower bound with one tick of quantization; no violation observed.
- All measurements are single-host, 4-core; treat absolute ms as host-specific, ratios as portable.

---

## Evidence index (`runs/20260928-2342/v9-evidence/`)

- `v9_harness.py` — loop/costs/stale/stale-fast/cooldown harness (the exact code that produced every number)
- `v9_merge_iso.py` — single-merge isolation probe; `v9_summarize.py`, `v9_table.py` — log aggregation
- `test_semantics_loop_rate.py` — the delivered pytest module (copy of clone `tests/`)
- `logs/v9_loop_scale1.0.jsonl`, `logs/v9_loop_scale0.5.jsonl` — every loop run (raw)
- `logs/v9_costs_scale1.0.jsonl`, `logs/v9_costs_scale0.5.jsonl`, `logs/v9_merge_iso.jsonl`
- `logs/v9_stale.jsonl`, `logs/v9_stale_fast_scale0.5.jsonl`, `logs/v9_cooldown.jsonl`
- `logs/report_tables.md` — the tables above, generated from the raw logs
- `logs/battery_stdout.txt`, `logs/battery2_stdout.txt` — raw harness stdout of the big batteries
- `pytest_loop_rate_verbose.txt`, `pytest_full_suite.txt` — pytest runs (6 passed / 73 passed)
- `environment.txt` — HEAD, python, host kernel, date

Reproduction (all from the scratch clone; nothing writes to the real repo):

```
mkdir -p /home/freakymustard/.hermes/cache/scratch/v9 && git clone https://github.com/freakymustard67/jev-rover.git /home/freakymustard/.hermes/cache/scratch/v9/clone
cd /home/freakymustard/.hermes/cache/scratch/v9/clone
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py loop --scale 1.0 --seconds 24 --repeats 3 --L 0.05 0.5 2.0 5.0
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py loop --scale 0.5 --seconds 12 --repeats 3 --offer paced --L 0.05 2.0 5.0
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py costs --scale 1.0 --reps 300
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py costs --scale 0.5 --reps 300
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py stale --scale 0.5 --L 31 --seconds 34
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py stale-fast --scale 0.5 --repeats 3
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_harness.py cooldown
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python v9_merge_iso.py 1.0 30
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -v -p no:cacheprovider tests/test_semantics_loop_rate.py
```

Cross-check greps used in the report:
```
grep -n "print(\|logging\|warn" /home/freakymustard/jev-rover/semantics.py        # no output
grep -n "stale_dropped" /home/freakymustard/jev-rover/semantics.py                # 497,543,566
grep -n "min_interval\|max_passes\|max_age" /home/freakymustard/jev-rover/config.py  # 141,142,150
git -C /home/freakymustard/.hermes/cache/scratch/v9/clone log -1 --format=%H      # 9c33ec0…
```
