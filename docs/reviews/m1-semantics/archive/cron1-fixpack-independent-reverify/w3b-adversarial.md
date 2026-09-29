# W3B — Adversarial verification of the M1 semantics fix pack (12 patches)

* Verifier: W3B subagent (Hermes), 2026-09-29 ~01:03–01:16 IST
* Subject: `/home/freakymustard/jev-rover` @ `9c33ec0` (public GitHub freakymustard67/jev-rover), worktree clean; **the real repo was read-only throughout** (final `git status --short` empty, HEAD `9c33ec0`).
* Fix pack: `wave2/w2b/patches/0001…0012` (mail format), applied in order with `git -c user.name=x -c user.email=x@local am`.
* Design doc referenced: `wave2/w2c/m2-design.md`.
* Interpreter: `/home/freakymustard/jev-rover/.venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`, pytest with `-p no:cacheprovider`, all runs from a clone root.

## Method / where the evidence lives

Two scratch clones under `/home/freakymustard/.hermes/cache/scratch/wave3/w3b/`:

| clone | state |
|---|---|
| `clone_pre` | pristine `git clone` of the real repo @ `9c33ec0` (unpatched). Later got the one-line `--trace` fix applied to demo it. |
| `clone` | same, plus all 12 patches (`git am` clean, commit `56fdceb` on top of `9c33ec0`). |

