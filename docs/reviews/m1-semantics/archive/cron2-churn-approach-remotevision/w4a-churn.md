# w4a — Multi-pass churn of `SemanticStore.merge` (PR #1 tip, bec1d91)

**Verdicts up front**

1. **CONFIRMED — phantom-alive pinning (S4).** A recurring sub-threshold detection
   (conf 0.30 < `min_confidence` 0.5) within `match_radius_m` of a stationary object
   refreshes `last_seen_s` and resets the miss counter **every pass, forever**:
   over 100 seeds × 10 passes the object **never vanishes** (0.00 vanish events),
   is still alive at t = 540 s with `last_seen_s = 540`. Mechanism pinned to
   `semantics.py:329-331` (refresh + `_misses[oid] = 0` + `seen.add`) executing
   **before** the confidence gate at `semantics.py:332-335`.
2. **Continuous movers churn heavily** at the default radius: a per-pass
   displacement of 0.2 m (the exact stability boundary `d = α·R` = 0.2 m) already
   breaks identity ~3–4 times per 10 passes; d = 0.8/1.2 m/pass produce **one new ID
   per pass** (12 IDs per 10 passes for a single object) and lose the `moved` signal
   entirely (`moved` = 0.00 events).
3. **Recommended policy:** keep `match_radius_m = 0.5`, add carry hysteresis for
   movers (`carry_factor` 2.0, `carry_cap_m` 1.0, bootstrap carry = R) and the
   sub-threshold confidence decay (`sub_threshold_decay` 0.7). Measured effect in
   §7: mover churn at d = 0.5/pass goes 8.58 → 3.0 new IDs (0.0 vanish); phantom
   pinning goes "never vanishes" → vanish fires at pass 2 (t = 120 s). No
   measurable cost on stationary-noisy scenes.

All line numbers are from the PR tip (`review/m1-semantics-audit` = bec1d91,
scratch clone at `~/.hermes/cache/scratch/w4a/repo`). Raw aggregates:
`w4a-churn-results.json`; full tables + per-pass traces: `w4a-churn-tables.md`
(both copied next to this report).

---

## 1. Method

* Scratch clone: `git clone -q https://github.com/freakymustard67/jev-rover …/w4a/repo`
  → `git checkout -q review/m1-semantics-audit` → HEAD `bec1d91`.
  Repo and `/tmp/opencode` untouched (read-only rule).
* Sim: `w4a-churn-sim.py` (next to this report); run from the scratch-clone root with
  `PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python w4a-churn-sim.py`
  (~6 min, 100 seeds per config). It drives the **real** `SemanticStore.merge()` with
  directly constructed `SemanticObject` detections (no `FakeVision`/homography needed —
  merge consumes world-metre objects, cf. `tests/test_semantics_merge.py:16-17`).
  Config: `dataclasses.replace(SemanticsConfig(), …)`; defaults
  (`config.py:140-156`): audit cadence 60 s, `min_confidence` 0.5, `match_radius_m` 0.5,
  `ema_alpha` 0.4, `move_threshold_m` 0.25, `vanish_passes` 2, `max_misses` 10.
  Store dirs point into scratch so the repo is never written to.
* Timeline: 10 passes at t = 0, 60, …, 540 s (matching `audit_period_s = 60`,
  `max_passes_per_min = 4` — i.e. one audit per minute, no skipped passes).
* Noise: independent Gaussian per axis per object per pass, σ ∈ {0.03, 0.10} m
  (applied to detections; stationary and mover alike).
* Patched variants (F1/F2/H1/H2/HF, §6) are a line-faithful copy of the PR-tip
  `merge()` inside the sim; for every `patch="none"` config the copy's diffs and
  object states were **asserted equal to the real `merge()`** for the first 5 seeds
  (all configs passed; no divergence).
