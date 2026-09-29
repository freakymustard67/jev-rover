All checks done. Summary follows.

---

## 1. Local vs GitHub — coherent
- Local HEAD = `9c33ec06afcc644c23deef76447b4a2370de31c1`, "docs: planning package (semantics proposal, M1 plan, sweep validation, sim + prototype)", 2026-09-28 23:46:24 +0530 (18:16Z).
- `git ls-remote origin` HEAD and `refs/heads/master` = same SHA; GitHub API latest commit = same SHA. **No divergence.**
- Branch `master` tracks `origin/master`, up to date; `git status` clean, no untracked/stashed files. Working tree = commit.
- `gh auth status`: logged in as `freakymustard67` (repo scope). `gh repo view`: `jev-rover`, `master`, **public**. No writes performed.

## 2. CI — none exists
- No `.github/`, no `.gitlab-ci.yml`, `.circleci`, `.travis.yml`. **A PR would trigger zero automated checks.**
- Local suite: `./setup.sh` (venv + requirements + pytest), then `.venv/bin/python -m pytest` (`pytest tests/ -q` per docs). I ran `--collect-only`: **78 tests** collected.
- Doc drift: README.md:48 says "25 tests"; README.md:247 and docs/planning/README.md:38 say "67 tests". Actual = 78. Stale counts — worth fixing before a review PR.

## 3. Cited "lessons" — verified, with one attribution nuance
- **README lesson #2 exists but doesn't say what the plan paraphrase implies.** README.md:216–218 (under "## What the build actually taught (worth keeping)", 209): *"Publish derived state every frame, not only when it changes. The planner replans at 0.75 s intervals; scenes are rebuilt at 15 Hz. Only writing `path_*` on replan frames made 80% of states lie."* README never mentions `age_s`, merge, or semantics.
- The C5 text is m1-plan.md:97–99: *"Also an instance of README lesson #2: `age_s` must be recomputed on every scene, not frozen at merge time. `SemanticsRunner.snapshot(t)` returns a `dataclasses.replace` copy with fresh `age_s` each frame."* Framed as *"an instance of"* — acceptable as application, **but the phrase "README lesson #2: age_s must be recomputed…" is plan text, not README text**. Don't quote it as if README says it. Implementation is real: semantics.py:322/326 (`snapshot`/fresh `age_s`), 547–548; asserted by tests/test_semantics_integration.py:50.
- **Tactician.offer pattern verified.** tactics.py:272–273: *"def offer(self, scene: Scene, now: float) / Non-blocking. Hand the latest scene over if it is worth a call."*; tactics.py:285–286: *"Build the state NOW, in the caller thread. The worker gets an immutable snapshot instead of racing the control loop's mutations."* M1 plan C4 (m1-plan.md:63): *"mirroring the `Tactician.offer` rule we already learned the hard way"*. The "hard way" provenance is README lesson #1 (211–215, offer-before-planned → `path_valid` always false) — consistent, though the literal phrase "learned the hard way" appears in README only at line 77 (calibration rules). semantics.py:13 states it "mirrors `tactics.py`"; SemanticsWorker.offer at semantics.py:430. No misattribution beyond the paraphrase caveat.

## 4. Existing semantics/sweep build state
- **Semantics: built** (M1 shipped — README.md:276–277, docs/planning/README.md:21). `semantics.py` (691 lines), config sections, 4 test files, `prototype/` + `tools/sim/tof_sim.py` tracked.
- **Sweep: schema/sim only.** `SweepState` in scene.py, `config.sweep`, test in test_semantics_schema.py:29; simulator exists; no `sweep.py` — M4 planned (docs/planning/README.md:24).

## 5. Other reviewer notes
- **Doc conflicts already documented, not hidden:** m1-plan.md §1 C1–C8 records proposal-vs-code conflicts (C1 height_suspect rule, C2 clamp-vs-reject contradiction) with owner approvals at lines 387–392. No conflicting *documents*; the plan and proposal are reconciled.
- **Dangling `/tmp` paths in committed docs:** m1-plan.md:4 ("Responds to `/tmp/opencode/semantics-layer-proposal.md`") and semantics.py:18 ("prototype: /tmp/opencode/semantics_proto/") — should point at the repo copies (`docs/planning/`).
- README.md is 278 lines, not 225 as task context stated.
- Docs claim "no network" for tests — consistent with test layout. `.gitignore` covers `.venv/`, `.env`, `runs/`, `calibration/camera.json`, `firmware/.../secrets.h` — no secrets risk in visible tree.

**Bottom line:** repo is clean and fully synced with GitHub; review PR would be green-by-default (no CI at all — the main gap), and the two plan citations are real with one paraphrase caution.