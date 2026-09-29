# W4C — RemoteVision threat model & M1-surface audit (jev-rover M2 prep)

**Date:** 2026-09-29 · **Base:** PR #1 branch `review/m1-semantics-audit`, tip `bec1d91` (scratch clone)
**Method:** read-only audit of the M1 code (all line numbers = PR tip) + a loopback-only prototype of `RemoteVision` driven against a stdlib mock server through the **real** `SemanticsWorker`/`SemanticsRunner`.
**Prototype & evidence (this dir):** `w4c-remotevision.py` (adapter), `w4c-mockserver.py` (mock), `w4c-drive.py` (driver), `w4c-transcript.txt` (full run log), `w4c-run-summary.json`.
**Run command (from the scratch-clone root):**
`JEV_VISION_TOKEN=<sentinel> PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python w4c_drive.py`
**Result:** 73/73 checks pass, exit 0; loopback only (127.0.0.1 mock + one dead loopback port; no external host); existing semantics tests still green (`pytest tests/test_semantics_worker.py tests/test_semantics_integration.py tests/test_semantics_schema.py` → 22 passed in 6.7 s).

---

## 1. Audit findings — the surfaces M2 will touch

### 1.1 Config (`config.py`)

| Fact | Where |
|---|---|
| `VisionModelConfig`: `kind` fake\|local\|remote, `labels`, `endpoint=""`, `timeout_s=10.0`, `fixtures`. **No token/auth field** (matches proposal invariant line 302 "No secrets in config/artifacts; endpoint + token from env only"). | config.py:119-125 |
| The `semantics` section is parsed **strict** — unknown keys raise (so a `token` key is already rejected today; confirmed in transcript G). | config.py:244 (strict table 237-247), `_section` strict check 372-375 |
| Validation for a remote kind today: `kind` in the choices; `timeout_s > 0`. **Nothing else** — no endpoint scheme/host check, no timeout upper bound, no non-empty-labels requirement for real kinds, fixtures sub-checks run regardless of kind. | config.py:302 (kind), 303-304 (timeout), 305-312 (fixtures), whole `_validate_semantics` 298-350 |
| Demonstrated gaps (transcript G): endpoint `"://not-a-url at all"` loads fine; `timeout_s=3600.0` loads fine; `timeout_s=0` rejected with clean message. | w4c-transcript.txt:106-109 |
| `RoomConfig.to_dict()` / `save()` serialize the whole config **including `endpoint`** (asdict). Nothing in `run.py` calls `save()` today, but any consumer that does would persist whatever is in `endpoint` — including embedded creds if someone ever puts them in the URL. Serialized model keys: `['endpoint','fixtures','kind','labels','timeout_s']`. | config.py:359-362 (to_dict), 364-365 (save); transcript G line 110 |

### 1.2 Factory (`semantics.py`)

- `build_vision(cfg, homography)` raises for every non-fake kind with exact message:
  `vision model kind 'remote' is planned for M2; only 'fake' ships in M1` — verified for both `remote` and `local` (transcript F:102-103). This is the M2 entry point; it is reached from `run.py:265`, before the worker starts (`run.py:266`). | semantics.py:116-126
- `VisionModel` protocol is `name: str` + `infer(frame, *, labels=None) -> list[Detection]`. | semantics.py:64-68
- `Detection.bbox_px` is **full-res camera pixels** — a remote adapter that downscales must rescale back before returning (m2-design.md:340-342 states this contract). | semantics.py:57-61

### 1.3 Worker (`semantics.py:485-551`)

- One thread, `queue.Queue(maxsize=1)` in and out; results are **latest-wins** (a full out-queue drops the oldest). | semantics.py:496-497, 516-524
- `offer()` non-blocking; `poll()` non-blocking. | 502-508, 510-514
- `_run` catches **all `Exception`s** per pass: increments `errors`, stores `last_error = f"{type(e).__name__}: {e}"[:200]`, pushes an error `PassResult` — the thread never dies on an adapter failure. This is exactly the "degrade, never block" path a remote adapter plugs into. | 526-547, esp. 542-547
- A raising adapter is therefore **sufficient**: no retries, no special handling needed in the adapter. | 534 (infer call), 542-547

### 1.4 Runner guards (`semantics.py:554-655`)

- One in-flight pass (`_inflight`); further submits refused, counted `skipped["inflight"]`. | 577-579, 610-612
- No floor context / wrong frame resolution refusals (resolution once-per-run warning). | 580-593
- **Cooldown and budget are hard limits, honoured even for `force=True`** (comment at 594-595): `failure_cooldown_until` (default 10 s), `min_interval_s` (2 s), `max_passes_per_min` (4) via rolling 60 s window. | 594-598, 599-601, 602-606
- `poll()`: error result → `fail_cooldown_until = t + failure_cooldown_s`, returns `None`; fresh success → merge; result older than `max_age_s` (30 s) → `stale_dropped`, **no merge, no cooldown**. | 618-631, esp. 625-627, 628-630
- `stats()` exposes `model`, `passes`, `errors`, `last_error`, `stale_dropped`, `skipped`, `median_ms`. | 642-655

