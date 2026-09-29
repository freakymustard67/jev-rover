# v7 — Cross-session persistence design note (semantics layer, proposal §15.4)

**Author:** independent verification/design subagent (v7). **Date:** 2026-09-29.
**Scope:** what auto-loads and when, staleness policy, room identity, coordinate drift, failure
modes, plus executable checks of the *shipped* persistence code. M1 approved
"persist-no-autoload" (`docs/planning/m1-plan.md:382`, `:391-392`); this note defines the
M3-ish target that answers §15.4 ("persist semantic maps across days or session-only?").
**Repo state checked:** HEAD `9c33ec0`. All experiments in scratch clone
`/home/freakymustard/.hermes/cache/scratch/w5/v7/jev-rover-1790634310`; repo untouched.

Evidence files in this run dir:
- `v7-persistence-pytest.txt` — pasted raw pytest output (probe + full suite).
- `v7-persistence-probe.py` — the probe as run in the clone.

---

## 0. Measured facts (executable checks, all in the clone)

Command: `PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider -s tests/test_v7_persistence_probe.py`
→ **10 passed in 0.41 s**; full clone suite: **88 passed in 14.19 s** (78 baseline + 10 probe).

| check | result |
|---|---|
| (1) round-trip: `SemanticStore.merge → _save_latest → load_map` | All fields round-trip: `rebuilt == st.snapshot(...)` (object equality incl. `sources=["vision","sweep"]`, `height_suspect=True`, `destination` with `object_id`). Top-level payload keys exactly `{room, saved_at, map}`. **CONFIRMED** |
| (2) corrupt inputs, exact exceptions | empty file → `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`; whitespace → `JSONDecodeError: Expecting value: line 2 column 3 (char 6)`; truncated → `JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 52 (char 51)`; valid JSON without `map` → `KeyError: 'map'`; `map:null` → `AttributeError: 'NoneType' object has no attribute 'get'`; unknown object key → `TypeError: SemanticObject.__init__() got an unexpected keyword argument 'bbox_px'`; missing object key → `TypeError: ... missing 1 required positional argument: 'id'`; unknown diff key → `TypeError: SemanticDiff.__init__() ... 'notable'`; unknown destination key → `TypeError: Destination.__init__() ... 'sweep_confirmed'`. **CONFIRMED** (all nine) |
| (3) schema version field | none: no key containing "version" anywhere in payload/map/object/diff; `_save_latest` never writes one (`semantics.py:345-349`). **CONFIRMED** (refutes any assumption of a version today) |
| (4) timing, 100-object map | merge incl. save: median **6.13 ms** (max 7.95); explicit `_save_latest`: median **5.03 ms**; `load_map`: median **0.63 ms**; `latest.json` = **35,431 B** (~354 B/object). **CONFIRMED** |
| (5) forward-compat asymmetry | adding `schema_version` at **map level** is silently ignored by `load_map`; the same key inside an object raises `TypeError`. **CONFIRMED** |
| (6) wrong-room load | a byte-copy of `roomA_latest.json` loaded as `roomB_latest.json` is accepted; payload `room` is never checked. **CONFIRMED** |
| (7) room_name path handling | `room="../escape"` wrote `escape_latest.json` **outside** `store_dir` (no sanitisation). **CONFIRMED** |
| (8) events log | single shared `runs/semantic/events.jsonl` (`semantics.py:50,367-368`) — events from two rooms land in one file; line keys `[appeared, labels, moved, t, vanished]`, no room field, ~**95 B/line**. Deviation from proposal `:145` ("per room … `<room_name>_events.jsonl`"). **CONFIRMED** |
| (9) saved age vs wall clock | saved `map.age_s` is frozen (0.0 for the saving pass; `semantics.py:348` snapshots at `last_pass_t`); the only cross-session time signal is `saved_at` (wall `time.time()`, `semantics.py:347`). **CONFIRMED** |
| (10) save failure path | `store_dir` pointing at a regular file → `merge` survives, no artifact, **no signal** (`except OSError: pass`, `semantics.py:353-354`). **CONFIRMED** |

---

## 1. (a) What to persist vs derive; what auto-loads

