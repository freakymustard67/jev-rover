# Run REPORT — cron pass #3 (stamp 20260928-2220)

Hourly verification & improvement pass for the jev-rover semantics-layer project.
Wall: 2026-09-29 ~03:50–04:15 IST. Dispatcher: cron job 645e9ffc8cbc. Batch: 3 parallel
subagents (`deleg_3e8375ba`), all completed synchronously; all spot-checked by the cron.

## Delta check (step 2)

- Master `/home/freakymustard/jev-rover` = `9c33ec0`, worktree clean, origin HEAD same.
  No new commits → no delta to verify. LEDGER last-seen HEAD unchanged.
- PR #1 (`review/m1-semantics-audit`): open, `mergeable=true`, `mergeable_state=clean`,
  `merged=false`, 14 commits, 0 comments, 0 reviews; last update 2026-09-28T19:57:52Z
  (the title rename already logged). No owner action. R2 stays parked.

## Task selection (step 3)

Top 3 open backlog items: **V6** (sweep payload spec + watchdog interleave), **V7**
(cross-session persistence design), **V8** (viz semantics overlay). Dispatched as 3
parallel subagents; each wrote its full report disk-first under `runs/20260928-2220/`.

## Results

### V6 — `v6-sweep-payload.md` (34.6 KB)

- Payload spec v1: 4 B/sample core (`angle int8` rel deg, `range uint16` **mm**,
  `status uint8`) + 16 B chunk header; optional `signal_mcps`/`ambient_mcps` (uint16
  centi-MCPS) and `t_us` flag-gated. 181 samples = 740 B core / 1464 B full; chunk 64 →
  272/400 B datagrams; max chunk for 512 B policy = 124 samples. Status codes + field
  provenance quoted from `refs/taskA6/um2356.txt` + datasheet (`ds_text.txt`).
  Proposal correction: `ranges[cm]` throws away sensor resolution (native mm).
- Watchdog ground truth (cites `rover_esp32.ino:649-652, 685-690, 934-943`): trip =
  `(now − g_cmd.lastMs) > 400` evaluated in the control tick; `lastMs` is stamped at
  **UDP-poll time** and `rvrPollUdp()` runs before the tick every pass → a blocking
  sensor read cannot trip the watchdog **while the host is alive**; it freezes the tick
  instead. One-call sweeps freeze the safety layer 3.4–181.7 s; sim trip latency
  0.84–10.57 s vs 0.40 s interleaved. Interleave rule stated (≤5 ms/pass, non-blocking
  poll, UDP first) + text-only firmware sketch + 6 offline test additions.
- NEW findings:
  1. `link.py:107-111` crashes on binary scan chunks: `json.loads(bytes)` raises
     `UnicodeDecodeError` (byte ≥ 0x80, e.g. |angle|>63°) which is **not** caught by
     `except json.JSONDecodeError`; escapes `telemetry()` → kills the run loop. Needs
     magic-byte sniff (e.g. 0x53) before decode. (I re-read the clause + child's
     `check_parser.py` demos it.)
  2. Desmear refutation: the §10.1 / `sweep-validation.md` "1.0 s sweep" is physically
     unreachable (≥33 ms/sample → ≥3.0 s); at 3.25 s / 3.6 s the ±25% motion-error case
     collapses from **100% → 13% / 10%** success (p50 error 22 cm). Pivot cases stay
     100%. Recommendation: 91×2° for stationary/scan-turn; 31×6° while driving.
  3. Part mismatch: firmware's front ToF is a **VL53L0X** (2.0 m validity window,
     `ino:58-63, 214, 416-418`), not the VL53L1X the 4 m/25° assumptions need;
     `config.py` `SweepSensorConfig.max_range_m=4.0` is only honest with an L1X fitted.
- Unverified (flagged): Adafruit L0X `readRange()` block duration; ESP-IDF UDP mailbox
  depth; real I2C transaction times; servo make/speed.

### V7 — `v7-persistence.md` (24.7 KB)

- Executable probe (`v7-persistence-probe.py`, pasted output in
  `v7-persistence-pytest.txt`): 10 checks, all green; full clone suite 88 passed.