### 1.5 Artifacts (`_save_latest` / `_append_events`) — exactly what is serialized

- `{room, saved_at, map}` under `store_dir/<room>_latest.json`, atomic via `.tmp`+`os.replace`; failures swallowed (`except OSError: pass`). `map` = `snapshot()` → `SemanticMap(age_s, passes, model, objects, destination, diff)`; `model` is **`vision.name`**; each object = `SemanticObject(id, label, x, y, confidence, sources, plane_assumed, height_suspect, first_seen_s, last_seen_s, motion)`. | semantics.py:414-426, 394-405; scene.py:269-281, 294-308; observed in `w4c_latest.json` (run dir)
- `events.jsonl`: one line per diff — `{t, appeared, moved, vanished, labels}`. | semantics.py:428-442; observed format in store/events.jsonl
- **Leak surfaces to sanitize:** (a) `last_error` (already contains `type: repr(str(e))` up to 200 chars) flows into `stats()` → printed to stdout **and** written to `runs/summary_*.json` by the run loop; (b) `vision.name` is written into the artifact `model` field; (c) `endpoint` is serializable config. A token can only reach (a) if the adapter puts it in an exception message/URL, or (c) if it is embedded in `endpoint`. | semantics.py:544 (last_error), 650 (stats), 399-400 (model=name from FakeVision-style `name`); run.py:399-413 (summary incl. semantics stats at 408, written 411-412)

---

## 2. Prototype — design and divergences

`w4c-remotevision.py` implements the protocol with **stdlib only** (`urllib.request`, `json`, `base64`, `socket`; `cv2` only for JPEG encode, already a repo dep). Behavior:

- endpoint: `JEV_VISION_ENDPOINT` env wins, else constructor arg; empty → clean `RemoteVisionError` naming both sources (transcript A:6).
- token: `JEV_VISION_TOKEN` read **at call time**, sent only as `Authorization: Bearer` header; never stored, never logged; error text defensively scrubbed. (m2-design.md:343-346 chooses `auth_env` defaulting to `JEV_ROVER_VISION_TOKEN` — see divergence D3.)
- request: `{protocol:1, request_id, labels, image_b64, width, height, format:"jpeg", timeout_ms}`; JPEG encode on the **worker thread** (never the loop). A 1 MiB response read cap prevents an oversized-body DoS. | w4c-remotevision.py
- timeout: `urlopen(req, timeout=timeout_s)`; connect/read failures bubble as `RemoteVisionError("transport error: TimeoutError: timed out")` (transcript C:24).
- response handling: non-2xx → `HTTP nnn: reason`; invalid JSON → clean error; top-level shape must be `{"detections": [...]}` (raises otherwise); entries validated (label non-empty str, bbox 4 finite numbers, `x1>x0 ∧ y1>y0`, within sent image ±8 px, score finite/clamped [0,1]); bad entries skipped and counted (`last_skipped`); **if every entry is invalid and the list was non-empty, it raises** (fail loud, not a silent empty pass). Truncation at `MAX_DETECTIONS` (semantics.py:50).

**Divergences to reconcile in M2** (flagged for the owner):

