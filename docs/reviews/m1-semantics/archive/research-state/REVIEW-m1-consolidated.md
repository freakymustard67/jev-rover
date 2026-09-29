# jev-rover — M1 semantics layer: consolidated deep review

**Scope:** the M1 “semantics layer” — plan (`docs/planning/m1-plan.md`), proposal (`docs/planning/semantics-layer-proposal.md`), and the committed implementation (`f352f61`, repo tip `9c33ec0`).
**Method:** two waves of independent, **read-only** research. *Wave 1:* 8 parallel subagents — plan↔code audit, threading/snapshot seam, store heuristics, destination/Jev, open-vocab landscape, ToF sweep + height math, tests/acceptance, repo/CI coherence. *Wave 2:* adversarial verification — 7 executable demos driving the real code, a 12-fix patch package built and validated in scratch clones, an M2 design note, and a meta-review that re-verified 19 claims and corrected 4 wave-1 overstatements. The repository and `/tmp/opencode` were never modified; all experiments ran in scratch clones.
**Artifacts:** `round1-reports/` (8 reports + evidence) · `runs/20260929-wave2/` (demo scripts + logs, patches, design note, meta-review) · this document.

---

## 0. Verdict

**Implementation: A−. Plan doc: C+. Proceed with the M1.5 list (below) — not a rework.**

The implementation is faithful to its pinned decisions, its concurrency discipline is sound (single in-flight pass, immutable snapshot, stale-at-poll), the suite is green, the control/reflex path is genuinely untouched, and no fabricated results were found in either wave. The real weaknesses:

1. A plan doc that has drifted from the code it pins (see §7 errata).
2. Three of five acceptance items only partially met at `9c33ec0` (see §3).
3. A false-positive class in the probe rule (`height_suspect` on a mat) for the plan’s own headline object — **latent**, because nothing consumes the flag until M3.
4. The unexamined frame-space contract (raw vs undistorted) that will bite live camera use in M2 (see §2.1).

---

## 1. Behavioral claims — adjudicated with executable demos [executed]

All demos drive the real committed code (clone of `9c33ec0`, repo interpreter, `PYTHONDONTWRITEBYTECODE=1`). Verdicts are idempotent across re-runs; the shipped suite covers **none** of these behaviors.

| # | Claim | Verdict | Key evidence |
|---|-------|---------|--------------|
| 1 | Insertion-order greedy merge → two same-label objects can swap identities | **CONFIRMED** (with nuance) | Older `obj_0001` consumed the detection nearer newer `obj_0002` (0.28 m vs own 0.45 m); association stayed swapped on the next pass; reversed creation order → no swap |
| 2 | Object resting on a non-floor-colored mat → `height_suspect=True` | **CONFIRMED** | Probe LAB distance 84.0 ≥ tol 26.0 on mat / 0.0 on bare floor; reproduced end-to-end through `SemanticsRunner` (`worker_errors=0`) |
| 3 | No eviction: unmatched objects never removed; a 50-pass-stale object resurrects with the same id | **CONFIRMED** | After 50 empty passes: still in map, `misses=50`; pass 52 → same `obj_0001`, `appeared=[]` (silent resurrection) |
| 4 | Below-`min_confidence` detection (0.05) still moves the object and overwrites flags | **CONFIRMED** | `x 3.0→3.12` (EMA α=0.4), `conf 0.90→0.05`, `height_suspect False→True`, `moved` event |
| 5 | Wrong-sized frame → no error, silently wrong projection | **CONFIRMED** | 640×360 frame under 1280×720 config: **1.92 m** projection error, no exception, `worker.errors=0`; `for_scale(0.5)` → 0.000 m; full-res bbox silently clipped → 0.61 m |
| 6 | Case-sensitive label matching → duplicate objects for `'blue mat'` + `'Blue Mat'` | **CONFIRMED** | Second object created (`appeared`); query then ties 1.00/1.00 |
| 7 | Two 0.2 m passes leave `motion='static'` (total 0.4 m) | **REFUTED as stated** | Straight 0.2 m/pass → `['static','moved']` (raw compounds 0.200→0.320 > 0.25); `static/static` only for oscillation (net 0) or estimate-relative stepping |