- Measured facts: save→load round-trip preserves all fields; payload keys exactly
  `{room, saved_at, map}`; **no schema version** (map-level unknown keys silently
  ignored, object-level unknown keys → `TypeError` — hard break on schema bump);
  **wrong-room loads undetectable** (`room` never checked); `room='../escape'` writes
  outside `store_dir` (no sanitisation); single shared `events.jsonl` for all rooms
  (deviates from proposal `:145`); `age_s` frozen at save (use `saved_at` wall time);
  `next_id` resets each session → naive-load id collision; save failures silently
  swallowed (`semantics.py:353-354`); 100-object save/load = 5.0/0.6 ms.
- Design recommendation: gated **map-only** auto-load — opt-in
  `semantics.persistence.enabled` + explicit `room_id` + TTL 72 h + calibration drift
  bands (≤0.03 m silent / ≤0.25 m re-anchor+damp diff / >0.25 m refuse) + known
  schema_version; events/destination never auto-loaded; first fresh pass is a
  reconcile pass (position reset, diff suppressed, match-rate ≥0.5 backstop);
  v2 payload adds schema_version, room_id, calibration block, per-object wall times.

### V8 — `v8-viz-overlay.md` (14.9 KB) + `v8-evidence/`

- Prototype: `viz.py` only, +72/−2; `Renderer(..., show_semantics=False)` — default off,
  matches existing viz switch style. Draws per-object dot + id/label/conf (ring when
  `height_suspect`), destination diamond, approach point + rover→approach line, on
  camera view **and** minimap, reusing `_world_to_px` / `_mm_pt` and
  `semantics.approach_point` (same function the `--find` path uses).
- Evidence: flag-off render byte-identical to pristine clone (sha256
  `c53098b2…`, all three copies); on-vs-off = 6836 changed px; exact colour counts
  (magenta/yellow/orange) absent off / present on; no crash with empty map /
  `semantics=None` / `grid=None`; deterministic re-run; 67/67 repo tests in the clone.
- Integration: 2-line run.py change (`--viz-semantics` argparse flag →
  `Renderer(..., show_semantics=args.viz_semantics)`); no other plumbing needed
  (`scene.semantics` already set each tick).
- Unverified: end-to-end with the flag wired into run.py and real-camera rendering
  (left as the 2-line proposal).

## Cron spot-checks (independent, this session)

- `git status` clean @ `9c33ec0` before dispatch and after all children.
- Firmware re-read: accept path stamps `lastMs` (line 652); watchdog eval (689);
  `loop()` poll-before-tick (938→942); VL53L0X default + 2.0 m cap (58-63, 214).
- `link.py:107-111` re-read — `except json.JSONDecodeError` indeed misses
  `UnicodeDecodeError` for non-UTF8 bytes.
- `semantics.py:342-354, 373-384` re-read — no version field, room ignored,
  `except OSError: pass`, `next_id` not persisted.
- PNG evidence: all off-renders sha `c53098b2…` (identical); my own pixel diff
  on-vs-off = **6836** (matches child); overlay visually verified by image inspection
  (labels + confidences 0.92/0.85/0.80 visible, yellow diamond, orange approach line
  ending in an arrowhead).
- `desmear_recheck.py` re-run by me: 100% @1.0 s → **13% @3.25 s / 10% @3.6 s**
  (±25% err) reproduced exactly; pivot 100% at both durations.
- v7 probe re-run by me: **10 passed in 0.38 s**.
- `config.py:163-181` re-read: `SweepConfig{enabled, sensor, match, desmear}` — no
  `step_deg`/`rate_hz` fields (matches child claim).

## Backlog movements

- V6, V7, V8 → `[x]` (outcomes recorded in LEDGER).
- Added: **I9** scan link fix + codec (from V6), **I10** sweep docs errata (from V6),
  **I11** persistence hardening pack (from V7), **I12** viz overlay adoption (from V8).
- S1 (delta watch): applied, nothing to verify. R2: parked (PR untouched).

## Files

- Reports: `runs/20260928-2220/{v6-sweep-payload,v7-persistence,v8-viz-overlay}.md`
- Evidence: `runs/20260928-2220/v8-evidence/*.png` + demo/probe scripts;
  `v7-persistence-probe.py` + `v7-persistence-pytest.txt`; `refs/taskA6/` (ST doc
  snapshot copied from scratch for durability).
- Scratch (can prune): `~/.hermes/cache/scratch/w5/{v6,v7,v8}/`.