New independent scripts (not the pack's tests) live in `w3b/scripts/` and drive the real code paths
(`SemanticStore.merge`, `project_detections`, `SemanticsRunner.maybe_pass/poll`, `Perception`,
`SyntheticRoom`, `run.py` CLI). Raw terminal output for both phases: `w3b/raw-evidence/{pre,post}/`.

Post-pack the product suite is green: `pytest tests/` → **80 passed**, bare `pytest` → **80 passed**
(`pytest.ini` `testpaths = tests`), `pytest docs/planning/prototype` → 11 passed.

---

## 0. `--trace` NameError (mandatory item)

**Claim to test:** on unpatched HEAD the CLI crashes with a NameError at `run.py:339` (`print(trace_line)`).

**Pre-patch repro** (clone_pre; the crash arrives immediately after the first well-formed trace
line is printed — the stray `print(trace_line)` follows it in the same block):

```
$ git rev-parse --short HEAD
9c33ec0
$ PYTHONDONTWRITEBYTECODE=1 .../python run.py --config config/room.synthetic.json \
      --source synthetic --mission patrol --seconds 3 --no-jev --trace          # exit=1
t=  0.00 pose=( 3.20, 1.80,  +0.0) src=tag near=1.01 clear=1.01 occ=0.0 jev=hold_course(none,0.0) risk=0.0 cmd=(+0.06,-120,path)
Traceback (most recent call last):
  File ".../clone/run.py", line 388, in <module>
    raise SystemExit(main())
  File ".../clone/run.py", line 339, in main
    print(trace_line)
          ^^^^^^^^^^
NameError: name 'trace_line' is not defined
```

(also captured as `raw-evidence/trace_unpatched.out`, and by `trace_check.sh pre`.)

**No patch touches the trace line.** `grep -rn "trace_line" wave2/w2b/patches/` → no matches (exit 1).
Confirmed by inspection: patch 0010 rewrites run.py around the semantics block only; the trace
block (old lines 331-339) is outside every hunk's context.

**Post-pack re-run still crashes** (`trace_check.sh post`, `raw-evidence/post/p5_trace.txt`, exit=1):

```
  File ".../clone/run.py", line 420, in <module>
    raise SystemExit(main())
  File ".../clone/run.py", line 371, in main
    print(trace_line)
NameError: name 'trace_line' is not defined
```

**Fix proposal: `w3b/fix-trace.diff`** (one deleted line — the stray debug print; the rich trace
line above it remains):

```diff
diff --git a/run.py b/run.py
index 9c38ed6..954215e 100644
--- a/run.py
+++ b/run.py
@@ -368,7 +368,6 @@ def main(argv=None) -> int:
                               f"jev={judg.get('maneuver')}({judg.get('source')},"
                               f"{judg.get('age_s')}) risk={judg.get('risk')} "
                               f"cmd=({cmd.v_mps:+.2f},{cmd.w_deg_s:+.0f},{cmd.source})")
-                print(trace_line)
 
             if args.video or args.show:
                 canvas = renderer.draw(frame, scene, judg, cmd, executor.path)
```

**Demonstrated in both clones** (fix applied only to the scratch clones; NOT to the real repo):

* `git apply --check` → `apply_check_ok_on_pristine` (clean context even without the pack), then applied.
* `run.py … --mission patrol --seconds 4 --trace` → **exit 0** in both:
  `raw-evidence/p6_trace_fixed_pre.txt` (55 trace lines) and `p6_trace_fixed_post.txt` (52 trace
  lines), each ending in a normal `summary written to runs/summary_…json`.

**VERDICT: CONFIRMED** (bug real on HEAD; the pack does not fix it; the one-line deletion fixes it
on both patched and unpatched trees).

---

## 1. Meta-review: test counts (unpatched clone)

No `pytest.ini` / `pyproject.toml` / `setup.cfg` exists at HEAD, so bare `pytest` collects from
cwd recursively — which includes `docs/planning/prototype/test_april_sem.py`.

| command (clone_pre, unpatched) | collected | ran |
|---|---|---|
| `pytest --collect-only -q` (bare) | 78 | 78 passed |
| `pytest tests/ --collect-only -q` | 67 | 67 passed |
| `pytest docs/planning/prototype --collect-only -q` | 11 | 11 passed |

| location | text | verdict |
|---|---|---|
| `README.md:48` | `.venv/bin/python -m pytest   # 25 tests` | **STALE** — the documented (bare) command runs 78; `tests/` alone is 67. Neither is 25. |
| `README.md:247` | `tests/` … `67 tests` | **CORRECT for `pytest tests/`** (67). Caveat: it is a row describing the `tests/` dir, so it is fine — but the bare command two sections up does not produce it. |
| `docs/planning/README.md:38` | `.venv/bin/python -m pytest tests/ -q   # 67 tests, no network` | **CORRECT** — the command is explicitly scoped to `tests/`, which yields 67. |

Post-pack: `tests/` = 80, bare = 80 (the new `pytest.ini` pins `testpaths = tests`), prototype
explicit = 11; patch 0012 corrects README lines 48 and 247 to 80. **However `docs/planning/README.md:38`
is left unchanged and still says 67**, which is stale once the pack lands (now 80). Minor doc leftover.

**VERDICT:** the meta-review's "25 is stale" → **CONFIRMED**; "67 is correct for `pytest tests/`" →
**CONFIRMED** (with the caveat that bare pytest gave 78 pre-pack, so the count depends entirely on
scoping); plus one new finding: `docs/planning/README.md:38` becomes stale after the pack.

---

## 2. Check 1 — sub-`min_confidence` detection must not move an object

Script: `w3b/scripts/check1_lowconf.py`. Config: `config/room.synthetic.json` (min_confidence 0.5,
ema_alpha 0.4). Two routes: (A) hand-built `SemanticObject`s through `SemanticStore.merge`;
(B) full `Perception` → `project_detections` → `merge` with a real 1280x720 synthetic frame.

### Pre-patch (clone_pre) — `raw-evidence/pre/check1.txt`

```
OBS min_confidence=0.5 ema_alpha=0.4 move_threshold_m=0.25
OBS A_after_lowconf.x=3.0480 A_after_lowconf.y=1.2000 A_after_lowconf.confidence=0.0500 A_after_lowconf.height_suspect=False A_after_lowconf.last_seen_s=1.0 A_after_lowconf.motion=static ...
OBS B_pass2_projected=[('blue mat', 3.12, 1.2, 0.05, False)] rejected=0
OBS B_after_lowconf.x=3.0480 B_after_lowconf.y=1.2000 B_after_lowconf.confidence=0.0500 B_after_lowconf.height_suspect=False B_after_lowconf.last_seen_s=2.0 ...
```

The 0.05-score detection at x=3.12 was merged: stored x EMA'd 3.0 → **3.048**, confidence
overwritten 0.9 → **0.05**, `height_suspect` True → **False**. (The fork's "x 3.0→3.12" is the raw
detection position; the stored value moved to 3.048 at alpha 0.4 — same bug, slightly different number.)

### Post-patch (clone, all 12 patches)

```
OBS A_after_lowconf.x=3.0000 A_after_lowconf.y=1.2000 A_after_lowconf.confidence=0.9000 A_after_lowconf.height_suspect=True A_after_lowconf.last_seen_s=1.0 A_after_lowconf.motion=static A_after_lowconf.appeared=[] A_after_lowconf.moved=[] A_after_lowconf.vanished=[]
OBS B_after_lowconf.x=3.0000 B_after_lowconf.y=1.2000 B_after_lowconf.confidence=0.9200 B_after_lowconf.height_suspect=False B_after_lowconf.last_seen_s=2.0 ...
```

Position, confidence and `height_suspect` are untouched; `last_seen_s` still advances (freshness
hit) and no `moved` event is emitted. In route B the stored confidence stays at the pass-1 value
0.92 (pass-2's 0.05 does not overwrite it).

**VERDICT: CONFIRMED** — patch 0002 does what its message says.

---

## 3. Check 2 — eviction / no silent resurrection

Scripts: `w3b/scripts/check2_evict.py` (cap=3 override, churn, config validation),
`w3b/scripts/check2b_evict_default.py` (the default cap that `run.py` actually uses).

### Pre-patch (`raw-evidence/pre/check2.txt`)

```
OBS vanish_passes=2 max_misses=None
OBS t=2.0 ids=['obj_0001'] misses={'obj_0001': 2} vanished=['obj_0001']
OBS t=3.0 ... t=6.0 ids=['obj_0001'] misses=3..6 vanished=[]
OBS alive_after_6_missed_passes=True
OBS return_ids=['obj_0001'] return_appeared=[] return_vanished=[] return_labels=['blue mat']
OBS return_reused_obj_0001=True
```

`obj_0001` is never removed; a return after 6 missed passes silently re-adopts the same id
(`appeared=[]`). Config JSON with `max_misses` → `ConfigError: badcap.semantics: unknown key(s): max_misses`.

### Post-patch (`raw-evidence/post/check2.txt`, `check2b.txt`)

```
OBS vanish_passes=2 max_misses=10
OBS t=2.0 ids=['obj_0001'] misses={'obj_0001': 2} vanished=['obj_0001']   # alive until the cap
OBS t=3.0 ids=[] misses={} vanished=[]                                    # evicted on the 3rd miss
OBS alive_after_6_missed_passes=False
OBS return_ids=['obj_0002'] return_appeared=['obj_0002'] return_vanished=[] return_labels=['blue mat']
OBS return_reused_obj_0001=False
OBS churn_map_size=0 churn_next_id=7                                      # bounded map, identities churn
OBS bad_cap_validation=ConfigError: badcap.semantics.max_misses must be >= vanish_passes (2 < 5)
```

Default cap (`check2b`): alive through 9 misses, **evicted on the 10th** (`alive=False` at
misses=10) — pre-patch `alive=True` forever. Re-detection after eviction creates `obj_0002` **and
announces it** via `diff.appeared` (no silent resurrection); the vanish event still fires once at
`vanish_passes=2`, eviction itself is silent.

**VERDICT: CONFIRMED** — patch 0004 removes objects at `max_misses`, prevents id resurrection and
adds the `max_misses >= vanish_passes` validation.

---

## 4. Check 3 — label case-folding (`'blue mat'` + `'Blue Mat'` → one object)

Script: `w3b/scripts/check3_labelcase.py`.

| observation | pre-patch | post-patch |
|---|---|---|
| `store.merge` 'blue mat' then 'Blue Mat' | `A_n_objects=2`, `appeared=['obj_0002']` | `A_n_objects=1`, `appeared=[]`, x=3.008 (EMA merge), label kept `'blue mat'` |
| third pass `'blue mats'` | `A_plural_n_objects=3` | `A_plural_n_objects=1` |
| `dedupe_detections(['Blue Mat' 0.7, 'blue mats' 0.9] IoU>0.5)` | `B_dedupe_n_kept=2` | `B_dedupe_n_kept=1` (score 0.9 kept) |
| `FakeVision.infer(labels=['blue mats'])` vs fixture `'Blue Mat'` | `C=[]` (0 detections) | `C=['Blue Mat']` |
| `project_detections` on two same-place differently-cased fixtures | `D=2` objects | `D=1` (score 0.95) |

**VERDICT: CONFIRMED** — patch 0005 folds case+plurals for matching, dedupe and both adapter
filters while display labels keep their spelling.

---

## 5. Check 4 — frame-resolution guard (640x360 frame under a 1280x720 config)

Script: `w3b/scripts/check4_resguard.py`. The synthetic config IS 1280x720, so the mismatch
scenario is literal: half-scale frame (and half-scale detector boxes) against the full-scale
homography.

**Measured silent misprojection** (identical pre/post, geometry only) — the mat at (3.0, 1.2)
lands at (1.5, 2.4) when its half-scale image coordinates are read through the full-scale H:

```
OBS A_world(3.0,1.2) full_img=(600.0,480.0) half_img=(300.0,240.0) full_H_projection=(1.500,2.400) silent_error_m=1.921
OBS A_world(4.4,1.0) ... silent_error_m=2.555
OBS A_world(1.5,0.8) ... silent_error_m=1.588
```

The 1.921 m figure matches the pack's claim exactly.

**Pre-patch (`raw-evidence/pre/check4.txt`)** — no guard at all:

```
OBS real_frame_shape=(720, 1280, 3) ctx_camera_w=<absent> ctx_camera_h=<absent>
OBS C_accept1=True C_accept2=False C_skipped={'interval':0,'budget':0,'inflight':1,'no_context':0}
OBS C_warning_lines=0 C_warning=<none>
OBS C_store_objects=[('blue mat', 1.5, 2.4)] C_passes=1
```

The mismatched pass is accepted, runs and merges the misprojected object at (1.5, 2.4) — silent.

**Post-patch (`raw-evidence/post/check4.txt`)** — what patch 0008 actually does:

```
OBS ctx_camera_w=1280 ctx_camera_h=720
OBS C_accept1=False C_accept2=False C_skipped={...,'resolution': 2, 'cooldown': 0}
OBS C_warning_lines=1 C_warning=[semantics] frame 640x360 does not match the configured camera 1280x720; refusing semantic passes (calibrate at the capture resolution)
OBS C_store_objects=[] C_passes=0
OBS D_accept_matched=True D_skipped={...,'resolution': 0, ...} D_store_objects=[('blue mat', 3.0, 1.2)]
OBS E_accept_unknown_camera=True E_skipped={...,'resolution': 0, ...}
```

It refuses (`maybe_pass` → False) **before** the pass is offered, warns **once** to stderr (not per
refusal), counts `skipped['resolution']`, and leaves the store untouched; a matching frame is
accepted and projects to (3.0, 1.2); `camera_w/h = 0` (unknown) disables the check.

**Nuance:** the guard lives in `SemanticsRunner.maybe_pass`, not in `Perception.process()` as the
m2-design §1.4 sketch suggested, so it covers the semantics path only (a wrong-sized frame still
flows through the rest of `process()`). For the stated requirement — "raise or warn loudly in the
semantics path" — that is exactly on target, and it is a *refusal* plus a warning, not just a warning.

**VERDICT: CONFIRMED.**

---

## 6. Check 5 — probe patch 0007 ("7x3 median instead of a single pixel")

Script: `w3b/scripts/check5_probe.py`. Diff message intent: MJPG ringing / a dead pixel / a speck of
contact shadow could flip the single-pixel probe; sample a 7x3 patch and take the per-channel median
(`probe_px` still sets the offset).

| observation (frame: real 1280x720 synthetic render, blue mat bbox, probe at (600,546)) | pre | post |
|---|---|---|
| `A_base` (clean floor under base) | False | False |
| `A_single_dead_pixel` (one pixel set to black at the probe point) | **True** (flipped) | **False** (median survives) |
| `PROBE_PATCH_PX` module constant | `<absent>` | `(7, 3)` |
| `B_on_nonfloor_mat` (box resting on a painted non-floor mat; probe below its base) | True | True |
| `B_base_on_mat_free_floor` (same box, no mat) | False | False |
| `E_hs_probe_off_frame` / `E_hs_outside_polygon` / `E_hs_unobserved_cell` | True / True / True | True / True / True |
| `F_hs_probe_px0` (probe_px=0 still moves the offset to (600,540)) | False | False |
| `D_hs_whole_patch_dark` | — | True |

**Footprint probes** (darkening subsets of the 7x3 window):

| darkened | pre | post |
|---|---|---|
| 10 px (rows py±1, 5 cols) | False | False |
| 12 px (rows py±1, 6 cols) | False | **True** (median flips at ≥11 of 21 px) |
| only row py (the probe row, 7 px) | **True** | **False** |
| row py+2 (outside the patch) | False | False |
| cols px+4..px+6 (outside the patch) | False | False |

The 11/21 flip threshold and the no-op outside row py-1..py+1 / cols px-3..px+3 independently
confirm the window really is 7x3 = 21 px centred on the probe point.

**Documented M1 rule intact:** an object resting on a non-floor-coloured mat still gets
`height_suspect=True` (B=True post-patch; the w2a demo's result reproduces). The out-of-frame,
outside-polygon and unobserved-cell guards all still return True.

**Behaviour deltas caused by the median** (both directions, real changes vs pre-patch):

1. *More sensitive:* rows py−1 and py+1 dark (contact shadow / mat edge just above and below the
   probe row) while the probe row itself is clean floor → pre **False**, post **True**.
2. *Less sensitive:* a 1-px-tall dark line exactly on the probe row (7 of 21 px) → pre **True**,
   post **False** (minority of the patch is ignored).

Neither contradicts the documented rule text ("floor-coloured means the object rests on the floor"),
but the effective probe is now "majority of a 7x3 px patch", which shifts which speckle sizes flip
the flag. Worth one line in the M1 notes; not a defect.

**VERDICT: CONFIRMED (with the two documented sensitivity deltas above).**

---

## 7. Check 6 — CLI tri-state (`--semantics off` override, `--semantics-once` wiring)

Harness: `w3b/scripts/cli_check.sh` (P1–P4), run against both clones with a purpose-built config
`w3b/raw-evidence/enabled.json` (copy of `config/room.synthetic.json` with `semantics.enabled=true`,
`audit_period_s=1.5`, `min_interval_s=0.5`, `max_passes_per_min=60`).

| run | pre-patch (`clone_pre`) | post-patch (`clone`) |
|---|---|---|
| P1: enabled config + `--semantics off --find "blue mat"` | `[semantics] enabled: … (M1: one pass…)`, `passes=1` — OFF did **not** override | `[find] ignored: --semantics off disables the semantic layer`, `"semantics": null` — OFF overrides |
| P2: enabled config, **no** `--semantics-once`, 6 s | `passes=1` | `passes=4`, `passes_started=4`, cadence line: `mission-start pass + audit every 2s` |
| P3: enabled config + `--semantics-once`, 6 s | `passes=1` (indistinguishable — the flag is inert) | `passes=1`, cadence line: `one pass at mission start` |
| P4: `--semantics fake --semantics-once --find "blue mat"` (no `--mission`), 3 s | exit 1, `--mission goto needs --waypoint NAME` | exit 0, `[find] 'blue mat' -> blue mat at (3.00,1.20) m … object=obj_0001`, `"mission": "none"`, `passes=1` |

The "inert" claim is verified statically too: pre-patch `run.py` mentions `--semantics-once` only in
`add_argument` (line 191); post-patch it is read at lines 268 and 322.

Extra (from patch 0010's message): `--semantics fake` over a config that names
`model.kind="local"` (`w3b/raw-evidence/local.json`) —
pre: exit 1 `NotImplementedError: vision model kind 'local' is planned for M2; only 'fake' ships in M1`;
post: exit 0 with `[semantics] enabled: model=fake-vision-v0 …` (the adapter is force-replaced).
Consistent with the design doc's acceptance command (§ m2-design ~line 492).

**VERDICT: CONFIRMED** — tri-state override, `--semantics-once` wiring (one pass vs mission-start +
audits), the idle `--find` mission, and the forced fake adapter all behave as claimed.

---

## 8. Residual observations (not blockers)

1. **Sub-threshold freshness keeps phantoms alive (0002 × 0004 interaction).** A persistent 0.05-
   score blob near a tracked object refreshes `last_seen_s` and resets `_misses` on every pass, so
   that object can neither vanish nor be evicted while the phantom recurs. This is the documented
   intent of 0002 ("keeps the object alive"), but it does mean a stale model can pin an entry
   indefinitely; the 12-patch set has no expiry for "fresh but never above min_confidence" hits.
2. **Guard location.** Resolution mismatch is refused only on the semantics path (see §5 nuance);
   `Perception.process()` and the renderer are unchecked (the m2-design §1.4 sketch had suggested a
   warn in `process()`).
3. **`docs/planning/README.md:38`** still says 67 tests after the pack; `tests/` is now 80.
4. **Probe sensitivity window** changed in both directions (§6 deltas 1–2).
5. Pack corroboration: `wave2/w2b/evidence/fix10-enabled-default.txt` (60 s audit period, 2.1 s run,
   `passes=1`) is consistent with my P2 rerun at a shorter audit period (`passes=4`); no contradiction.
6. Only the six named checks were adversarially re-driven; patches 0006 (centres-dedupe), 0009
   (cooldown counter), 0011 (approach_point) and 0012 were covered only by the full green suite
   (`pytest tests/` 80/80 post-pack) plus the CLI runs above.

## 9. Evidence index

```
w3b/fix-trace.diff                        one-line --trace fix (applies to 9c33ec0 and to the packed tree)
w3b/raw-evidence/trace_unpatched.out      pre-patch --trace NameError (run.py:339)
w3b/raw-evidence/pre/  post/              per-check raw stdout/stderr for both phases:
    check1.txt check2.txt check2b.txt check3.txt check4.txt check5.txt
    p1_off_override.txt p2_enabled.txt p3_once.txt p4_find_no_mission.txt p5_trace.txt
    git_am.txt                            `git am` transcript (12/12 applied, no fuzz)
w3b/raw-evidence/p6_trace_fixed_{pre,post}.txt   --trace runs with fix-trace.diff applied (exit 0)
w3b/raw-evidence/enabled.json local.json  purpose-built CLI configs
w3b/scripts/check{1,2,2b,3,4,5}_*.py      the five independent check scripts
w3b/scripts/cli_check.sh trace_check.sh trace_fix_check.sh local_fake_check.sh
```

Reproduce an individual check (from the packed clone):

```bash
cd /home/freakymustard/.hermes/cache/scratch/wave3/w3b/clone
W3B_CLONE=$PWD W3B_OUT=$PWD/../out/post PYTHONDONTWRITEBYTECODE=1 \
  /home/freakymustard/jev-rover/.venv/bin/python ../scripts/check1_lowconf.py
```