- **D1 — request schema:** flat `image_b64` (this task's brief) vs the nested `image{format,b64,width,height,scale}` of m2-design.md:317-327. Adopt the nested form when `input_scale`/`jpeg_max_px` land, because the bbox rescale contract needs `scale`.
- **D2 — all-invalid entries:** raise (here) vs "malformed entries skipped and counted" (m2-design.md:348). Recommendation stands: raise when the list was non-empty and nothing survived; otherwise a server bug looks like an empty room.
- **D3 — env names:** `JEV_VISION_ENDPOINT`/`JEV_VISION_TOKEN` (brief) vs `JEV_ROVER_VISION_ENDPOINT` (m2-design.md:346) / `JEV_ROVER_VISION_TOKEN` (m2-design.md:401). Align on the m2-design names; keep the `auth_env` indirection.
- **D4 — response has no protocol echo** (m2-design.md:328-335): add `protocol` to the response and reject mismatches (see A1).

## 3. Transcript evidence (driving the real worker/runner)

All scenario times are synthetic runner clocks; the worker thread is real. Full log: `w4c-transcript.txt`.

- **Healthy path:** mock detections project + merge end-to-end through the real store (2 objects, artifacts written) — transcript:11-13.
- **Timeout (server sleeps 0.6 s, `timeout_s=0.25`):** submit returned in 0.1 ms while slow; a second submit was refused one-in-flight; 60×`poll()+snapshot()` during the in-flight pass cost 1.1 ms total (control loop never blocked); worker recorded `RemoteVisionError: transport error: TimeoutError: timed out`; `poll()` consumed the error as `None`, cooldown armed at `t+5`, blocked the next submit, worker thread alive; following pass after cooldown merged (passes=2) — transcript:16-26.
- **500 / malformed JSON / wrong schema / all-invalid entry / connection-refused** each: clean error string in `last_error` (`HTTP 500: Internal Server Error` / `malformed JSON: JSONDecodeError` / `schema: expected {'detections': [...]}` / `all 1 detection entries malformed` / `transport error: ConnectionRefusedError: [Errno 111] Connection refused`), cooldown armed and blocking, thread alive, following pass succeeds — 6 consecutive failures then success, `passes=7 errors=6 started=13`, `skipped={cooldown:6, inflight:1, ...}` — transcript:29-99.
- **M2 entry point:** `build_vision('remote')` and `('local')` raise the exact M1 NotImplementedError — transcript:101-103.
- **Config:** bogus endpoint accepted at load (gap), `token` key rejected by strict parsing (`w4c.semantics.model: unknown key(s): token`), `timeout_s=0` rejected / `3600` accepted (no upper bound = gap), serialized model has no token field — transcript:105-110.
- **Token hygiene:** 14 mock requests, every one carried a `Bearer` header (value withheld; sha256 prefix `3fb6ece4` printed), bodies were `application/json`, and an in-driver + external `grep -rF` of the transcript, store artifacts and summary found **zero** occurrences of the sentinel token — transcript:112-115; external grep: "NO MATCHES (clean)".

## 4. Threat model — failure mode × mitigation × gap × recommendation

| # | Failure mode | Current mitigation (PR tip) | Gap | Recommendation for M2 |
|---|---|---|---|---|
| 1 | Endpoint unreachable / refused / DNS fails | Worker blanket except → error result → runner cooldown; demonstrated | None functionally; host:port text is benign, but message must be scrubbed of creds if URL ever contains them | Keep; forbid creds in URL (V5); keep `_scrub` |
| 2 | Server hangs | `urllib` `timeout_s`; cooldown | Only `timeout_s > 0` validated (config.py:303-304); 3600 s accepted; not bounded vs `max_age_s` (30 s) | Validate `0 < timeout_s ≤ min(max_age_s, 60)` at load; test both bounds |
| 3 | HTTP 4xx/5xx (auth failure, 413) | `HTTPError` → clean error; cooldown throttles to 1 call per cooldown window | No written no-retry policy on the M1 surface | Keep no-retry (m2-design.md:347-350 agrees); test that one pass = exactly one POST |
| 4 | Malformed JSON body | Clean `RemoteVisionError`; degrade path | None | Keep; add regression test |
| 5 | Wrong top-level schema | Prototype raises `schema: expected {'detections': [...]}` | m2-design §2.4 does not state the top-level requirement | Require `detections` list; test |
| 6 | Junk entries / degenerate bboxes | Skips + counts; bbox sanity vs sent image; project_detections clamps again (semantics.py:248-251) and drops off-floor | Silent empty pass if adapter returns `[]` for all-invalid | Raise when all entries invalid (D2); surface skip count in stats |
| 7 | Oversized response / frame (DoS) | Prototype: 1 MiB read cap; frame encode cap; `MAX_DETECTIONS=64` (semantics.py:50) | Not in m2-design; no request cap other than planned `jpeg_max_px` (m2-design.md:400) | Codify caps as constants + tests; server contract: 413 beyond cap |
| 8 | **Secret leakage** into artifacts/logs | No token config field (config.py:119-125); strict section rejects unknown keys (config.py:244; demo); token header-only; scrubbed errors | `endpoint` is serialized by `to_dict/save` (config.py:359-365); `last_error` → `stats()` → `runs/summary_*.json` + stdout (semantics.py:544,650; run.py:399-413); `vision.name` lands in artifact `model` (semantics.py:399-400) | Endpoint scheme/host validation (reject userinfo/`?token=`); static `name` convention; sentinel-token hygiene test (A6); scrub at adapter boundary |
| 9 | Eavesdrop on the wire (token, camera frames) | None (loopback prototype only; m2-design example endpoint is `http://192.168.1.20:8080`, m2-design.md:434) | No TLS assumption stated anywhere | State it: https required, plaintext only for loopback / explicitly opted-in LAN (`allow_plaintext_lan`); frames leave the host — treat as privacy-sensitive |
| 10 | Proxy env vars (`http_proxy`) hijack the call | None | `urllib.request.urlopen` honours environment proxies by default | Build a no-proxy opener for the vision endpoint (or require `no_proxy`); note in docs |
| 11 | Misconfiguration (remote kind, empty endpoint) | Adapter raises naming env+config (transcript A:6); build_vision raises at M1 (semantics.py:116-126) | Config load accepts `remote` + empty endpoint (no check in 298-350); m2-design defers to a build_vision warn (m2-design.md:410-411) | Warn/fail at `build_vision` naming both sources; keep config hermetic for CI |
| 12 | Cost/rate blow-up | `max_passes_per_min=4` (config.py:141) enforced even for `force` (semantics.py:602-606); one in-flight (577-579); min interval (599-601) | Remote cost proxies not surfaced per-pass | Keep defaults; assert ≤ N POSTs/min in test (A5); stats already expose errors/latency |
| 13 | Stale/dropped results | `max_age_s` drop (628-630) | A slow-but-under-timeout pass past `max_age_s` merges nothing **and does not arm cooldown** | Document; optionally count stale long-remote passes as soft failures |
| 14 | Schema/model drift | None | Request has `protocol:1` (m2-design.md:321) but response does not echo it (328-335) | A1: response echoes `protocol`; adapter rejects mismatch; record response `model`/`ms` in `PassResult` (m2-design.md:464) |

## 5. Concrete M2 acceptance-criteria additions (proposal line 310 / budget guards line 189)

- **A1 Schema versioning + echo.** Request `protocol=1`; response must carry `protocol`; mismatch → `RemoteVisionError`. Mock test both directions. Also record the response `model` string (sanitized) in `PassResult.model` or a new field.
- **A2 Size caps.** Adapter: response byte cap (1 MiB) + raise; request frame cap (px) before encode; keep `MAX_DETECTIONS=64` (semantics.py:50). Tests with oversized mock bodies and a 64+ detections response.
- **A3 TLS assumption + plaintext policy.** Endpoint must be `https://` unless host is loopback or `allow_plaintext_lan: true`; validate scheme/host at load (reject `user:pass@`, query strings); token sent only over an allowed transport.
- **A4 Retry-vs-cooldown policy.** Exactly one HTTP attempt per pass; all failures go to the runner cooldown (semantics.py:625-627); test counts mock requests == passes offered.
- **A5 Budget enforcement test.** With `max_passes_per_min=2`, a simulated minute produces ≤ 2 POSTs at the mock (pattern: tests/test_semantics_worker.py:99-108).
- **A6 Secret hygiene test (mandatory).** With `JEV_ROVER_VISION_TOKEN=<sentinel>`: run a full mock pass; assert the sentinel absent from `*_latest.json`, `events.jsonl`, `stats()` dump, `last_error`, and exception `str()`; assert the request URL contains no sentinel; assert a `token` config key is a `ConfigError` (strict section, config.py:244).
- **A7 Failure-isolation regression suite.** Port the w4c driver scenarios (timeout / 500 / malformed / schema / all-invalid / refused) to pytest against the loopback mock with tiny sleeps; assert: clean error, thread alive, cooldown blocks, next pass merges. This is the M2 analogue of tests/test_semantics_worker.py:111-121.
- **A8 Validation bounds.** `0 < timeout_s ≤ min(max_age_s, 60)`; `labels` non-empty for real kinds; endpoint scheme/host checks; error messages name both the config key and the env var (m2-design.md:346).
- **A9 Name hygiene.** `RemoteVision.name` must be a static constant (it is serialized into artifacts as `map.model`, semantics.py:399-400 → 417-421); add a test asserting no `://` in `name`.

## 6. What was not validated / limitations

- Only the stdlib mock server was tested (loopback HTTP, HTTP/1.1). No TLS, no proxies, no redirects, no chunked/streaming responses, no IPv6 — those remain untested paths (`urllib` follows redirects up to 10 by default; a cross-host redirect would resend the token — worth forbidding explicitly).
- The timeout measured is client-side only; the mock's handler thread tolerates the client hang-up (BrokenPipe swallowed) — real servers should too, but that's server-side.
- Worker resilience was demonstrated across 6 sequential failures + recoveries and a 14-request run; no soak test (hundreds of errors) was run.
- JPEG encode cost on full-res frames was not profiled (the test frames are 160×120); the encode happens on the worker thread by construction.
- Full repo suite not re-run (time-boxed to the 3 semantics test files: 22 passed); no repo files were modified — the scratch clone carries only untracked prototype files (`git status`: `?? w4c_drive.py`, `?? w4c_mockserver.py`, `?? w4c_remotevision.py`, `?? w4c_out/`).
- The report's line numbers are PR-tip (`bec1d91`); master HEAD `9c33ec0` may shift config/semantics lines slightly.