**Nuances (kept for honesty):**
- **Claim 1:** in the headline scenario the swapped pairing was *also* the minimum-cost pairing under the code’s own metric — the root cause is association against the lagging smoothed estimate with per-pass motion near the 0.5 m match radius; insertion order is still decisive (reversed order → no swap). The shipped test only exercises well-separated (3 m) objects.
- **Claim 2:** the mat flag is the *documented* C1 rule, not an accident; it is honest only as “projection untrustworthy”, not as “elevated”. The shipped synthetic room contains no mat, so no test covers this case.
- **Claim 7:** consecutive same-direction motion reads as `static, moved` — the genuinely hidden cases are the first sub-threshold step (by design), oscillation, and estimate-relative stepping.

---

## 2. Confirmed defects & risks

### 2.1 M2-blocking

**Frame-space contract (raw vs undistorted).** `run.py:294` hands the worker the *raw* pre-undistort frame, while pose, floor model, polygon and `H` all live in *undistorted* space; `calibrate.py cmd_floor` clicks the raw frame despite its docstring; `viz` draws on the raw frame. Measured magnitude (K=800/640/360, k1=−0.15, 1280×720): corners move **46–88 px** → **15–45 cm** floor error. Fix design (9 exact edit points) in the M2 design note §1: expose `Perception.frame_h` and pass *that* everywhere consistent.

### 2.2 Robustness / correctness (latent or minor today)

- **Frame-resolution silent misplacement** [executed]: nothing compares `frame.shape` against `camera.width/height`; `Camera.read` never verifies the negotiated capture size. A driver that ignores/clamps the requested size silently misprojects everything (1.92 m in the demo). Patch 8 adds a fail-loud refusal + counter.
- **Merge/store, five issues** [executed, one demo each]: (a) insertion-order greedy assignment (swap demo above); (b) no eviction → unbounded map growth + silent id resurrection (50-pass demo); (c) sub-`min_confidence` detections still drag positions/flags (0.05-score demo) — `min_confidence` gated only `diff.appeared`; (d) labels compared case-sensitively in merge/dedupe while adapters filter case-insensitively; (e) the plan’s “centres < 0.15 m” dedupe clause was not implemented (IoU only). Patches 1–6 address all five.
- **Probe rule** [executed for the mat frame; mechanism-read otherwise]: single-pixel probe sits at/below the noise floor (~2 cm at shipped scales) — one shadow speck or dead pixel flips the flag; shadow-aware split untested. Patch 7 (7×3 median) + staged plan in §6/D4.
- **Destination matcher**: knife-edge Jaccard (“bottle” vs “plastic water bottle” = 0.333 < 0.34 cutoff → dropped); no `-es/-ies`/typo tolerance (shipped naive fold: `boxes`→`boxe`); ambiguity→Jev `Choice` path is synchronous, unbudgeted, uncached (dormant — `run.py:299` passes `jev=None`, test-only). Not in the package; recommended (D5, backlog).
- **CLI drift**: the plan’s acceptance #3 command was **unrunnable as written** (`SystemExit: --mission goto needs --waypoint NAME`); `--semantics-once` parsed but never read; `--semantics off` did not override an enabled config (`run.py:242`); `--find` persists a destination while the plan text says print-only (benign — no driving wired). Patch 10 fixes all four (with one flagged interpretation, D3).
- **`--trace` crash** 🆕 *(found by the wave-2 meta-review; missed by all 8 wave-1 reports)*: `run.py` had a dead `print(trace_line)` printing an undefined name — `--trace` died with `NameError` on the first frame, dead since `c93e25c`. **Fixed on this branch** (commit “run: drop dead print(trace_line)…”), verified NameError → clean trace output.
- **No CI**: a PR triggers zero checks; the `--trace` bug and doc drift would have been caught by a trivial `pytest` job.
- **Loop-rate invariant test absent**: only approximate substitutes exist (submit/poll timing asserts, worker:74 / integration:96,101). Recommended, not in the package.

### 2.3 Corrections to wave-1 reporting (from the adversarial meta-review — kept for honesty)

