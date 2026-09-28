"""Jev tactical layer: the rover's programmable common sense.

Everything a human should review lives at the top of this file: the questions,
the option rubrics, and the thresholds. Nothing else in the project hard-codes
a number that changes how the rover reacts to a judgment.

Design rules, deliberately copied from the drone project:
  * Jev is advisory. Code owns safety and execution; the reflex layer in
    control.py overrides any judgment, always.
  * Code decides WHEN to ask. An unchanged scene reuses the cached judgment.
  * The state has to contain the answer: if a maneuver cannot be expressed
    with the observed fields, it does not exist as an option.
  * Several atomic questions in one request. They run in parallel, so this
    costs no extra latency over one broad question.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from scene import Scene

MODEL = "jev-latest"

# --- how the control code reacts to a judgment --------------------------------
THRESHOLDS = {
    "stale_after_s": 2.0,     # ignore a judgment older than this; the world moved on
    "call_hz": 2.0,           # upper bound on how often we ask
    "call_budget": 400,       # hard cap per run, so a bug cannot run up a bill
    "consult_within_m": 3.0,  # ask when anything is this close on the path...
    "consult_stuck_s": 0.8,   # ...or progress has stalled this long
    "consult_unknown": 0.40,  # ...or this fraction of the near ring was never seen
    "consult_mover_m": 2.5,   # ...or a moving obstacle is this close
    "refresh_s": 6.0,         # re-ask even about an unchanged scene this often
    "commit_s": 1.2,          # commit to a maneuver instead of chattering
    "override_risk": 1.7,     # ...unless things get this dangerous, then re-decide now
    "risk_slow": 1.45,        # score above which speed is bled regardless of maneuver
    "unreliable": 0.5,        # Noul above which a full-speed decision is not earned
    "stuck": 0.5,             # Noul above which recovery starts
    "obstructed": 0.5,        # Noul above which the straight line is treated as blocked
}

# The rover's own capabilities. Without this the model cannot know what
# "back_and_turn" costs, or what counts as a wide gap.
ROBOT = {
    "type": "indoor differential-drive rover; can turn in place; no arm",
    "footprint_radius_m": 0.18,
    "max_speed_mps": 0.6,
    "max_turn_deg_s": 120,
    "sensors": {
        "camera": "fixed overhead camera, whole room; blind behind tall furniture; "
                  "occlusion_risk says how much of the 1.5 m ring around the rover "
                  "has never been seen",
        "distance": "front time-of-flight; an ESP32 reflex stops forward motion "
                    "below 0.18 m regardless of anything Jev says; no rear sensor",
    },
    "latency": "judgment arrives about 0.1-0.3 s after the scene; commands stream at 20 Hz",
    "note": "Distances are metres from the camera map. 'unseen' limit means no recent "
            "observation, not a wall. The rover cannot climb and cannot see under furniture.",
}

MISSION = ("Reach the goal while never colliding with anything. Prefer a slower, "
           "verified route over a fast uncertain one. If a person or pet is in the "
           "way, wait. If the rover cannot make progress, recover instead of pushing.")

MANEUVERS = {
    "hold_course": (
        "The planned path is still a good route: goal.path_valid is true and the path's "
        "initial bearing (goal.path_bearing_deg) is safe to follow. This is the default - "
        "choose it unless something concrete argues otherwise. Note that turning away "
        "from the goal bearing is normal when the planner routes around furniture; "
        "path_bearing_deg, not goal.bearing_deg, is what the rover will drive."),
    "veer_left": (
        "Override the planner to the left: the path ahead is blocked or unsafe, and the "
        "left sectors (near_left, left, far_left) offer clearly more verified free space "
        "than the right (compare free_m and free_runs_deg, prefer runs whose limit is "
        "'obstacle'). Do not choose this merely because the goal lies to the left."),
    "veer_right": (
        "Override the planner to the right: the path ahead is blocked or unsafe, and the "
        "right sectors (near_right, right, far_right) offer clearly more verified free "
        "space than the left. Do not choose this merely because the goal lies to the right."),
    "creep": (
        "The way ahead is limited by 'unseen' rather than a seen obstacle, or the "
        "observation is unreliable (occlusion_risk high, pose_source not 'tag'). "
        "Advance slowly at creep speed to improve observation instead of committing "
        "to a full-speed maneuver."),
    "back_and_turn": (
        "Blocked or too tight straight ahead, no side gap is wide enough for the "
        "rover's width (widest_run_clearance_m is small), and progress has stalled. "
        "Reverse a little while rotating toward the freer side. Note there is no rear "
        "sensor: reverse is slow and short."),
    "stop_and_wait": (
        "A moving obstacle (tracks[].kind == 'moving') is crossing or about to enter "
        "the path, or the follow target sits between the rover and the goal. Hold "
        "position and wait for the picture to clear rather than driving through."),
    "reacquire_goal": (
        "The goal direction is unknown or stale and no safe path is visible; the "
        "rover cannot tell where to go. Rotate in place to look around before moving."),
}

QUESTIONS = {
    "maneuver": Choice(
        instructions={
            "question": "Which single maneuver should the rover commit to right now?",
            "role": "You are the tactical decision layer of a small indoor rover.",
            "mission": MISSION,
            "policy": "Safety and execution are owned by code. Choose the maneuver that "
                      "best matches the observed fields; code may refuse it if a reflex "
                      "fires. Weigh verified free space over unknown space.",
        },
        criteria=MANEUVERS,
    ),
    "risk": Score(
        instructions={
            "question": "How dangerous is the rover's immediate situation?",
            "inspect": "`observed.obstacles`, `observed.dynamics`, `observed.hardware`, "
                       "`observed.goal.path_bearing_deg`",
            "focus": "Judge danger relative to where the rover is travelling (the path "
                     "bearing), not to whatever happens to be nearest on any side.",
        },
        criteria=[
            "clear and open: nearest obstacle is beyond ~2x the stopping distance and "
            "nothing is blocked in the direction of travel",
            "tight but manageable: an obstacle or narrow gap is close, but there is a "
            "verified gap the rover fits through and progress is possible",
            "about to hit something: a blocked sector within stopping distance on the "
            "current heading, or the front distance sensor is below its slow threshold",
        ],
    ),
    "truly_stuck": Noul(
        instructions={
            "question": "Is the rover genuinely stuck rather than briefly slowed?",
            "inspect": "`observed.dynamics`, `observed.goal`",
            "focus": "no_progress_s of several seconds while speed was commanded is a "
                     "stuck rover; a fraction of a second of slip on a rug is not.",
        },
        criteria={
            "true": "Recovery is warranted: reverse/rotate or look around.",
            "false": "Keep executing; the rover is expected to move again shortly.",
        },
    ),
    "path_obstructed": Noul(
        instructions={
            "question": "Is the straight line from the rover to the goal physically "
                        "blocked by a seen obstacle, as opposed to merely unseen or narrow?",
            "compare": ["`observed.obstacles.nearest_bearing_deg`", "`observed.goal.bearing_deg`",
                        "`observed.obstacles.sectors`"],
            "focus": "A sector with limit 'obstacle' near the goal bearing is a block. "
                     "An 'unseen' limit is uncertainty, not a block.",
        },
        criteria={
            "true": "An obstacle actually sits between the rover and the goal.",
            "false": "No seen obstacle interferes with the straight line to the goal.",
        },
    ),
    "observation_unreliable": Noul(
        instructions={
            "question": "Are the observations too poor to justify a full-speed decision?",
            "inspect": "`observed.quality`",
            "focus": "Dead-reckoned pose, a stale tag, or a high occlusion_risk near the "
                     "rover all argue that the picture cannot be trusted at speed.",
        },
        criteria={
            "true": "Slow down or creep until the observation improves.",
            "false": "The map is fresh and trustworthy; normal speed is fine.",
        },
    ),
    "path_still_good": Noul(
        instructions={
            "question": "Is the current planned path still valid and safe to follow?",
            "inspect": "`observed.goal`",
            "focus": "A valid path with nothing on its initial bearing "
                     "(goal.path_bearing_deg) is good. A path through regions marked "
                     "'unseen', a moving track near that bearing, or a stale plan "
                     "(path_valid false) is not.",
        },
        criteria={
            "true": "Code should keep following the planned path.",
            "false": "The plan is stale or unsafe; act on the maneuver instead.",
        },
    ),
}


def decision_needed(scene: Scene, judgment_age_s: float | None) -> bool:
    """Code decides WHEN there is a judgment worth paying for."""
    if judgment_age_s is None or judgment_age_s > THRESHOLDS["refresh_s"]:
        return True
    if scene.nearest_m is not None and scene.nearest_m < THRESHOLDS["consult_within_m"]:
        return True
    if any(s.blocked for s in scene.sectors):
        return True
    if scene.quality.occlusion_risk > THRESHOLDS["consult_unknown"]:
        return True
    if scene.dynamics.no_progress_s > THRESHOLDS["consult_stuck_s"]:
        return True
    if any(t.kind == "moving" and t.range_m < THRESHOLDS["consult_mover_m"] for t in scene.tracks):
        return True
    return False


def build_state(scene: Scene) -> dict:
    """Everything the model is allowed to know, and nothing it cannot observe."""
    observed = scene.to_dict()
    mission = observed.pop("mission", {"mode": "goto"})
    return {"robot": ROBOT, "mission": mission, "observed": observed}


DEFAULT = {
    "maneuver": "hold_course", "confidence": 0.0, "probabilities": {},
    "risk": 0.0, "risk_confidence": 0.0,
    "truly_stuck": 0.0, "path_obstructed": 0.0, "observation_unreliable": 0.0,
    "path_still_good": 0.0,
    "source": "default", "age_s": 0.0,
}


class Tactician:
    """Asks Jev for a judgment at most ``hz`` times a second, and only when the
    scene has actually changed enough to be worth a call. Non-blocking: the
    control loop reads whatever judgment is currently cached."""

    def __init__(self, hz: float = THRESHOLDS["call_hz"],
                 budget: int = THRESHOLDS["call_budget"], model: str = MODEL):
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
        if not key:
            raise RuntimeError("set TYPESAFE_API_KEY (see .env.example)")
        self.client = TypeSafeClient(api_key=key)
        self.min_dt = 1.0 / hz
        self.budget = budget
        self.model = model
        self.calls = 0
        self.attempts = 0
        self.skipped = 0
        self.errors = 0
        self.tokens = 0
        self.latency: list[float] = []
        self.last_error: str | None = None
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._latest = dict(DEFAULT)
        self._stamp = 0.0
        self._lock = threading.Lock()
        self._last_sent = float("-inf")
        self._last_key = None
        self._last_offer_t = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @staticmethod
    def _key(scene: Scene) -> tuple:
        """Coarse fingerprint: only re-ask when the situation is materially new."""
        tracks = tuple(sorted(
            (t.kind, int(t.bearing_deg / 20), int(t.range_m / 1.0)) for t in scene.tracks))
        return (
            tuple(None if s.free_m is None else int(s.free_m / 0.75) for s in scene.sectors),
            tuple(s.limit for s in scene.sectors if s.blocked),
            None if scene.nearest_m is None else int(scene.nearest_m / 0.75),
            scene.quality.pose_source,
            int(scene.quality.occlusion_risk * 4),
            int(min(scene.dynamics.no_progress_s, 4.0)),
            None if scene.goal.bearing_deg is None else int(scene.goal.bearing_deg / 20),
            scene.goal.path_valid,
            None if scene.goal.path_bearing_deg is None else int(scene.goal.path_bearing_deg / 20),
            tracks,
        )

    def offer(self, scene: Scene, now: float) -> None:
        """Non-blocking. Hand the latest scene over if it is worth a call."""
        age = None
        with self._lock:
            age = now - self._stamp if self._stamp else None
        if not decision_needed(scene, age):
            return
        if self.attempts >= self.budget or now - self._last_sent < self.min_dt:
            return
        key = self._key(scene)
        if key == self._last_key and age is not None and age < THRESHOLDS["refresh_s"]:
            self.skipped += 1
            return
        # Build the state NOW, in the caller thread. The worker gets an
        # immutable snapshot instead of racing the control loop's mutations.
        state = build_state(scene)
        try:
            self._q.put_nowait((state, now))
            self._last_sent, self._last_key = now, key
        except queue.Full:
            pass

    def read(self, now: float) -> dict:
        with self._lock:
            out = dict(self._latest)
        out["age_s"] = round(now - self._stamp, 2) if self._stamp else None
        return out

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                state, now = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            t0 = time.time()
            self.attempts += 1  # counts against the budget whether or not the call succeeds
            try:
                r = self.client.system_one(state=state, model=self.model, questions=QUESTIONS)
                a = r.answers
                judgment = {
                    "maneuver": a["maneuver"].choice,
                    "confidence": round(a["maneuver"].confidence, 3),
                    "probabilities": {k: round(v, 3) for k, v in a["maneuver"].probabilities.items()},
                    "risk": round(a["risk"].score, 2),
                    "risk_confidence": round(a["risk"].confidence, 3),
                    "truly_stuck": round(a["truly_stuck"].noul, 3),
                    "path_obstructed": round(a["path_obstructed"].noul, 3),
                    "observation_unreliable": round(a["observation_unreliable"].noul, 3),
                    "path_still_good": round(a["path_still_good"].noul, 3),
                    "source": "jev",
                }
                self.tokens += r.usage.input_tokens + r.usage.output_tokens
                self.calls += 1
                self.latency.append(time.time() - t0)
                with self._lock:
                    self._latest, self._stamp = judgment, now
            except Exception as e:  # degrade, never crash the run
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                # A failed call must not leave the same static scene marked as
                # "unchanged" forever: clear the key so it can be re-asked.
                self._last_key = None
                with self._lock:
                    self._latest = dict(DEFAULT, source=f"error:{type(e).__name__}")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            self.client.close()
        except Exception:
            pass

    def stats(self) -> dict:
        lat = sorted(self.latency)
        return {
            "calls": self.calls, "attempts": self.attempts,
            "skipped_unchanged": self.skipped, "errors": self.errors,
            "last_error": self.last_error, "tokens": self.tokens,
            "median_latency_s": round(lat[len(lat) // 2], 3) if lat else None,
            "p90_latency_s": round(lat[int(len(lat) * 0.9)], 3) if lat else None,
        }
