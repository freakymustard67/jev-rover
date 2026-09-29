# Errata snippet — approach standoff vs planner inflation (w4b/I7 audit, 2026-09-29)

Drafted for `docs/planning/semantics-layer-proposal.md:171` and `:174`,
`docs/planning/m1-plan.md:353` (and the M2 routing note in
`docs/reviews/m1-semantics/m2-design.md`). The same text is applied in the
separate docs commit of `i7-approach-standoff.patch` (commit 2 of 2).

---

**proposal:171** — replacement for "obstacle inflation already exists in the planner":

> **Errata (w4b/I7 audit, 2026-09-29):** "obstacle inflation already exists in the planner" is
> exactly what makes a 0.35 m ring unreachable for objects of radius ≥ ~0.12 m — the planner
> blocks a disc of radius ≈ obj_r + inflation + cell/2, and the executor's reflex stops at a
> ~0.30 m surface gap. The standoff must satisfy `standoff ≥ obj_radius + 0.35`
> (`RoomConfig.required_standoff_m`; 0.65/0.75 m for r = 0.30/0.40), and the goal handed to
> routing must be the free-space projection of the ring point (`Planner.project_to_free`),
> never the raw ring point: the raw point parks the rover ~0.3 m short (658 map_brake trips,
> permanent standstill) while `path_valid`/`path_bearing_deg` stay green.

**proposal:174** — acceptance "routes the rover to the mat and stops within standoff":

> **Errata (w4b/I7 audit, 2026-09-29):** "within standoff" is only meaningful at the per-object
> standoff (`obj_radius + 0.35`); at a fixed 0.35 m ring and r ≥ ~0.12 m the rover reaches the
> projected free-space goal and the 0.30 m latch, but the reflex holds it at the inflated
> boundary (map_brake trips) rather than at the requested ring.

**m1-plan:353** — note appended to the `--find` acceptance item:

> **Note (w4b/I7 audit, 2026-09-29):** when M2 wires routing, set `scene.goal` to
> `executor.planner.project_to_free(approach_point(...))` — never the raw ring point — and derive
> the standoff per object (`cfg.required_standoff_m(obj_radius)`; ≥ obj_r + 0.35). A raw ring
> point inside the planner's inflated region stalls the rover ~0.3 m short (permanent standstill,
> 658 map_brake trips). The fix is implemented in `control.py`/`run.py`.

**m2-design (routing decision)** — addendum:

> **I7 addendum (w4b audit, 2026-09-29):** aim at the free-space projection
> (`executor.planner.project_to_free(approach)`), not the raw approach point, and use the
> per-object standoff (`cfg.required_standoff_m`); the raw point can sit inside the planner's
> inflated region and stalls the rover.

---

Suggested follow-ups (not in this patch):
- F4-lite: carry a world-space radius from the detection bbox into `SemanticObject`/`Destination`
  so the caller can apply `required_standoff_m(obj_r)` without the fixture path.
- Derive the map_brake gap (0.30 m, `control.py` `_reflex`) from config/standoff instead of
  hardcoding it.
