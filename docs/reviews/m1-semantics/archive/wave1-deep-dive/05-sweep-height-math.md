All sources verified and both numeric checks done. Writing up.

---

# A) ToF physical confirmation — checked against primary sources

**Sources fetched today** (st.com is firewalled on this host; official ST PDFs were retrieved via mirrors and verified by DocID):
DS = VL53L1X datasheet **DocID031281 Rev 3** (pololu/sparkfun mirrors of the ST PDF); UM2356 API manual **DocID031478 Rev 1** (pololu mirror); **AN5191 Rev 1** (ROI) and **AN5231 Rev 2** (cover window) via `r.jina.ai` text-proxy of st.com; ST community thread 254529 (answer by ST employee J. Kvam); ClearGrasp arXiv:1910.02550; multipath arXiv:1404.1116; Hale & Querry water data (omlc.org).

**Transparent PET / glass — the water-bottle case.** Confidence: HIGH for the mechanism, MEDIUM-HIGH for end-to-end detectability. ST's own docs contradict the datasheet's marketing line ("absolute distance whatever the target color and reflectance"):
- AN5231: required window transmission >87% at 930–950 nm; PMMA 94%, polycarbonate/tempered 85–88%, **"PET is not recommended (80% transmission)"**. A bottle is two walls: ~0.8² plus ~4–5% Fresnel loss per surface.
- ST forum (vendor-authoritative): "most plastic is transparent to 940 nm no matter what color"; "even the clearest glass is only 95% transparent"; **"ALL the photons are averaged… with a really bright target like sheet metal that will dominate"**; and curved glossy surfaces "bounce off and are not detected at all".
- Physics: a *full* bottle returns a shallow volume echo (water absorption a=0.267 cm⁻¹ at 940 nm → 1/e depth ≈ 3.7 cm, Hale & Querry), usually **detectable with a few-cm positive bias**. An *empty* PET bottle gives only off-axis Fresnel glints; from most bearings the sensor returns the **background wall** (attenuated ~×0.3 through two walls). Cross-domain corroboration: standard depth sensors fail on transparent objects (ClearGrasp); mixed/transparent returns corrupt ToF depth (arXiv:1404.1116).
- Consequence for design: for bottle-class labels, "range = expected background" and "dropout" must map to **inconclusive / not-confirmed**, never *contradicted*. Confidence: HIGH on that rule.

**Dark/black surfaces.** HIGH (DS tables 6/7/9). What matters is signal rate, not colour: max range 88% white 360 cm vs 17% grey 170 cm (dark, 100 ms); 4×4 ROI drops grey-17% to **45 cm**. Black matte (<5%) will only be seen close-in; the actionable levers are longer timing budget (TB 100→140 ms: 360→400 cm white) and the driver's signal limit (default 1 Mcps → status 2 SIGNAL_FAIL; lowering it trades false ranges for reach — UM2356 warns unset limits "could return an incorrect measurement").

**Ambient light.** HIGH: long mode, white 88%: 360 cm dark → **166 cm at 50 kcps/SPAD (sun behind a window) → 73 cm at 200 kcps/SPAD (direct sun)**; short mode is ambient-flat at ~130 cm. DS footnote: **office lighting ≈ 5 kcps/SPAD** — benign. Verdict: fine indoors away from direct sun/IR flood; direct sun reduces the sweep to a <1 m proximity sensor.

**Status/intensity as the disambiguator.** HIGH. UM2356 exposes per-measurement `RangeStatus` (0 valid, 1 SIGMA_FAIL, 2 SIGNAL_FAIL, 4 out-of-bounds, 5 HW, 7 wrap, 8 processing, 14 invalid), `SignalRateRtnMegaCps`, `AmbientRateRtnMegaCps`, `EffectiveSpadRtnCount`, `SigmaMilliMeter`; defaults sigma 15 mm / signal 1 Mcps. The scan payload should carry all five per beam — that's what separates "no object", "object too dark/transparent", and "noisy".

**Outcome design.** Confidence: MEDIUM-HIGH. Three values + confidence is right, but map them as: *confirmed* = status 0 AND |range−expected| ≤ tol; *contradicted* = status 0 AND range matches neither the object nor its plausible background (e.g. a closer obstacle); *absent/inconclusive* = status≠0, dropout, or range = background → keep vision confidence, don't refute. Mitigations that fit a servo sweep: multi-bearing rescan (the bottle's specular lobe is narrow — a glint is angle-dependent), ROI shrink to 8×8/4×4 (15–20°) to exclude wall/background returns (AN5191 §4.4 recommends exactly this when walls contaminate the reading), longer TB on rescan, and per-ROI offset calibration if <5 cm accuracy is needed (AN5191 §3.2).