**Persist (v2 snapshot):** room identity + calibration provenance + geometry fingerprint;
wall-clock `saved_at`; the object list with stable ids, labels, coordinates, confidence,
sources, flags, session-relative first/last seen (`SemanticObject`, `scene.py:269-281`) and new
per-object wall times (`first_seen_wall`, `last_seen_wall`); `passes`, `model`.

**Derive at load, never trust from file:**
- `age_s` — recompute from `saved_at` vs wall now (the stored value is the age at the *saving
  pass*, measured 0.0; README lesson #2 / `m1-plan.md:97-99` already says it is recomputed every
  frame).
- `diff` — reset to empty at load; the diff baseline is the loaded map itself, the *first fresh
  pass* of the new session produces "what's new/moved since last session".
- `destination` — **never loaded**; re-resolve after a fresh pass (`resolve_destination`,
  `semantics.py:654`). A stale destination is a control-safety hazard, and the proposal's own
  design routes destinations through a fresh resolve (proposal §7).
- `_misses`, `_labels`, `next_id` — rebuilt on load (`next_id = max(numeric id suffix)+1`;
  today it resets to 1, `semantics.py:247`, so a naive load would collide ids at
  `semantics.py:297-298`).
- Staleness verdict (TTL / drift / match-rate), computed at load, not persisted.

**Loading semantics (recommended default):**

| artifact | auto-load? | rule |
|---|---|---|
| `<room>_latest.json` | **yes, gated** (M3 target; M1 ships off) | opt-in `semantics.persistence.enabled` + explicit `room_id` + TTL + calibration band + known schema version; loaded objects enter as **prior, unverified**, never as observed state |
| `events.jsonl` | **no** | append-only diagnostic; never state. Optional tail-read (last 50 lines) for a human summary only |
| `destination` (inside the snapshot) | **no** | always re-resolved after a fresh pass |
| grid / floor / homography | no | comes from config each session (geometry is re-derived, semantics is remembered) |

**Merge rule when fresh data meets loaded data.** Loaded objects are inserted into
`store.objs` with ids unchanged, `_misses=0`, `_labels` rebuilt, `next_id` bumped, and a
`reconcile_pending` flag. The first merged pass of the session is the **reconcile pass**:
- match with the existing rule (same label + nearest ≤ `match_radius_m` 0.5 m, greedy,
  `m1-plan.md:259`; `config.py:145`);
- matched → **position reset to the raw observation** (no EMA on this pass), confidence/last_seen
  refreshed, `sources` gains `"vision"` if absent; this re-anchors in one pass instead of the
  ~5 passes the α=0.4 EMA needs (0.6^5 ≈ 8 % residual);
- unmatched loaded objects → miss counter **not** incremented this pass (normal from pass 2);
- new detections → new ids from `max+1`;
- **diff events suppressed** (counted in stats as `reconcile: {matched, loaded}`), so a stale or
  drifted map cannot spam `vanished`/`appeared` at start.
After the pass, compute match rate; if `< reconcile_min_match` (0.5 default) the loaded map is
discarded beyond that point (`reconcile_dropped` counter) — wrong room / rotten memory.
From pass 2, normal merge with EMA resumes.

Per-object freshness is `last_seen_wall` + `verified_this_session`; session-relative
`first_seen_s` is kept for schema compatibility but must not be interpreted across sessions.

---

## 2. (b) Snapshot format + schema versioning

Extend the existing JSON (round-trip compatible in the read direction for v1 files):

```jsonc
{
  "schema_version": 2,                 // NEW, top-level int. Absent => legacy v1.
  "room": "synthetic-room",            // existing
  "room_id": "office-a",               // NEW: stable identity for auto-load (see (d))
  "calibration": {                     // NEW: what frame the coords are in
    "fingerprint": "sha256:ab12…",
    "width_m": 6.4, "height_m": 3.6,
    "homography": {"image_points_px": [[…]], "world_points_m": [[…]]},
    "floor_polygon_px": [[…]],
    "camera": {"width": 1280, "height": 720}
  },
  "saved_at": 1790617194.91,           // existing, wall clock
  "session": {"started_wall": 1790617000.0, "t": 1.07},   // NEW, optional
  "map": {                             // existing SemanticMap shape
    "age_s": 0.0, "passes": 1, "model": "fake-vision-v0",
    "objects": [{ …existing fields…, "first_seen_wall": 0.0, "last_seen_wall": 0.0 }],
    "destination": null,
    "diff": {"appeared": [], "moved": [], "vanished": []}
  }
}
```