* Scenarios:
  * **S1 stationary** — 5 objects, distinct labels, ≥1 m apart.
  * **S2 mover** — "teddy" displaces d ∈ {0.2, 0.5, 0.8, 1.2} m/pass (+x from
    (0.6, 2.5)); 2 stationary others (`mat`, `box`), same noise.
  * **S3 misses** — stationary object, detection absent at pass 3 (one-pass) or
    passes 3–4 (two-pass), present otherwise.
  * **S4 phantom-alive** — pass 0: real detection (conf 0.9); passes 1…9: only a
    sub-threshold (conf 0.30) detection 0.15 m away, every pass.
  * **S5 dropout flicker** (added for the `vanish_passes` sweep) — stationary object,
    detector misses with p = 0.25/pass.
  * **S6 weak-but-real** — an object whose every detection is conf 0.40 (< 0.5).

## 2. Mechanics being measured (PR-tip cites)

* Match: same canonical label + distance ≤ `match_radius_m`, consumed globally
  nearest-first — `semantics.py:310-320` (gate at `:318`).
* On a match: `last_seen_s` refresh, `_misses = 0`, `seen.add` happen at
  `semantics.py:329-331`, **before** the sub-threshold gate `semantics.py:332-335`
  (`if d.confidence < min_confidence: continue` — "freshness only").
* Unmatched detection → new ID `obj_%04d` (`semantics.py:358-363`); only announced in
  `diff.appeared` when conf ≥ 0.5 (`semantics.py:366-367`).
* Miss accounting: `_misses += 1` per unseen pass; `diff.vanished` fires exactly once
  when `_misses == vanish_passes` (`semantics.py:375-376`); eviction at
  `_misses >= max_misses` (`semantics.py:377-385`). **Vanish is an event, not a
  removal** — the object stays in the map up to 10 missed passes (600 s).
* Mover physics of the EMA: for a constant-step mover the detection-to-estimate
  distance converges to `m = d/α` (2.5·d at α = 0.4), so the fixed match radius
  caps stable tracking at `d ≤ α·R` = 0.2 m/pass. The sim confirms this boundary
  (see §4).

## 3. Baseline results (R = 0.5 m, vp = 2; 100 seeds; means over 10 passes)

T1 — per-scenario totals (appeared / vanished / moved events, new IDs, first-vanish pass):

| scenario | appeared | vanished | moved | new IDs | 1st-vanish hist | objs alive@end | newest last_seen (s) |
|---|---|---|---|---|---|---|---|
| S1 stationary σ=0.03 | 5.00 | 0.00 | 0.00 | 5.0 | — | 5.0 | 540 |
| S1 stationary σ=0.10 | 5.03 | 0.03 | **5.17** | 5.0 | {3:2, 2:1} | 5.0 | 540 |
| S2 mover d=0.2 σ=0.03 | 3.94 | 0.77 | 6.40 | 3.9 | {6:16, 7:21, 8:19, 5:3, 9:18} | 3.9 | 540 |
| S2 mover d=0.2 σ=0.10 | 4.50 | 1.31 | 6.77 | 4.5 | {5:29, 7:24, 4:16, 3:14, 8:4, 6:7, 9:4, 2:1} | 4.5 | 540 |
| S2 mover d=0.5 σ=0.03 | 8.58 | 4.93 | 3.42 | 8.6 | {2:55, 3:45} | 8.6 | 540 |
| S2 mover d=0.5 σ=0.10 | 8.67 | 4.99 | 5.06 | 8.7 | {2:50, 3:50} | 8.7 | 540 |
| S2 mover d=0.8 σ=0.03 | 12.00 | 8.00 | **0.00** | 12.0 | {2:100} | 12.0 | 540 |
| S2 mover d=0.8 σ=0.10 | 11.91 | 7.92 | 2.02 | 11.9 | {2:100} | 11.9 | 540 |
| S2 mover d=1.2 σ=0.03 | 12.00 | 8.00 | **0.00** | 12.0 | {2:100} | 12.0 | 540 |
| S2 mover d=1.2 σ=0.10 | 12.00 | 8.00 | 2.04 | 12.0 | {2:100} | 12.0 | 540 |
| S3a one-pass miss | 1.00 | **0.00** | 0.00 | 1.0 | — | 1.0 | 540 |
| S3b two-pass miss | 1.00 | **1.00 @pass 4** | 0.00 | 1.0 | {4:100} | 1.0 | 540 |
| S4a phantom-alive σ=0.03 | 1.00 | **0.00** | 0.00 | 1.0 | — | 1.0 | **540 (pinned)** |
| S4b phantom-alive σ=0.10 | 1.00 | 0.13 | 0.00 | 1.1 | {4:2, 5:2, 3:2, 7:1, 2:1} | 1.1 | 540 |
| S6 weak-but-real conf .40 | **0.00** | **0.00** | 0.00 | 1.0 | — | 1.0 | **540 (pinned)** |

