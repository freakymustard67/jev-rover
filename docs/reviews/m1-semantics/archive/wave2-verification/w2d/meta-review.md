# Wave-2 meta-review (w2d) — adversarial audit of the eight wave-1 reports

Repo: `~/jev-rover` @ `9c33ec0` (read-only; suite re-run 78 passed, 13.92 s; `git status` clean before/after).
Method: all eight reports read; **~19 claims independently re-verified** against code/plan/docs by reading and by executing probes (`spotcheck.py`, `height_check.py`, re-run of `tof_sim.py`, `pytest --collect-only`, the plan's own acceptance commands). Re-verified vs trusted is listed in §0.

---

## 0. What I re-verified vs trusted

**Re-verified (executed or code-read):**
1. **Test composition** (06): `tests/` = 67 exactly (25 legacy + 42 semantics) + 11 from `docs/planning/prototype/test_april_sem.py` = 78 collected; suite green. Per-file counts: schema 9, projection 8, merge 9, destination 5, worker 7, integration 4.
2. **Acceptance #3 command fails as written** (03): executed → `SystemExit("--mission goto needs --waypoint NAME")`. With `--mission patrol` it works: `blue mat (3.00,1.20) conf 0.92 → approach (3.12,1.53)`.
3. **Frame-space ambiguity** (01/02): `run.py:294` hands the raw frame to `maybe_pass`; `process()` rebinds a local after `cv2.undistort` (perception.py:733-734); `process()` returns a `Scene`, so run.py cannot offer the used frame today. Confirmed.
4. **C1 probe rule + mat mechanism** (02): probe single pixel at `(cx, y1+probe_px)` (semantics.py:191-203); `floor_lab` = whole-polygon median incl. covers (perception.py:269-273, adapt 286-290); synthetic renders **no mat** (`FURNITURE_LAYOUT` = sofa/table/box/shelf). Mechanism confirmed by reading; end-to-end "confirmed" not executed.
5. **Merge flaws** (02): greedy insertion-order; never evicts; `confidence` raw + `height_suspect` overwritten; no `min_confidence` on matching (only gates `appeared`); labels compared case-sensitively while adapters filter case-insensitively; dedupe is IoU-only (plan's `<0.15 m` clause dropped). All confirmed.
6. **Gating bypass** (03): `_ask_jev_label` is synchronous, unbudgeted, uncached; `run.py:299` passes `jev=None` (test-only path). Confirmed.
7. **`--semantics off` doesn't override `semantics.enabled`** (00-R2): `run.py:242` `if args.semantics == "fake" or args.find or cfg.semantics.enabled`. Latent only: both shipped configs ship `enabled:false`.
8. **Matcher numbers** (03): `bottle` vs `plastic water bottle` = 0.333 < 0.34 → dropped; `mat` vs `yoga mat extra large` = 0.25 → dropped; `chargr`/`waterbottle`/`boxes` → no candidates; fold turns `boxes`→`boxe`. Executed.
9. **approach_point degeneracy** (03): from (3.2,1.0) to dest (3.0,1.0) standoff 0.35 → (3.35,1.0), beyond the object. Executed.
10. **Polygon inset** (02): 120 px = **exactly 0.4 m** wall clearance in `room.example.json` (world 0.4..6.0 / 0.4..3.2). Executed.
11. **Plan staleness** (00/03): header "no code written yet"; §4 table vs folded notes vs code on ranking formula, `min_label_score` 0.35 vs 0.34, dedupe clause, vanish-removal, EMA on confidence, `t_done−t_submit` vs `t_poll−t_submit`; `sources` "set semantics" (plan:156) vs "insertion-ordered list" (plan:185). Confirmed.
12. **Height identity** (05): `h = Zc·Δ/(d+Δ)` re-derived and simulated — **max error 0.00 cm** across 3 tilts × 4 positions × 3 heights. Flat-mat bias real: 0.46–0.69 m phantom height for a 0.4 m-deep mat at d=1 m (their 0.93 m needs d≈0.4 m). Direction confirmed, exact figure not reproducible without their camera params.
13. **tof_sim reproduces** (05): `sanity: 1.224 m (expect ~3.25)` printed exactly; matcher table consistent.
14. **README lesson #2** (07 nuance vs 00): README lesson #2 = "Publish derived state every frame"; README never says `age_s`; plan line 97 frames it as "an instance of" — legitimate application. Confirmed 07 right, 00's label overstated.
15. **Main-thread I/O + copy costs** (01): `store.merge` (serialize+`os.replace`+`events.jsonl`) runs inside `runner.poll`; `semantic_context()` re-measured 73,784 B / ~10.6 µs; `frame.copy()` 6,220,800 B, medians 0.23–0.43 ms. Confirmed (their "~0.55 ms" was a slower run; same order).
16. **No CI; repo synced** (07): no `.github/`; local HEAD == `origin/master` == 9c33ec0.
17. **Control path untouched** (safety): no `semantic` refs in control/reflex; `tactics.py` absent from `f352f61`; `build_state` pops only `mission`.
18. **Cooldown uncounted** (01/06): `semantics.py:511-512` returns False with no counter; `stats()` has no cooldown key.
19. **Loop-rate test absent** (00/06): no fps/invariant test; replacement = submit `<0.05 s` (worker:74, integration:96) + poll `<0.2 s` (integration:101).

**Trusted (not re-verified):** report 04's landscape beyond spot checks (HF API confirmed `grounding-dino-tiny` live + apache-2.0; `facebook/sam3` license "other"; latency figures are extrapolations — no GPU on this box); report 05's ST datasheet specifics and matcher timings (88/608 ms); report 06's per-test timing details; report 07's `gh` auth state; report 02's oblique-camera pixel scales; report 00's "no Scene deserializer" (grep confirmed empty).

---

## 1. Claims that appear overstated or wrong

- **A1 (wrong — report 07).** "README.md:247 and docs/planning/README.md:38 say 67 tests … Actual = 78. Stale counts." Wrong for those two: `tests/` contains **exactly 67** (verified per-file), and `docs/planning/README.md:38`'s command is `pytest tests/ -q` → 67 is correct; README:247 describes the `tests/` dir → also correct. Only **README.md:48** ("25 tests", bare `pytest` → 78) is stale. Reports 00 and 06 got this right; 07 conflated "bare pytest collects 78" with "the docs' 67 is wrong".
- **A2 (overstated — report 00).** "README lesson #2 — MISMATCHED." The plan's sentence is a valid application of README lesson #2 ("publish derived state every frame"); the true part is only that README never mentions `age_s`. Report 07's nuance is the correct read.
- **A3 (nitpick — report 00 claim 8).** "OccupancyGrid `occupied_thr`/`stale_s` — PARTIAL MISMATCH." Plan C4 itself lists them as `SemanticContext` fields (plan:77); code matches exactly (perception.py:157-161, sourced 720-721). No mismatch with pinned C4 — at most a "where the constant lives" nuance.
- **A4 (labelling — report 02).** "Blue mat (false positive **confirmed**)" is mechanism-derived, not executed: the renderer has no mat and no test covers an object-on-mat. Same for "rug >50% inverts classification" (mechanism correct, unexecuted). And their flat-mat `h_est = 0.93 m` is setup-dependent (0.46–0.69 m for plausible setups; 0.93 m needs d≈0.4 m).
- **A5 (label-dependence — report 03).** "'find the water bottle' → water bottle 1.0" holds only for a fixture labelled exactly "water bottle"; with "plastic water bottle" it is 0.667. Their knife-edge finding (0.333 < 0.34) is the load-bearing part and it is correct.
- **A6 (new — unreported by all eight).** `run.py:339` `print(trace_line)` — **NameError; `--trace` crashes on the first frame** (verified by running it). Pre-existing since c93e25c, one-line fix. Implication: nobody has exercised `--trace` since M1; the wave-1 reports audited run.py without executing it.
- **A7 (minor — report 00).** The `--find` "prints + sets nothing" divergence is real but low-stakes: `run.py:303` sets the destination into the persisted map (no driving), while the plan's acceptance text says print-only. Report's framing is fair; severity should be "doc/acceptance drift", not a defect.

## 2. Contradictions between reports

- **B1. Test counts:** 07 ("67 stale") vs 00/06 ("67 consistent with 25+42"). Resolution: 07 wrong for README:247 and docs/planning/README.md:38; README:48 stale (see A1).
- **B2. Cadence wording:** 01 "configured 4 passes/min" (`max_passes_per_min=4`) vs 02 "audit passes are 60 s" (`audit_period_s=60`). Both knobs exist; M1 runs neither (one forced pass at t≥1.0). Not a real conflict once disambiguated.
- **B3. Vision default:** 04 recommends MM-GDINO-T #1 / YOLOE #2 and calls YOLO-World superseded; the project skill reference (`semantics-layer.md`, proposal-era) still says YOLO-World default / GDINO-tiny "when phrase grounding matters". 04 is newer and primary-source-verified; the reference doc needs an owner decision + update. Also note 04's license flip: YOLOE is AGPL-3.0, MM-GDINO Apache-2.0.
- **B4. Probe urgency:** 02 says "fix the untested case first"; 05 treats the C1 probe as adequate for the on-table case and proposes height-from-H as a later second signal. Not a direct contradiction; it is a scope question (see Q2).
- **B5. Loop-rate wording:** 00 "no loop-rate invariant test" vs 06 "replaced by submit/poll-cheap tests" — both true (different files); no conflict.

## 3. Top open questions

- **Q1. Plan §4 table vs "folded notes": which is authoritative?** Code follows the folded notes; the §4 table contradicts itself (0.35 vs 0.34) and the shipped code on 5+ rules. Patch the plan (rewrite table or annotate) before M2 sign-off — the plan is the review artifact.
- **Q2. Does the mat-probe issue need a fix before M2?** Nothing consumes `height_suspect` in M1 or M2 (informational; `destination_trustworthy` is M3). Cheap now: render a mat (+ shadow) in `synthetic.py`, add failing-first tests, switch probe to a small band in **metres** via `H`. Rule change (cover-aware / shadow-split) is a design decision; recommend: tests + band in M1.5, rule decision at M2, hard gate before M3 consumes the flag.
- **Q3. Frame-space contract (raw vs undistorted) + resolution guard.** M2-blocking whenever `camera.intrinsics` is set (which `calibrate.py` instructs). Pin: undistort once, pass one frame to process/worker/renderer; assert `frame.shape == (camera.height, camera.width)` in `process()`/`semantic_context`.
- **Q4. CLI semantics drift:** acceptance #3 unrunnable as written; `--semantics-once` inert; `--semantics off` doesn't override enabled configs; `--find` persists a destination contrary to print-only text. Decide: fix flags (tri-state; wire/remove once; benign mission default) vs fix docs — at minimum docs, ideally both before M2's smoke tool.
- **Q5. Persistence/privacy:** `runs/semantic/{<room>_latest.json,events.jsonl}` persist labels+positions; gitignored; no auto-load (good). M2 must confirm what leaves the machine if `RemoteVision` is used (endpoint/token via env) and keep to bboxes/labels, never frames, into Jev. Add a one-line privacy note when M2 wires RemoteVision.
- **Q6. Safety-relevant path:** verified untouched (no semantics refs in control/reflex; tactics.py untouched; only new main-thread work is the frame copy ~0.2–0.6 ms + merge disk I/O <6 KB in `poll`). Non-blocking gaps: `close()` join 1.0 s < M2 pass times; merge/persist on the control loop if the store grows.
- **Q7. Jev budget plumbing for `_ask_jev_label` (M3):** share Tactician budget vs runner budget; must run on the worker, cache by `(text, sorted labels)`; decide now so M2 doesn't accidentally wire the synchronous path.
- **Q8. Eviction/resurrection:** unbounded map growth + Jev-state token cost (C5 bound is test-only). Cap before triggers multiply passes (M3).
- **Q9. Vision model + colour binding + license (M2 owner decision):** MM-GDINO-T (Apache) default vs YOLOE (AGPL, CPU-capable); colour must be verified from crops (both models silently ignore colour words).
- **Q10. No CI:** a pytest job on `tests/` is cheap; would have caught the `--trace` NameError and doc drift.

## 4. Recommended fix order (M1.5) and re-scoping

**M1.5 — land before M2 real-camera work:**
1. **Frame-space contract + resolution assert** (Q3) — M2 blocker.
2. **CLI/acceptance fixes + doc patch** (Q4, Q1, A1/A2): plan header/§4/§6/acceptance text, stale line refs, `/tmp` paths → `docs/planning/`, README counts; `--find` mission default; `--semantics-once` wire-or-drop; `--semantics off` tri-state.
3. **Missing acceptance tests** (00/06): loop-rate invariant (absolute-cadence formulation), zero-worker-thread check, `skipped["cooldown"]` counter + summary assertion, CLI `--find` smoke test; plus the `--trace` one-liner (A6).
4. **Matcher fix** (03 option 1): asymmetric overlap `0.7·|q∩w|/|q| + 0.3·|q∩w|/|w|` + `-es/-ies` fold + difflib fallback; retune thresholds; correct plan §6 test description.
5. **Store hygiene** (02): `min_confidence` gate on position/flags, distance-ordered (or Hungarian) assignment, eviction/resurrection cap, label canonicalisation, `height_suspect` hysteresis. (Before M3 triggers multiply passes; can trail M2 slightly if needed.)

**Before M3:** Jev budget plumbing for `_ask_jev_label`; state compaction; probe rule decision if not done; height-from-H decision (needs intrinsics/pose extension).
**M4:** sweep spec rulings (status/intensity payload; "background/dropout ⇒ inconclusive, never contradicted" for bottle-class; ROI shrink; beam-model validation beyond the matcher sim).
**Anytime:** CI; merge off the control loop; `close()` timeout parametrize.

**Re-scope:** M2 (adapter + manual trigger) can proceed after items 1–2; items 3–5 are cheap and should not slip past M2 exit. Nothing found here blocks M2's fixtures-first path; the hard gate is the frame-space contract before *live* camera use.

## 5. Verdict

The M1 plan+implementation is **good**. The implementation is faithful to its pinned decisions, the concurrency discipline is sound (single in-flight, immutable snapshot, stale-at-poll), the suite is green (78), the control/reflex path is genuinely untouched, and no report found a fabricated result — the wave-1 reports are largely accurate, with a handful of overstated labels (A1–A4). The real weaknesses are (i) a plan doc that has drifted from the code it pins (§4 table vs folded notes; unrunnable acceptance #3; "no code written yet"; counts), (ii) three of five acceptance items only partially met, (iii) a known false-positive class in the C1 probe for the headline object (mat) that is latent because nothing consumes `height_suspect` yet, and (iv) the unexamined frame-space contract that will bite M2. Fix-forward with the M1.5 list; grade: implementation **A−**, plan doc **C+**, overall **good — proceed with M1.5, not a rework**.

Artifacts: `spotcheck.py`, `height_check.py`, this file (all under `wave2/w2d/`). No repo or `/tmp/opencode` writes; repo clean after all probes.