**Scan matching (coarse-to-fine grid vs ICP).** Confidence: HIGH. Keep the grid: I timed the sim's matchers on this box — local search **88 ms** (1,521 candidates × 91 beams), bootstrap **608 ms** (19,499 × 46). It needs no correspondences, can't diverge, handles 5% dropouts/2% outliers via the capped loss; ICP/Gauss-Newton needs correspondences and its Jacobians are discontinuous exactly at AABB corners, where all the information lives. Bigger risk than the optimizer is the **beam model**: real beams are 15–27° cones (emitter cone stays 27° regardless of ROI, AN5191), and "photons are averaged" — the sim's 2° rays, uniform dropouts and reflectance-free model validate the *matcher*, not *detectability*.

# B) Object height from the floor homography

**Derivation.** H maps pixels→floor (z=0). For a bbox: P_b = H(bottom px), P_t = H(top px) is *not* the object top's floor position — it's where the top ray crosses z=0. With camera centre (C_xy, Z_c) and a vertical segment standing at P_b of height h, the core-projection identity gives

  **|P_t − P_b| = h/(Z_c − h) · d,  d = |P_b − C_xy|  ⟺  h = Z_c·Δ/(d + Δ)**

(degenerates as h→Z_c: the top pixel reaches the horizon). Verified numerically: exact (0.000 cm) at 6 positions × 4 heights and on a 22°-tilted camera; 0.7–7.5 cm error when feeding it an honest cylinder-silhouette bbox.

**Is h in H alone? No — HIGH confidence, proven.** (Z_c, f) and (k·Z_c, k·f) give a numerically *identical* H (max difference 0.00e+00); the uncalibrated plane-pose is a 2-parameter family. Needed: Z_c and C_xy. The repo stores only floor correspondences + polygon (`calibrate.py`), so an extension is required: **(1, recommended)** run the already-supported `calibrate.py intrinsics`, then `cv2.solvePnP` on the stored floor reference points → pose → Z_c, C_xy; **(2)** no-intrinsics fallback: tape Z_c, C_xy ≈ H(principal point ≈ image centre) — ±5 cm here costs only ±1.4 cm on h; **(3)** self-calibration from ≥2 detections (Δ-lines intersect at C_xy) fails at 21–45 cm error — discard.

**Sensitivity (measured).** Top edge ±2 px → ±4.4 cm on a 0.25 m bottle (~18%); bottom edge ±1 px → ±2.5 cm; Z_c ±5 cm → ±0.45 cm; C_xy ±5 cm → ±1.4 cm. Conditioning: near-nadir at 1280×720 (f≈340–420 px), a 0.25 m object spans only 12–25 px vertically (1 px ≈ 1.1–2.1 cm of h); more camera obliquity helps.

**Failure modes (must-gate).** A flat 0.6×0.4 m mat yields h_est = 0.93 m (that's its footprint, not height) — needs the label/plane prior (M1 already has `project.point_by_label`). A 0.25 m bottle on a 0.75 m table yields h_est ≈ 0.58 m ≈ 2.3× its true height, with the anchor landing on the table footprint — i.e. the anomaly *is* the signal, as long as the class prior says "bottle ⇒ 0.1–0.4 m".

**Verdict / fit.** Feasible and cheap **as a coarse, class-gated plausibility signal** (±5 cm best case) — into M1 only if intrinsics are available (else it is a classic M3 second signal alongside the C1 probe rule, which needs no calibration and already catches the on-table case). Do not trust it under ~10 px bbox extent, and never treat its output as a measurement.

**What I ran/created:** sim re-run (read-only, reproduces §10.1 exactly; its sanity line prints 1.224 m vs "expect ~3.25" because a furniture box lies on that ray — cosmetic only); `height_math_check{,2,3}.py` in `.hermes/cache/scratch/taskA6/`. Nothing written to the repo or /tmp/opencode. Two caveats: st.com needed mirrors/proxy; DS pages 2–3 and 25–27 have no text layer (OCR-only, not needed).