Reading:

* **Stationary σ=0.03: zero churn.** 5 IDs, no vanish, no moved — quiet.
* **Stationary σ=0.10 is not free**: 5.17 spurious `moved` events per 10 passes over
  5 objects (~11% of object-passes exceed `move_threshold_m` 0.25 from noise alone;
  the raw displacement is the detection-to-EMA-ghost distance, σ_rel ≈ 1.12σ ≈ 0.11 m,
  so 0.25 m is only ~2.3σ). Rare real churn too: 3/100 seeds had a vanish
  (`new IDs mean 5.03`).
* **Movers**: churn grows monotonically with step size; at d ≥ 0.8 m/pass the mover
  is a **new ID every pass** and `moved` never fires (0.00 at σ=0.03) — the layer sees
  "vanish + appear", not "moved". At run end the map contains 12 objects for 3
  physical ones (alive@end 12.0) — churn also bloats the map with ghost IDs that
  linger until eviction (max_misses 10).
* **S3 timing** (vp = 2): a 1-pass miss is fully absorbed (no vanish, same ID, `last_seen`
  keeps advancing after the return); a 2-pass miss fires `vanished` exactly once, on
  the **second consecutive missed pass** (pass 4, t = 240 s), and the **return is
  silent**: the same ID re-matches at pass 5 with **no `appeared` event**
  (S3b `silent_resurrections` = 1.00; trace in `w4a-churn-tables.md`, pass 5 shows
  `ls300` and empty diff). Consumers that watch `diff.appeared` miss the return.
* **Sub-threshold**: S6 shows the layer can hold table entries that never once passed
  `min_confidence` (conf 0.40, `appeared` = 0.00, still alive and (baseline) refreshed
  at t = 540).

## 4. Mover behaviour in detail (S2, R = 0.5)

Per-pass means (passes 1–9; appeared/vanished/moved), σ = 0.03:

| step d | new IDs (mean / max) | IDs on mover | vanished | per-pass A/V/M |
|---|---|---|---|---|
| 0.2 | 3.9 / 5 | 1.9 | 0.8 | 0/0/0.2 … 0/0/1.0 … 0.2/0.2/0.6 (churn starts ~pass 5) |
| 0.5 | 8.6 / 10 | 6.6 | 4.9 | 0.6/0/0.5 then ≈0.6/0.6/0.4 every pass |
| 0.8 | 12.0 / 12 | 10.0 | 8.0 | 1.0/0/0 then 1.0/1.0/0 every pass |
| 1.2 | 12.0 / 12 | 10.0 | 8.0 | same: 1 new ID per pass |

Concrete trace (d = 0.5, seed 0, `w4a-churn-tables.md:116-129`): `obj_0001` (teddy)
tracks passes 0–1, dies at pass 3; `obj_0004, 6, 7, 8, 9` each live 2–3 passes; by
pass 9 the map holds 9 objects, 7 of them teddy ghosts vs 1 true position. The
churn cycle length at d = 0.5 is `⌈R/d⌉ ≈ 2` passes per ID — matching
`vanish_passes = 2` plus the match geometry (`m = d/α = 1.25 m > R`).

**At what displacement does the mover churn?**
* d ≤ 0.2 m/pass (= α·R, exactly): identity mostly holds but the margin is zero —
  with noise, first churn events cluster mid-run (hist passes 5–9). Already ~2 IDs
  lost per 10 passes on the mover.
