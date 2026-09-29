"""W4C driver: real SemanticsWorker/SemanticsRunner vs loopback RemoteVision mock.

Run from the scratch-clone root:
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python w4c_drive.py

Scenarios: ok, slow>timeout, HTTP 500, malformed JSON, wrong schema, all-invalid
entry, connection refused. For each failure: clean error result, worker thread
alive, cooldown armed and blocking, following pass succeeds after cooldown.
Also: token-hygiene scan of every file this run writes, build_vision('remote'/
'local') M1 entry-point message, and config validation gaps for a remote kind.

LOOPBACK ONLY: only 127.0.0.1 is contacted (the mock server + one dead loopback
port). No external host is touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from config import ConfigError, RoomConfig
from perception import Homography, SemanticContext
from semantics import SemanticsRunner, build_vision
from w4c_mockserver import MockVisionServer, dead_port
from w4c_remotevision import ENDPOINT_ENV, TOKEN_ENV, RemoteVision, RemoteVisionError

OUT = Path(__file__).resolve().parent / "w4c_out"
STORE = OUT / "store"
OUT.mkdir(parents=True, exist_ok=True)
CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  |  {detail}" if detail else ""))
    return bool(ok)


def hr(title: str) -> None:
    print(f"\n---- {title} " + "-" * max(0, 72 - len(title)))


# ---------------------------------------------------------------- harness
def _ctx(floor_lab=np.array([180.0, 128.0, 128.0])) -> SemanticContext:
    # verbatim from tests/test_semantics_worker.py:37-46 (PR tip)
    H = Homography([[0, 0], [100, 0], [100, 100], [0, 100]],
                   [[0, 0], [1, 0], [1, 1], [0, 1]])
    return SemanticContext(
        homography=H, room_w_m=1.0, room_h_m=1.0, cell_m=0.05,
        polygon_px=np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float),
        floor_lab=floor_lab, floor_lab_tolerance=20.0,
        grid_log_odds=np.zeros((20, 20), np.float32),
        grid_last_seen=np.zeros((20, 20), np.float32),
        grid_t=0.0, occupied_thr=0.42, stale_s=3.0)


FRAME = np.zeros((120, 160, 3), np.uint8)
CTX = _ctx()


def wait_errors(runner: SemanticsRunner, want: int, deadline: float = 5.0) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        if runner.worker.errors >= want:
            return True
        time.sleep(0.005)
    return False


def wait_result(runner: SemanticsRunner, t: float, deadline: float = 5.0):
    end = time.time() + deadline
    while time.time() < end:
        res = runner.poll(t + 0.001)
        if res is not None:
            return res
        time.sleep(0.005)
    return None


def run_error_scenario(runner, label: str, tb: float, set_bad, set_ok) -> None:
    """bad mode -> error + cooldown; ok mode -> following pass succeeds."""
    prev_passes = runner.store.passes
    prev_cd = runner.skipped["cooldown"]
    set_bad()
    check(f"{label}: submit accepted", runner.maybe_pass(tb, CTX, FRAME, force=True))
    check(f"{label}: worker records a clean error", wait_errors(runner, runner.worker.errors + 1))
    time.sleep(0.05)                                   # let _push land before consuming
    res = runner.poll(tb)
    check(f"{label}: poll() consumes the error as None (no merge)", res is None)
    check(f"{label}: cooldown armed at t+5.0",
          abs(runner.fail_cooldown_until - (tb + 5.0)) < 1e-6,
          f"fail_cooldown_until={runner.fail_cooldown_until}")
    blocked = runner.maybe_pass(tb + 1.0, CTX, FRAME, force=True)
    check(f"{label}: cooldown blocks the next pass",
          blocked is False and runner.skipped["cooldown"] == prev_cd + 1,
          f"skipped[cooldown]={runner.skipped['cooldown']}")
    check(f"{label}: worker thread still alive", runner.worker._thread.is_alive())
    check(f"{label}: in-flight cleared after error", runner._inflight is None)
    print(f"         last_error = {runner.worker.last_error!r}")
    set_ok()
    check(f"{label}: pass accepted after cooldown",
          runner.maybe_pass(tb + 5.0, CTX, FRAME, force=True))
    merged = wait_result(runner, tb + 5.0)
    check(f"{label}: following pass succeeds (failure isolated)",
          merged is not None and runner.store.passes == prev_passes + 1,
          f"passes={runner.store.passes} objects={len(merged.objects) if merged else '-'}")


def main() -> int:
    print(f"python={sys.version.split()[0]}  scratch={OUT}")
    # fresh evidence per run: clear only this run's own fixed output files
    for p in (STORE / "events.jsonl", STORE / "w4c_latest.json",
              STORE / "w4c_latest.json.tmp", OUT / "w4c-run-summary.json"):
        p.unlink(missing_ok=True)
    print("LOOPBACK ONLY: mock on 127.0.0.1 + one dead loopback port; no external host.")
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        print("FATAL: run with JEV_VISION_TOKEN set (env-only secret demo)")
        return 2
    tok_fp = hashlib.sha256(token.encode()).hexdigest()[:8]

    srv = MockVisionServer().start()
    print(f"mock server: {srv.url}")
    os.environ[ENDPOINT_ENV] = srv.url

    # ---------------------------------------------------- direct adapter unit
    hr("A. adapter direct checks")
    saved = os.environ.pop(ENDPOINT_ENV, "")
    try:
        RemoteVision(timeout_s=1.0).infer(FRAME)
        check("no endpoint anywhere -> clean error", False)
    except RemoteVisionError as e:
        check("no endpoint anywhere -> clean error", "endpoint is not set" in str(e),
              f"msg={e}")
    os.environ[ENDPOINT_ENV] = saved

    rv_env = RemoteVision(timeout_s=2.0)               # endpoint via env
    dets = rv_env.infer(FRAME, labels=["mat", "bottle"])
    check("infer via env endpoint: 2 valid detections, 0 skipped",
          [d.label for d in dets] == ["mat", "bottle"] and rv_env.last_skipped == 0,
          f"dets={[(d.label, d.bbox_px, round(d.score, 2)) for d in dets]}")
    rv_cfg = RemoteVision(srv.url, timeout_s=2.0)      # endpoint via constructor
    check("infer via constructor endpoint: works",
          len(rv_cfg.infer(FRAME, labels=["mat"])) == 2)

    # ------------------------------------------------------ real runner setup
    cfg = RoomConfig()
    cfg.name = "w4c"
    cfg.semantics = replace(cfg.semantics, store_dir=str(STORE),
                            min_interval_s=0.0, failure_cooldown_s=5.0,
                            max_passes_per_min=200)
    vision = RemoteVision(timeout_s=0.25)               # env endpoint, short timeout
    runner = SemanticsRunner(cfg, "w4c", vision)
    try:
        hr("B. healthy pass (baseline) + artifact write")
        srv.mode = "ok"
        check("baseline pass accepted", runner.maybe_pass(1.0, CTX, FRAME, force=True))
        merged = wait_result(runner, 1.0)
        check("baseline pass merged (2 objects)",
              merged is not None and merged.passes == 1 and len(merged.objects) == 2,
              f"labels={[o.label for o in merged.objects] if merged else '-'}")
        check("artifacts written",
              (STORE / "w4c_latest.json").exists() and (STORE / "events.jsonl").exists())

        hr("C. slow server > timeout: submit non-blocking, loop unaffected")
        srv.mode = "slow"                               # sleeps 0.6 s, timeout is 0.25 s
        t0 = time.time()
        ok = runner.maybe_pass(2.0, CTX, FRAME, force=True)
        dt = time.time() - t0
        check("submit returns immediately while slow", ok and dt < 0.05, f"{dt*1000:.1f} ms")
        inflight = runner.maybe_pass(2.1, CTX, FRAME, force=True)
        check("second submit refused (one in flight)",
              inflight is False and runner.skipped["inflight"] == 1)
        t0 = time.time()
        for _ in range(60):
            runner.poll(2.0)
            runner.snapshot(2.0)
        dt_loop = time.time() - t0
        check("60x poll+snapshot in flight stays cheap", dt_loop < 0.2,
              f"{dt_loop*1000:.1f} ms total")
        check("timeout -> clean error recorded", wait_errors(runner, runner.worker.errors + 1))
        time.sleep(0.05)
        check("timeout: poll() consumes error as None", runner.poll(2.0) is None)
        check("timeout: cooldown armed", abs(runner.fail_cooldown_until - 7.0) < 1e-6,
              f"until={runner.fail_cooldown_until}")
        check("timeout: cooldown blocks", runner.maybe_pass(3.0, CTX, FRAME, force=True) is False
              and runner.skipped["cooldown"] == 1)
        check("timeout: worker alive", runner.worker._thread.is_alive())
        print(f"         last_error = {runner.worker.last_error!r}")
        srv.mode = "ok"
        check("timeout: following pass accepted after cooldown",
              runner.maybe_pass(7.0, CTX, FRAME, force=True))
        merged = wait_result(runner, 7.0)
        check("timeout: following pass succeeds",
              merged is not None and runner.store.passes == 2
              and [o.label for o in merged.objects] == ["mat", "bottle"])

        hr("D. failure modes x cooldown (500 / malformed / schema / badentry / refused)")
        run_error_scenario(runner, "http500", 9.0,
                           lambda: setattr(srv, "mode", "http500"),
                           lambda: setattr(srv, "mode", "ok"))
        run_error_scenario(runner, "malformed-json", 16.0,
                           lambda: setattr(srv, "mode", "malformed"),
                           lambda: setattr(srv, "mode", "ok"))
        run_error_scenario(runner, "wrong-schema", 23.0,
                           lambda: setattr(srv, "mode", "schema"),
                           lambda: setattr(srv, "mode", "ok"))
        run_error_scenario(runner, "all-invalid-entry", 30.0,
                           lambda: setattr(srv, "mode", "badentry"),
                           lambda: setattr(srv, "mode", "ok"))
        run_error_scenario(runner, "connection-refused", 37.0,
                           lambda: os.environ.__setitem__(
                               ENDPOINT_ENV, f"http://127.0.0.1:{dead_port()}/detect"),
                           lambda: os.environ.__setitem__(ENDPOINT_ENV, srv.url))

        hr("E. counters / stats after 6 consecutive failures")
        stats = runner.stats()
        print(json.dumps({k: stats[k] for k in
                          ("model", "passes", "passes_started", "errors", "last_error",
                           "stale_dropped", "skipped", "median_ms")}, indent=2))
        check("7 passes merged, 6 errors, 13 offers started",
              stats["passes"] == 7 and stats["errors"] == 6
              and stats["passes_started"] == 13,
              f"passes={stats['passes']} errors={stats['errors']} started={stats['passes_started']}")
        check("worker thread alive at end of run", runner.worker._thread.is_alive())
    finally:
        runner.close()

    hr("F. build_vision('remote'/'local') at M1 = the M2 entry point")
    H = _ctx().homography
    for kind in ("remote", "local"):
        sc = replace(cfg.semantics, model=replace(cfg.semantics.model, kind=kind))
        try:
            build_vision(sc, H)
            check(f"build_vision(kind={kind!r}) raises at M1", False, "did NOT raise")
        except NotImplementedError as e:
            check(f"build_vision(kind={kind!r}) raises at M1", True, f"msg={e}")

    hr("G. config: what a remote kind gets vs needs")
    room_stub = {
        "name": "w4c",
        "homography": {"image_points_px": [[0, 0], [100, 0], [100, 100], [0, 100]],
                       "world_points_m": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        "floor": {"polygon_px": [[0, 0], [100, 0], [100, 100], [0, 100]]},
    }

    def remote_cfg(**model):
        d = dict(room_stub)
        d["semantics"] = {"model": model}
        return RoomConfig.from_dict(d, where="w4c")

    try:
        remote_cfg(kind="remote", endpoint="://not-a-url at all", timeout_s=10.0)
        check("gap: bogus endpoint string accepted at load", True,
              "config.py:302-304 validates kind + timeout_s>0 only")
    except ConfigError as e:
        check("gap: bogus endpoint string accepted at load", False, str(e))
    try:
        remote_cfg(kind="remote", token="x")
        check("'token' config key rejected by strict section", False, "accepted?!")
    except ConfigError as e:
        check("'token' config key rejected by strict section", "unknown key" in str(e),
              f"msg={e}")
    for val, want_reject in ((0.0, True), (3600.0, False)):
        try:
            remote_cfg(kind="remote", timeout_s=val)
            check(f"timeout_s={val}: {'rejected' if want_reject else 'accepted (no upper bound = gap)'}",
                  not want_reject)
        except ConfigError as e:
            check(f"timeout_s={val}: rejected", want_reject, f"msg={e}")
    model_keys = sorted(RoomConfig().to_dict()["semantics"]["model"].keys())
    check("serialized model has no token field (endpoint IS serialized)",
          "token" not in model_keys, f"keys={model_keys}")

    hr("H. token hygiene: env-only, header-only, in no file this run wrote")
    check("token was sent as Bearer header on every mock request",
          bool(srv.requests) and all(r["authorization"].startswith("Bearer ") for r in srv.requests),
          f"requests={len(srv.requests)} auth_received=True value_withheld=True fp=sha256:{tok_fp}")
    check("mock saw JSON bodies", all(r["content_type"] == "application/json"
                                      for r in srv.requests))
    summary_path = OUT / "w4c-run-summary.json"
    summary = {
        "note": "W4C loopback prototype run; token value deliberately withheld",
        "endpoint_scheme": "http://127.0.0.1:<ephemeral>",
        "token_sha256_prefix": tok_fp,
        "mock_request_count": len(srv.requests),
        "checks": CHECKS,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    leak_targets = [STORE / "w4c_latest.json", STORE / "events.jsonl", summary_path]
    leaks = []
    for p in leak_targets:
        if p.exists() and token in p.read_text(errors="ignore"):
            leaks.append(str(p))
    check("token absent from every written artifact/summary",
          not leaks and token not in (runner.worker.last_error or ""),
          f"scanned={[p.name for p in leak_targets]} leaks={leaks}")
    srv.stop()

    hr("I. verdict")
    failed = [c for c in CHECKS if not c["ok"]]
    print(f"checks: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    for c in failed:
        print(f"  FAIL: {c['name']} | {c['detail']}")
    print(f"summary: {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())