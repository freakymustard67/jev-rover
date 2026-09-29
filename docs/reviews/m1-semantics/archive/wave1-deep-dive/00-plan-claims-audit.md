## Audit: M1 plan claims vs current jev-rover

**Context:** the plan's header ("no code written yet") is stale — M1 is committed (`f352f61`, 14 files, +1745/−12; identical file list to `58fb7c9..f352f61`). `/tmp/opencode/m1-plan.md` is byte-identical to `docs/planning/m1-plan.md`. `PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider`: **78 passed**, git status clean before/after; nothing created/modified.

### 1. Claim-by-claim (pre-M1 rev `58fb7c9` and current tree)

1. **Scene fields all defaulted (plan cites scene.py 266-285)** — VERIFIED. Current `scene.py:327-350` all defaults incl. `semantics=None`, `sweep=None`; pre-M1 `58fb7c9:scene.py:265-285` all defaults. Citation is stale: current 266-285 is the semantics block (264-291). **Caveat:** "old serialized scenes still parse" is UNVERIFIABLE — no Scene deserializer exists anywhere (only `config.from_dict`, `semantics.load_map:373`); no reader of scene JSON in repo or tools (grep clean).
2. **`config._section` ignores unknown keys** — VERIFIED pre-M1 (`58fb7c9:config.py:215-228`, loops `fields(typ)` only). Current: strict only for the four new sections (`config.py:241-245,362-369`); legacy tolerance test at `tests/test_semantics_schema.py:67-70`. Structural delta: `_section` now recurses nested dataclasses + `get_type_hints` for *all* sections (`config.py:370-396`) — tested but wider than "ignores unknown keys".
3. **runs/ gitignored** — VERIFIED `.gitignore` `runs/`; default `store_dir="runs/semantic"` `config.py:154`.
4. **`build_state` pops only `mission`** — VERIFIED `tactics.py:206-210`; `test_semantics_schema.py:96-98`; `tactics.py` absent from `f352f61` (untouched).
5. **`Tactician.offer` read-only precedent** — VERIFIED `tactics.py:272-292`; state built caller-side, comment 285-287.
6. **Perception mutated ~15 Hz on main thread** — VERIFIED `run.py:187` (default 15.0), `run.py:287`.
7. **Full-res homography vs 0.5-scaled frames** — VERIFIED `perception.py:657-661,744`; `run.py:243,294` pass full-res. Caveat: frame resolution vs `camera.width/height` never validated (see R1).
8. **OccupancyGrid has `occupied_thr`/`stale_s`** — PARTIAL MISMATCH: constants/params live on `OccupancyGrid` (`OCCUPIED_THR=0.42` `perception.py:301`; `observed(t, stale_s)`:368) but the *fields* are on `SemanticContext:157-161`, sourced `720-721`.
9. **"README lesson #2" (age_s recomputed)** — MISMATCHED: `README.md:77` "Rules of thumb learned the hard way"; item #2 is "White quiet zone" (`:82-83`). No age_s/freeze text in README (repo grep finds only `docs/planning/m1-plan.md:97`). Behavior exists (`semantics.py:322-333,547-548`).
10. **"25 existing tests"** — VERIFIED (git grep: 7+2+6+5+5). Actual new tests = **42** (67 fns, 78 collected), not "~22"; README:247 says 67 — internally consistent with 25+42.

### Plan-vs-implementation coherence (pinned decisions)

- **C1 probe rule** ✅ `semantics.py:176-203` (floor_lab None→True:187; outside image→True:193; polygon/lab/cell checks; `maybe_pass` refuses `no_context` 506-508). **C2 reject-never-clamp** ✅ `:222-225`, no clip/clamp in file, counted in `PassResult.rejected:406`. **C7 Detection dataclass** ✅ `:52-64`.
- **§3 schema** ✅ `scene.py:269-350` incl. extra `Destination.object_id:291`. `sources` is a plain list (`:276`); plan's "set semantics, sorted" not implemented (vacuous with one source — reopen at M4).
- **Config keys/defaults** ✅ `config.py:120-187` except: `min_label_score=0.34` not the table's 0.35 (matches the later "findings folded in"; plan internally inconsistent); added `model.fixtures:125` and `project.point_by_label:132` (not in §4 block).
- **Interface drift vs §4:** `build_vision(cfg, homography)` (`semantics.py:114`); `FakeVision.from_world(entries, homography)` (arg order swapped); `SemanticStore.merge(objects, rejected, model, t_pass)` not `(PassResult, ctx)` (`:259-260`); `SemanticsRunner.__init__` requires `vision` (`:485`); `maybe_pass(t, ctx, frame, …)` (`:500`). Heuristics: EMA on x,y only, confidence raw (`:282-284`) and Jaccard ranking (`:601-608`), matching folded notes, contradicting the plan's own pinned table.
- **`--find` "prints + sets nothing"** — divergence: `run.py:303` calls `runner.set_destination(dest)` → lands in `scene.semantics.destination` and persisted `latest.json` (no driving, correct).
- **Acceptance gaps:** no loop-rate invariant test, no zero-worker-thread assertion, no CLI-level `--find` e2e; state-size bound exists at `test_semantics_schema.py:89-99` (not integration as §5 implies).

### 2. Ranked missed risks

- **R1 (med-high): frame-resolution coupling unvalidated.** `semantic_context` uses the config-resolution polygon as-is (`perception.py:707`) while `FloorModel` scales its polygon to the actual frame (`:253-254`). If the capture resolution ≠ `camera.width/height`, projections and the probe are silently wrong. Recommend: assert `frame.shape == (camera.height, camera.width)` in `process()`/`semantic_context`, or scale the polygon like `FloorModel`.
- **R2 (med): `--semantics off` doesn't override `semantics.enabled`** (`run.py:242`): "off ⇒ byte-identical" holds only for `enabled:false` configs; an enabled config still spawns the worker. Recommend tri-state flag (unset/off/fake).
- **R3 (med): acceptance tests #5 and parts of #1/#3 are claimed but absent.** Add loop-rate invariant, zero-thread check, CLI `--find` e2e.
- **R4 (low-med): fingerprint/`decision_needed` ignore semantics** (`tactics.py:255-270,277`) — semantics changes never re-ask Jev; deliberate M3 scope but worth documenting since tokens are billed only on geometry-triggered calls.
- **R5 (low): summary/scene-log shape changes** (`run.py:377` always emits `"semantics"`; scene logs +2 keys) — note for any external log consumer.
- **R6 (low): stale citations** — scene.py line range, "README lesson #2", "~22 tests"; patch the plan doc to match committed reality.

**No files created or modified.**