* d = 0.5 m/pass: structure broken — a fresh ID every ~2 passes, one vanish per ~2
  passes, map bloat 8.6 objects for 3.
* d > 0.5 m/pass (0.8, 1.2): **one new ID per pass**, vanish per pass after pass 2,
  `moved` signal gone.
* The `moved` flag can only ever be seen for `raw ≤ match_radius_m` (0.5 m/pass):
  beyond that the detection can't be matched at all.

## 5. Sweeps

**match_radius sweep** (100 seeds; "newIDs" = total IDs created over the 10 passes;
"van" = vanish events):

| radius | S1 σ=0.10 | S2 d=0.2 | S2 d=0.5 | S2 d=0.8 | S2 d=1.2 | S4a phantom |
|---|---|---|---|---|---|---|
| R=0.3 | newIDs 6.6, van 2.1 | 6.6 / 3.2 | 12.0 / 8.0 | 12.0 / 8.0 | 12.0 / 8.0 | alive@end 1.0, van 0.0 |
| R=0.5 | 5.0 / 0.0 | 3.9 / 0.8 | 8.6 / 4.9 | 12.0 / 8.0 | 12.0 / 8.0 | alive@end 1.0, van 0.0 |
| R=0.75 | 5.0 / 0.0 | **3.0 / 0.0** | 6.9 / 3.7 | 11.0 / 7.1 | 12.0 / 8.0 | alive@end 1.0, van 0.0 |
| R=1.0 | 5.0 / 0.0 | 3.0 / 0.0 | 5.0 / 2.0 | 7.0 / 4.0 | 12.0 / 8.0 | alive@end 1.0, van 0.0 |

* **R = 0.3 is too tight**: σ = 0.10 noise alone causes churn (6.6 IDs, 2.1 vanish
  events, 0.98 silent resurrections per run) — the radius must stay well above the
  anchor noise floor.
* Bigger radius **monotonically reduces** churn but **cannot eliminate it**: full
  stability needs `R ≥ d/α` (d = 0.5 needs R ≥ 1.25 m; d = 0.8 needs ≥ 2.0 m) —
  i.e. radius inflation only buys the range `d ≤ α·R` (0.2 → 0.4 m/pass across the
  sweep), and it widens the window in which a same-label detection is absorbed
  across distance (not measured here; that cost is outside this sim).
* **Radius does nothing for the phantom** (S4a identical at every radius — the ghost
  is always inside radius). Pinning must be fixed in code, not config.

**vanish_passes sweep** (flicker; mean events per 10-pass run):

| vp | S3a 1-pass miss | S3b 2-pass miss | S5 dropout p=.25 | S5 silent resurrections |
|---|---|---|---|---|
| 1 | vanished @pass 3 | vanished @pass 3 | **1.56** | 0.86 |
| 2 | none | vanished @pass 4 | 0.36 | 0.25 |
| 3 | none | **none** (2 misses < 3) | 0.10 | 0.08 |

* vp = 1 turns every detector dropout into a vanish+silent-return flap in the event
  log (1.56 events per 10 passes at 25% dropout, 0.86 of them silent returns).
* vp = 3 suppresses one real signal class (a 2-pass absence is never reported) and
  delays detection of real removal by 60 s. **Keep vp = 2** as shipped: it absorbs
  single misses, reports sustained absence, and the residual flicker (0.36/run at
  25% per-pass dropout) is inherently noisy-input territory.

## 6. Candidate fixes measured

* **F1** — sub-threshold matched hits do not refresh `last_seen_s`/`_misses`/`seen`
  (the "refresh only when `d.confidence >= min_confidence`" variant).
* **F2** — on a sub-threshold hit, `prev.confidence = round(prev.confidence * 0.7, 3)`;
  if the decayed value drops below `min_confidence`, the hit stops counting as seen.
* **H1** — mover carry hysteresis: effective match radius per object =
  `min(max(R, 2·last_raw_step), R + 1.0)`, fresh objects carry 0.