**Migration policy:**
1. `schema_version` absent ⇒ v1 (M1 format, `runs/semantic/synthetic-room_latest.json`).
   Load: allowed with `frame_unverified=true` (no calibration data to compare) only when
   `persistence.allow_legacy` is true (transition default) and `room == cfg.name`.
2. Loader **must be forward-tolerant before auto-load ships**: filter unknown keys (warn) at
   object/diff/destination level instead of `TypeError` (today's behaviour, measured), and
   refuse `schema_version > current` with a clear log line. Map-level unknown keys are already
   ignored (asymmetry measured — fix deliberately, not accidentally).
3. Additive field with default ⇒ same major version; remove/rename/change meaning ⇒ bump major
   + explicit migration function; never reuse a name; never write a major-N field into a
   major-M < N file.
4. Keep the atomic write (tmp + `os.replace`, `semantics.py:350-352`); a crash leaves a `.tmp`
   orphan, never a torn `latest.json`.

---

## 3. (c) Staleness policy

Options considered, with numbers:

1. **Age decay (TTL)** from `saved_at`. Recommended default **T=72 h** (`persistence.ttl_s`),
   justification: covers the Fri-evening → Mon-morning gap (~60-66 h) that "across days"
   (§15.4) implies, while bounding how long the physical scene may have drifted; older ⇒
   session-only (explicit `--load-map` to override). Per-object age comes from
   `last_seen_wall`. A confidence-decay-by-age is *not* recommended as a silent transform
   (confidence is a raw detector score, `m1-plan.md:409`); expose age instead.
2. **Calibration/frame gating** — compare the stored homography with the current one on a probe
   grid over the floor polygon; use max deviation `Δ`:
   - `Δ ≤ 0.03 m`: same frame — load silently. (0.03 ≈ 2× the measured 1.5 cm projection noise
     for the best anchor mode, `m1-plan.md:400-401`.)
   - `0.03 < Δ ≤ 0.25 m`: drifted — load, set `frame_drift_m` flag, re-anchor on reconcile,
     suppress diff on the reconcile pass. (0.25 = `move_threshold_m`, `config.py:147`; below it,
     no false `moved`; also 71 % of the 0.35 m standoff, `config.py:159`.)
   - `Δ > 0.25 m`: refuse auto-load (session-only). Beyond the move threshold every loaded
     object would report `moved` if diff were not suppressed, and ≥ half the 0.5 m match radius
     (`config.py:145`) threatens runaway `appeared`/`vanished`.
   A strict byte-hash equality gate is *too* strict: `image_points_px` is rounded to 0.1 px and
   re-running `calibrate.py floor` (fresh clicks) changes it by ≥ that on any re-click
   (`calibrate.py:194-208`); 0.1 px ≈ 0.5 mm at the synthetic room's 200 px/m — so hash
   equality would disable auto-load after every recalibration. Hash is still stored as a fast
   "identical calibration" short-circuit.
3. **Coordinate-drift detection by content** (backstop, no extra state): reconcile match rate
   < 0.5 ⇒ discard the loaded map after the first pass (`reconcile_dropped`).
4. **Grid-based drift check** (delayed `height_suspect`, `m1-plan.md:41`): needs a *grid*
   snapshot, not just semantic objects — out of scope here; note as M3 candidate.

**Recommended default:** auto-load the map only when `persistence.enabled` and `room_id` are
set, `saved_at` within 72 h, `schema_version` known, room identity matches, and `Δ ≤ 0.25 m`;
inside 0.03-0.25 m load with `frame_drift_m` recorded and reconcile behaviour as in §1.

---

## 4. (d) Room identity

**Today:** `room_name` is `cfg.name` — config JSON `"name"`, default `"room"`
(`config.py:201`, loaded at `:231`; `config/room.synthetic.json:2` sets `"synthetic-room"`) —
passed as `SemanticsRunner(cfg, cfg.name, vision)` (`run.py:244`), which builds
`SemanticStore(semantics_cfg, room_name)` (`semantics.py:245`) and writes
`Path(cfg.store_dir) / f"{room_name}_latest.json"` (`semantics.py:350-352`). The artifact
payload also carries `"room"` (`semantics.py:346`), and the on-disk example shows
`"room": "synthetic-room"` (`runs/semantic/synthetic-room_latest.json:2`).

**Failure modes found:**
- **Collision:** two configs with the same `name` share `latest.json` and `events.jsonl`; the
  second room's save silently overwrites the first's map; events from both mix in one file with
  no room field (measured).
- **Unsafe names:** the name is used raw in paths — `room="../escape"` wrote outside `store_dir`
  (measured); `a/b` would create a subdirectory; names are not validated anywhere.
- **Rename:** changing `name` orphans the old artifacts; nothing detects that the same physical
  room changed its label; the old map never loads and the new name starts cold.
- **Wrong-room load is currently undetectable** (`load_map` ignores `room`, measured) — the
  future auto-load must enforce identity itself.

**Safety rule (recommended):** auto-load requires an explicit, validated `persistence.room_id`
(`^[A-Za-z0-9._-]{1,64}$`), distinct from the display `name`; the payload must match on
`room_id` **and** geometry fingerprint (width/height/floor polygon) **and** calibration band
(§3). Any mismatch ⇒ refuse, log one line (`[semantics] map refused: room mismatch …`),
continue session-only; never merge, never overwrite the other room's artifact, never
auto-rename. Config without `room_id` ⇒ auto-load never happens (M1-compatible: persistence is
opt-in). Rename policy: `room_id` is the identity; `name` is free to change. Both `latest.json`
and (v2) `events.jsonl` are keyed by `room_id`, with `name` kept inside the payload for humans.

---

## 5. (e) Coordinate drift between sessions

**Sources:** camera moved / re-aimed / different mount (explicitly assumed possible: "camera
fixed overhead per session (portable between rooms)", proposal `:331`), floor recalibration
(`calibrate.py:161-210`), resolution/intrinsics change, tag offset change.

**Quantified impact:**
- A "good" floor calibration still reports mean reprojection error up to 0.05 m before the tool
  complains (`calibrate.py:202-203` prints `err` and calls ≥ 0.05 m "HIGH — re-click"). So a
  recalibrated frame can legitimately differ from the stored one by ~cm-scale; worst-case
  projective error grows away from the 4 reference points (factor 2-3× near the far corners —
  **UNVERIFIED** for this room, no data in repo).
- Frame shift `Δ` displaces every stored coordinate by ≈ `Δ` (objects projected through the same
  homography check 6 in §0; consistent by construction).
- `Δ > 0.25 m` ⇒ every loaded object exceeds `move_threshold_m` (`config.py:147`) on the first
  matching pass; `Δ > 0.5 m` ⇒ most match attempts fail (`match_radius_m`, `config.py:145`) →
  the loaded map reads as entirely vanished and the room as entirely new.
- With EMA `α=0.4` (`config.py:146`) and no explicit re-anchor, a shifted frame contaminates
  stored positions over ~5 passes (0.6^5 ≈ 8 % residual left) — the map self-heals, but reports
  slightly wrong coordinates in the meantime, and `motion` may flicker near the 0.25 m line.
- Destination error = drift (labels/coords are the only source: `resolve_destination`,
  `semantics.py:672-673`); 0.25 m error is 71 % of the standoff — material for arrival.

**Policy (recommended):** measure `Δ` at load (§3); `Δ ≤ 0.03 m` treat as same frame;
`0.03-0.25 m` **re-anchor** (reconcile pass replaces coordinates instead of EMA) + suppress
diff on that pass + record `frame_drift_m`; `> 0.25 m` **invalidate** (session-only) unless
explicit `--load-map`; content match-rate backstop (§3.3). Report the drift value in
`summary["semantics"]["persistence"]`.

**Failure modes if unmitigated:** false `moved`/`vanished`/`appeared` storms at start; Jev gets
a wrong diff summary; `--find` resolves to stale coordinates; destination re-resolve may pick a
ghost object; ghost objects persist until the vanish counter removes them (2 passes,
`config.py:148`).

---

## 6. (f) Failure modes table

| # | Failure | Detection | Response (recommended) |
|---|---|---|---|
| 1 | corrupt / partial `latest.json` (truncated, empty, whitespace) | `json.JSONDecodeError` (3 exact messages measured, §0), or `.tmp` orphan present | never auto-load; log once; session-only; ignore/delete `.tmp`; next pass atomically overwrites |
| 2 | valid JSON, wrong shape (`map` missing/null, object fields bad) | `KeyError: 'map'` / `AttributeError: … 'get'` / `TypeError: …` (measured) | same as #1; `load_map` must catch **all** of these (it catches none today) |
| 3 | schema bump / future version | `schema_version > current`; unknown keys | refuse if major > current; else filter unknown keys + warn; never `TypeError` out to the loop |
| 4 | wrong room | payload `room_id`/geometry/calibration mismatch; reconcile match rate < 0.5 | refuse load; never merge; never overwrite the other room's file; session-only; log |
| 5 | stale map | `saved_at` age > TTL (72 h default); low match rate | refuse (≥ TTL); discard after reconcile pass (< 0.5 match) |
| 6 | unbounded `events.jsonl` | file size / line count (95 B/line measured) | per-room file (fix deviation from proposal `:145`); rotate at 5 MB or monthly to `events.<YYYYMM>.jsonl`; tail-read ≤ 100 lines ever |
| 7 | save silently failing (disk full / store_dir invalid) | none today (`except OSError: pass`, `semantics.py:353-354`) — measured silent | add `save_errors` + `last_save_error` to `stats()`; warn once per run; never fail the pass |
| 8 | id collision across sessions | `next_id` resets to 1 (`semantics.py:247`) | derive `next_id = max(existing)+1` on load; ids immutable once published (event log references them) |

---

## 7. (g) Jev / diff baseline, M3-M4 touchpoints

- The loaded map **is** the diff baseline for "what's new/moved" (G2, proposal `:27`): first
  fresh pass = reconcile; after it, the normal per-pass diff (`semantics.py:259-319`) reports
  changes *since last session*. Without a prior map the first pass can only report "appeared".
- Jev state: keep the M1 uncompacted flow (`m1-plan.md:380-381`) but add provenance
  (`map.age_s` from load; per object `verified_this_session`) so `destination_trustworthy` (M3,
  proposal `:196`) and the `observation_unreliable` trigger can tell "remembered" from "just
  saw". Token cost stays bounded by the M1 size test (`m1-plan.md:345`; 100-object
  `latest.json` measured at 35 KB — do not put the whole loaded map into the state; the
  existing 10-object/<6 KB bound applies).
- One-line diff summary (proposal `:155`) should be emitted **after** the reconcile pass, e.g.
  "remembered 3, re-seen 2, new 1" — never from the raw reconcile diff.
- M3: persistence config + load gates + reconcile logic + stats; scheduler audit pass
  (proposal §8) doubles as the re-verification trigger; map compaction.
- M4: sweep `confirm_objects` (`proposal:244`) should include loaded/unverified objects as
  candidates; a scan-turn fix at 2-5 cm (`proposal:232`) is strong enough to validate the frame
  and resolve the 0.03-0.25 m drift band physically.
- The delayed `height_suspect` idea (`m1-plan.md:41`) needs *grid* state from before an object
  appeared; semantic-object persistence alone does not provide it — treat as a separate M3
  design item (grid snapshot or reconstructed footprints).

---

## 8. (h) Minimal implementation sketch (~50 lines + tests)

New config (`config.py`, section `semantics.persistence`, all defaulted):
```
enabled: bool = False            # opt-in; project default stays off (M1 approved persist-no-autoload)
auto_load: bool = False          # turn on for M3 default once trusted
room_id: str = ""                # required for auto_load; validated ^[A-Za-z0-9._-]{1,64}$
ttl_s: float = 259200.0          # 72 h
drift_flag_m: float = 0.03
drift_max_m: float = 0.25
reconcile_min_match: float = 0.5
allow_legacy: bool = True        # v1 files (no calibration block)
```

Functions (all in `semantics.py`):
```python
def calibration_block(cfg: RoomConfig) -> dict          # w/h, homography pts, polygon, camera; fingerprint=sha256
def frame_drift_m(stored: dict, now: RoomConfig, n=5) -> float
    # build both H from stored/current points; project an n×n image-space probe grid
    # inside the current polygon through both; return max |Δ| in metres
def _load_map_doc(path) -> tuple[dict, int]             # tolerant read: catch JSONDecodeError/KeyError/
    # AttributeError/TypeError -> LoadError(reason); version check; unknown-key filter (warn)
def try_load_latest(store, cfg, now_wall) -> LoadReport   # gates: enabled/auto_load/room_id/file exists/
    # ttl/version/drift; on pass: rebuild objs/_labels/next_id, mark reconcile_pending,
    # destination=None, expected_age from saved_at
```
Wiring: `SemanticStore.__init__` calls `try_load_latest` (guarded, never raises);
`SemanticStore.merge` branches on `self._reconcile` (position reset instead of EMA, suppress
diff, evaluate match rate, then clear); `_save_latest` writes the v2 payload
(`schema_version`, `room_id`, `calibration`, wall fields); `_append_events` uses
`f"{room_id}_events.jsonl"` with size-based rotation; `SemanticsRunner.stats()` gains
`persistence: {loaded: bool, n: int, drift_m: float, age_h: float, dropped: str|None,
save_errors: int}`; `run.py` prints the one-line load report after constructing the runner
(near `run.py:242-247`) and adds `--load-map` (explicit override).

Offline test list (new `tests/test_semantics_persistence.py`):
1. v2 round-trip incl. `calibration`/`room_id`/wall fields; v1 file loads with
   `frame_unverified`.
2. corrupt matrix (the §0 nine cases) → `LoadReport.loaded is False`, no exception.
3. `schema_version=999` refused; unknown object key tolerated with warning.
4. room mismatch / missing `room_id` / geometry mismatch refused.
5. TTL boundary (71 h loads, 73 h refused).
6. drift: identical frame Δ=0 silent; Δ=0.05 m → reconcile + no diff events + coordinates
   reset in one pass; Δ=0.3 m → refused.
7. id stability: loaded `obj_0007` re-seen keeps id; new object gets `obj_0008`.
8. match rate 0.25 → map dropped, `reconcile_dropped` set; events file per room; rotation.

Estimated: `semantics.py` +60-80 lines, `config.py` +15, `run.py` +10, tests +120.
No repo writes were made by this note; artifacts stay under `runs/` (gitignored, `.gitignore:5`).

---

## 9. Claim ledger

| claim | status |
|---|---|
| M1 ships persistence but no auto-load; §15.4 is the open question (m1-plan:184-185, :382; proposal:323) | CONFIRMED (docs) |
| Round-trip preserves every field, incl. sources/height_suspect/destination | CONFIRMED (executable) |
| No schema version field anywhere today | CONFIRMED (executable) |
| `load_map` raises `JSONDecodeError`/`KeyError`/`AttributeError`/`TypeError` on the measured inputs; catches nothing | CONFIRMED (executable) |
| Unknown keys: ignored at map level, `TypeError` at object/diff/destination level | CONFIRMED (executable) |
| `load_map` never checks room identity; wrong-room copy accepted | CONFIRMED (executable) |
| room_name used raw in paths (`../` escape) | CONFIRMED (executable) |
| events log is shared across rooms and has no room field; ~95 B/line | CONFIRMED (executable) |
| Saved `map.age_s` is frozen; `saved_at` is the only wall signal | CONFIRMED (executable) |
| Save failures swallowed silently | CONFIRMED (executable) |
| 100-object save 5.0 ms / load 0.6 ms / 35.4 KB | CONFIRMED (measured, this machine) |
| id counter resets to 1 each session → collision risk on naive load | CONFIRMED (code `semantics.py:247,297-298`; no counter in artifact §0) |
| Calibration tool flags mean reprojection ≥ 0.05 m as HIGH | CONFIRMED (code `calibrate.py:202-203`) |
| Far-corner projective error amplifies calibration error 2-3× | UNVERIFIED (no per-room data) |
| Tag pose absolute accuracy (cm) for the camera pipeline | UNVERIFIED (README states practical 0.15-0.20 m tag size only) |
| Recommended thresholds (TTL 72 h, Δ bands 0.03/0.25 m, match rate 0.5) | judgment, anchored to cited config/doc numbers (move 0.25 m, match 0.5 m, standoff 0.35 m, anchor noise 1.5 cm) |
| `merge` cost negligible vs control loop at 100 objects (≤8 ms, ≤4 passes/min) | CONFIRMED (measured) |