- **Report 07’s “README:247 / docs/planning/README.md:38 counts are stale” is wrong** — both are correct (`tests/` really is 67 and that doc’s command is `pytest tests/`). Only `README.md:48` (“25 tests”, bare `pytest` → 78) was stale.
- **Report 00’s “README lesson #2 — MISMATCHED” is overstated** — the plan’s sentence is a valid application; only the `age_s` nuance holds.
- **Report 02’s “mat false positive CONFIRMED” was mechanism-derived, not executed** at the time; wave 2 executed it (claim 2 above). Its flat-mat height figure (0.93 m) is setup-dependent; realistic setups give 0.46–0.69 m.
- **Report 03’s “find the water bottle → 1.0”** only holds for a fixture labeled exactly “water bottle”; with “plastic water bottle” it is 0.667. The knife-edge finding (0.333 < 0.34) is the load-bearing part and is correct.

---

## 3. Acceptance status (vs the plan)

At `9c33ec0`: **partial** — the headline acceptance command was unrunnable as written; the cooldown counter was absent; a loop-rate invariant test does not exist. The meta-review’s count: *3 of 5 acceptance items only partially met*. This branch closes two of them (patch 9 counters, patch 10 CLI), and captures the corrected command’s exact output:

```
run.py --config config/room.synthetic.json --source synthetic --mission patrol \
       --seconds 20 --no-jev --semantics fake --find "blue mat"

[find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92 object=obj_0001;
       approach (3.13,1.53) standoff=0.35 m     (semantics.passes=1, errors=0)
```

(Plain `--semantics off` byte-identity and the loop-rate invariant remain recommendations.)

---

## 4. Fix package (12 verified diffs) + reviewer addendum

Built and validated in scratch clones: baseline **78 collected/78 passed** (67 product + 11 stray prototype tests) → patched **80 passed** (25 legacy + 55 semantics, `testpaths = tests`);
all 12 apply cleanly in a fresh clone via `git am`; **no new dependencies**; +13 new test functions. Each fix is one commit on this branch; rationale in `patches.md`.

| # | Fix |
|---|-----|
| 1 | Distance-ordered best-first merge assignment (swap fix; no Hungarian/scipy) |
| 2 | `min_confidence` gate on position/flag updates (freshness kept) |
| 3 | `height_suspect` 2-of-3 hysteresis |
| 4 | `max_misses` eviction cap + state-bound guard (measured: base 2 039 B, 10 objects 4 333 B < 6 KB) |
| 5 | Label canonicalisation (casefold + `-s/-es/-ies`) for matching + adapter filters |
| 6 | Restores the “centres < 0.15 m” dedupe clause (world-space bbox centres) |
| 7 | Probe 7×3 median patch instead of a single pixel |
| 8 | Frame-resolution guard (refuse + warn once + counter) |
| 9 | Cooldown refusals counted in `skipped` stats |
| 10 | run.py: tri-state `--semantics`, wired `--semantics-once`, idle default for `--find` (acceptance now runs) |
| 11 | `approach_point` never overshoots the destination |
| 12 | `pytest.ini` `testpaths = tests` + README counts corrected |

**Reviewer addendum:** commit 13 — drop dead `print(trace_line)` (fixes `--trace` NameError; verified before/after on the synthetic room).

**Deliberately not implemented (recommended only):** metre-based + shadow-aware probe; mat/cover-aware probe; frame-space contract (M2 blocker); merge off the control loop; asymmetric label scoring + typo tolerance; Jev Choice budgeting; CLI smoke tests; loop-rate invariant; CI.

---

## 5. M2 design note — what it settles

Full text in `m2-design.md`. Headlines:
- **Frame-space fix** (frame_h handoff; 9 exact edit points; rejected alternatives documented).
- **LocalVision = MM-GDINO-T** (`openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det`): re-verified ungated, Apache-2.0, `model.safetensors` = 692,015,412 B; `transformers>=4.55` first release with MM-GDINO; fallback IDEA-Research GDINO-T. Prompt convention verified in the processor source: lowercased, `". "`-joined; class-only prompts by default, colour verified from crops in a stage 2.
- **RemoteVision**: one JSON POST (base64 JPEG + labels + thresholds + timeout) → detections JSON; token only from env (`JEV_ROVER_VISION_TOKEN`); injectable transport for hermetic tests; bbox-space rescaling.
- **CPU reality** (this host: 4 cores, 5.7 GB, no GPU, no torch): processor `size` is the real lever (profile 400/666); local latency is **unmeasured** — the smoke tool must measure before live acceptance. `remote` is the escape hatch.
- **Acceptance/tests**: corrected command + captured output (above); hermetic default suite (synthetic + FakeVision), real-model tests opt-in via a `realvision` marker; `tools/smoke_semantics.py` sketch; `pytest.ini`.