* **H2** — H1 but a fresh object starts with carry = R (first hand-off up to 2R).
* **HF** — H2 + F2 (the recommended combination).

S4a phantom / S6 weak-but-real (100 seeds):

| scenario | policy | vanished | 1st-vanish | alive@end | newest last_seen |
|---|---|---|---|---|---|
| S4a | baseline | 0.00 | — | 1.0 | 540 |
| S4a | F1 | 1.00 | pass 2 | 1.0 | 0 |
| S4a | F2 | 1.00 | pass 3 | 1.0 | 60 |
| S4a | HF | 1.00 | pass 2 | 1.0 | 0 |
| S6 | baseline | 0.00 | — | 1.0 | 540 |
| S6 | F2 | 1.00 | pass 2 | 1.0 | 0 |
| S6 | HF | 1.00 | pass 2 | 1.0 | 0 |

Movers (S2, σ = 0.03): newIDs mean (max), IDs on mover:

| d | baseline | H1 | H2 | HF |
|---|---|---|---|---|
| 0.2 | 3.9 (5) | 3.0 (3) | 3.0 (3) | 3.0 (4) |
| 0.5 | 8.6 (10) | 3.9 (7) | **3.0 (3)** | **3.0 (4)** |
| 0.8 | 12.0 (12) | 12.0 (12) | **5.9 (6)** | 5.9 (6) |
| 1.2 | 12.0 (12) | 12.0 (12) | 12.0 (12) | 12.0 (12) |

At σ = 0.10, H2/HF: d=0.2 → 3.4/3.5 IDs; d=0.5 → 3.4/3.5; d=0.8 → 5.8; d=1.2 →
11.2 (H2) — mostly residual noise edges, not systematic churn.

Side effects on S1 (σ = 0.10): appeared 5.03 / vanished 0.03 / moved 5.17 (baseline)
vs 5.02 / 0.04 / 4.74 (HF) — statistically unchanged; H1/H2 add no noise churn.
S3a under HF: unchanged (1 ID, no vanish).

Interpretation: H1 (carry 0 bootstrap) fixes movers up to d = R exactly; H2 extends
full stability to `d ≤ min(2R, α(R+cap))` = 0.6 m/pass and partial recovery at 0.8
(≈4 IDs total on the mover: initial detach + ~3 re-births over 10 passes). d = 1.2 m/pass stays broken (beyond
2R = 1.0 bootstrap hand-off) — inherent: without prediction beyond the configured
radius, identity of a ≥1.2 m/pass mover at 60 s cadence is unrecoverable.
F1/F2 remove the pinning; F2 preserves more signal (`last_seen` = 60 rather than 0;
confidence decays monotonically so downstream ranking/staleness can see it).

## 7. Recommendation

**Keep `match_radius_m = 0.5` m.** R = 0.3 is measurably unsafe (noise churn);
R ≥ 0.75 buys mover relief that hysteresis buys better (R = 1.0 still gives 5 IDs at
d = 0.5 vs 3.0 under H2), while a bigger radius widens same-label mis-association —
the one cost this sim does not price in.

**Adopt HF (carry hysteresis + sub-threshold decay).** Projected effect (sim-measured):

| scenario | baseline newIDs / vanish | with HF |
|---|---|---|
| S1 σ=0.03 / 0.10 | 5.0 / 0.0  ·  5.03 / 0.03 | 5.00 / 0.00  ·  5.02 / 0.04 |
| S2 d=0.2 | 3.9 / 0.77 | 3.0 / 0.0 |
| S2 d=0.5 | 8.6 / 4.9 | 3.0 / 0.0 (max 4) |
| S2 d=0.8 | 12.0 / 8.0 | 5.9 / 2.0 |
| S2 d=1.2 | 12.0 / 8.0 | 12.0 / 8.0 (unchanged, honest limit) |
| S4a phantom / S6 weak | never vanishes | vanish @pass 2, `last_seen` stops refreshing |
| S3a 1-pass miss | no vanish | no vanish (unchanged) |

### Patch — `config.py` (new keys, defaults chosen from the measurements)

```diff
@@ SemanticsConfig (config.py:135-156)
     match_radius_m: float = 0.5
     ema_alpha: float = 0.4
     move_threshold_m: float = 0.25
     vanish_passes: int = 2
     max_misses: int = 10
+    carry_factor: float = 2.0          # mover hysteresis: eff radius = max(R, factor*last raw step)
+    carry_cap_m: float = 1.0           # ... capped at match_radius_m + carry_cap_m
+    sub_threshold_decay: float = 0.7   # confidence decay per sub-threshold-only pass
```

### Patch — `semantics.py` (`SemanticStore.merge`, PR-tip line context)

```diff
@@ __init__ (after `self._misses: dict[str, int] = {}`, semantics.py:289)
+        self._step: dict[str, float] = {}   # last raw displacement per object (mover carry)

@@ pairs build (semantics.py:312-319)
     for oid, prev in self.objs.items():
         prev_label = canonical_label(prev.label)
+        carry = self._step.get(oid, self.cfg.match_radius_m)      # bootstrap: fresh = R
+        radius = min(max(self.cfg.match_radius_m, self.cfg.carry_factor * carry),
+                     self.cfg.match_radius_m + self.cfg.carry_cap_m)
         for i, d in enumerate(unmatched):
             if det_labels[i] != prev_label:
                 continue
             dd = math.hypot(d.x - prev.x, d.y - prev.y)
-            if dd <= self.cfg.match_radius_m:
+            if dd <= radius:
                 pairs.append((dd, oid, i))

@@ consume loop (semantics.py:326-335)
         d = unmatched[i]
         taken.add(i)
         prev = self.objs[oid]
-        prev.last_seen_s = round(t_pass, 2)
-        self._misses[oid] = 0
-        seen.add(oid)
         if d.confidence < self.cfg.min_confidence:
-            # Freshness only: a sub-threshold hit keeps the object alive but
-            # must not move it or overwrite its flags.
+            # Sub-threshold sighting: decay confidence first. While the object
+            # is still nominally confirmed this is freshness-only; once the
+            # decayed confidence falls below min_confidence the sighting stops
+            # counting as seen (falls through to miss accounting), so a phantom
+            # that only ever fires weak hits cannot pin an object alive.
+            prev.confidence = round(prev.confidence * self.cfg.sub_threshold_decay, 3)
+            if prev.confidence < self.cfg.min_confidence:
+                continue
+        prev.last_seen_s = round(t_pass, 2)
+        self._misses[oid] = 0
+        seen.add(oid)
+        if d.confidence < self.cfg.min_confidence:
             continue

@@ raw displacement (semantics.py:351-352)
         raw = math.hypot(d.x - prev_x, d.y - prev_y)
+        self._step[oid] = raw                # arms the mover carry for the next pass
         prev.motion = "moved" if raw > self.cfg.move_threshold_m else "static"

@@ new-object creation (semantics.py:363-365)
         self.objs[oid] = obj
         self._misses[oid] = 0
+        self._step[oid] = self.cfg.match_radius_m     # bootstrap: first hand-off <= 2R
         self._labels[oid] = obj.label

@@ eviction (semantics.py:379-385)
         del self.objs[oid]
         self._misses.pop(oid, None)
         self._labels.pop(oid, None)
         self._hs_hist.pop(oid, None)
+        self._step.pop(oid, None)
```

Notes on the patch:

* `taken.add(i)` stays before the faded `continue`, so a fading phantom's detection is
  consumed and cannot spawn a duplicate object.
* Full mover stability holds for `d ≤ min(2R, α(R + carry_cap))` = 0.6 m/pass at
  defaults; partial (fewer re-births) up to ~1.0. Raise `carry_cap_m` to trade
  fast-mover continuity against wider carry windows.
* F2 (decay) is preferred over F1 (pure freshness gate) because it keeps the
  `confidence` field meaningful as a freshness estimate (helps destination
  tie-breaking at `semantics.py:723` and staleness consumers), at the cost of one
  extra pass of vanish latency for a strong object (pass 3 vs pass 2).