---

## 6. Decisions for the owner

- **D1 — Vision default runnable:** MM-GDINO-T (Apache-2.0, as designed) vs YOLOE (AGPL, CPU-capable). Note the project skill reference still defaults YOLO-World — it is superseded; update or decide explicitly.
- **D2 — Plan authority:** the `§4` table vs the folded notes — the code follows the folded notes; patch the plan (or annotate) before M2 sign-off, since the plan is the review artifact.
- **D3 — Patch 10’s audit cadence:** “`--semantics-once` = mission-start pass only; otherwise audits every `audit_period_s`” is an interpretation of proposal §8 — kept in the package, flagged; one line to disable.
- **D4 — Probe scope:** recommended staging — tests + metre-based small band now (M1.5), rule change (cover-aware / shadow split) at M2, hard gate before M3 consumes the flag.
- **D5 — M2 scope:** the plan’s “routed to” wording vs the design note’s scope (adapter + manual trigger; routing/driving separate) — decide before M2 starts.
- **D6 — Adopt/cherry-pick the package:** each commit is independent; merging wholesale ≥ cherry-picking is fine, but fix 10 is the only behavioral-interpretation one.

---

## 7. Plan-doc errata (collect; owner edits)

- Header still says “no code written yet” (stale — implementation committed in `f352f61`).
- `§4` table vs folded notes vs code conflicts: ranking formula (token-overlap vs shipped Jaccard), `min_label_score` 0.35 vs 0.34, dedupe clause, vanish-removal, EMA on confidence, `t_done−t_submit` vs `t_poll−t_submit`.
- `sources` “set semantics” (plan:156) vs “insertion-ordered list” (plan:185).
- Acceptance #3 command as written cannot run (and any run needs `--no-jev` or a TypeSafe key — pre-existing).
- Stale line refs; `/tmp/opencode` paths in committed docs → should be `docs/planning/`.
- `README.md:48` “25 tests” stale (bare `pytest` collects more); counts after this package: 80 product tests. **`README.md:247` and `docs/planning/README.md:38` are correct as written** — do not “fix” them.
- “routed to” wording (M2 acceptance) — see D5.

---

## 8. Provenance, confidence, residual unknowns

- **Executed:** all 7 claim demos; the fix package (clones + fresh-clone `git am`); the acceptance run; `--trace` before/after; suite runs; state-size measurements; matcher numbers; `approach_point` degeneracy; polygon inset (= exactly 0.4 m).
- **Code-read/derived:** frame-space magnitudes (K assumed as stated), probe mechanism, threading/`close()` behavior, plan-vs-code deltas.
- **Trusted (not re-verified):** external model landscape specifics beyond spot checks (latencies are extrapolations — no GPU on this host); ST datasheet specifics for the M4 sweep track.
- **Residual unknowns:** real-camera behavior of the resolution negotiation (claim 5’s trigger); local-model latency; whether a real detector returns the label spellings the matcher expects.

---

## 9. Artifact index

- `round1-reports/00…07 + combined` — wave-1 full reports (+ `evidence/`: measurement scripts, height-math checks, landscape notes).
- `runs/20260929-wave2/w2a/` — demos 1–7 + logs + verdict table. `w2b/` — patches + rationale + transcripts + evidence. `w2c/` — M2 design note (643 lines). `w2d/` — meta-review + probes. `reviewer-addendum/` — `--trace` fix diff.
- This branch — the fix package as reviewable commits + these docs.

*Prepared by the Hermes Agent research campaign (12 subagents across two waves, all read-only), 2026-09-29. Review workspace: `~/jev-rover-research/`.*