### Test updates that follow from the patch

* `tests/test_semantics_merge.py:106-116` (`test_sub_threshold_hits_only_refresh_freshness`):
  line 112 `o.confidence == 0.9` → `== round(0.9 * 0.7, 3)` (= 0.63) and line 113-115
  (still refreshes `last_seen_s` at pass 1; still no vanish at pass 2) remain valid —
  the test's *intent* (weak hits don't move the object or overwrite flags) is preserved.
* New test to add: "a phantom that only ever fires sub-threshold hits vanishes":
  pass 0 conf 0.9, passes 1-3 conf 0.3 → `diff.vanished` fires on the 2nd faded pass
  (pass 3 with decay 0.7 from 0.9; pass 2 from 0.4).
* Optional follow-up (not required for the fix): emit an `appeared`/resurrect event
  when an already-vanished ID re-matches (measured silent resurrection rate: 100% of
  S3b runs; 0.86/run at S5 vp=1) — today the event log has no positive signal for
  the return (semantics.py:358-368 only announces *new* IDs).

### What we deliberately did NOT recommend

* Raising `match_radius_m` to "fix" movers: measured ineffective beyond `α·R`
  (R = 1.0 still churns d = 0.5) and untouched by pinning.
* Lowering `vanish_passes` to 1: turns every dropout into a vanish flap (1.56/run vs
  0.36). Raising to 3 suppresses 2-pass absence entirely — keep 2.
* Master comparison (optional in the brief): not simulated. By code reading, master's
  merge (`master:semantics.py:284-287`) lacks the sub-threshold gate entirely, so a
  recurring sub-threshold hit **also refreshes `last_seen_s` and additionally drags
  the stored position/confidence toward the phantom** — i.e. master is equal or worse
  on pinning; the PR-tip fixes (#2, #4 in `docs/reviews/m1-semantics/patches.md:63-77,
  :95-99`) neither removed nor bounded it. This is exactly the residual quantified in §3-4.

## 8. Caveats (sim-dependent)

* Noise is homogeneous Gaussian in world metres per axis per pass, independent across
  objects; real detector noise lives in pixels and is homography-scaled (larger at
  range on an oblique floor camera), and detector *recall* is only modeled in S5
  (p = 0.25 dropout). Numbers for σ = 0.10 hybridize position jitter with dropout-like
  effects only via S5.
* Scenarios use distinct labels except where stated; same-label proximity effects
  (the cost of a large radius) are not exercised here beyond the existing unit test
  (`tests/test_semantics_merge.py:62-87`).
* `merge()` alone is driven: no `project_detections`, no `height_suspect` probing, no
  homography, no skipped passes (budget/inflight/cooldown can only reduce the number
  of passes per minute, not change per-pass semantics).
* 100 seeds per config; fixed scenario geometry (5 objects in S1, single mover in S2).
  Counts are means; max values reported where they matter (new IDs).
* The patched variants re-implement `merge()` (asserted identical to the real one for
  `patch="none"`); F1/F2/H1/H2/HF numbers therefore inherit that parity but are sim
  measurements, not production data.
* The simulated phantom is stationary and always inside radius; a phantom that
  wanders outside the radius spawns its own (invisible-in-`appeared`) low-confidence
  map entry instead — a neighbouring behaviour observed at S4b σ = 0.10 (0.13 vanish
  events mean, 8/100 runs) and worth its own follow-up test if undesirable.

## 9. Artifacts

| file | content |
|---|---|
| `w4a-churn.md` | this report |
| `w4a-churn-sim.py` | the simulator (run from the scratch-clone root; writes to scratch) |
| `w4a-churn-tables.md` | all sim tables incl. per-pass traces (S2 d=0.5, S2 d=0.5+HF, S4a, S4a+F2, S3b) |
| `w4a-churn-results.json` | raw aggregates (per-config metrics, histograms, per-pass means) |
| scratch `~/.hermes/cache/scratch/w4a/simstore/` | merge() event logs / latest maps written by the sim |