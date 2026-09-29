# I8 — M2 RemoteVision hardening / acceptance pack (A1–A9)

**Date:** 2026-09-29 · **Base:** PR #1 branch `review/m1-semantics-audit`, tip `bec1d91`
**Work clone:** `/home/freakymustard/.hermes/cache/scratch/c6/i8-repo` (branch `i8-remote-acceptance`, commit `d8f477b`)
**Patch:** `i8-remote-acceptance-evidence/0001-semantics-M2-RemoteVision-reference-adapter-A1-A9-ac.patch`
(1226 insertions, 2 new files, `git am` verified on a fresh GitHub clone of `bec1d91` → `f8dd6ac`)
**Work spec:** `runs/20260928-2057/w4c-remotevision-threat.md` §3 (prototype), §5 (A1–A9), §6 (limits)

## Headline

| # | item | verdict | evidence |
|---|---|---|---|
| **A6** | Secret hygiene (mandatory) | **PASS** — implemented + tested | sentinel absent from `*_latest.json`, `events.jsonl`, `stats()`, `last_error`, `str(exc)`; sent to mock only as `Bearer` header; `token` config key → `ConfigError` |
| **A1** | Protocol echo + model/ms | **PASS** — implemented + tested | echo required; mismatch/missing/bool → `RemoteVisionError`; `last_model`/`last_ms`/`last_protocol` recorded + sanitized |
| **A7** | Failure isolation (6 scenarios) | **PASS** — implemented + tested | timeout/500/malformed/schema/all-invalid/refused: clean error, thread alive, cooldown blocks, next pass merges; 6-consecutive-failure run `passes=7 errors=6 started=13` |
| **A2** | Size caps | **PASS** — implemented + tested | 1 MiB response cap (`+1` read ⇒ over-cap raises, exactly-at-cap accepted), frame-px cap checked **before** `cv2.imencode`, 65 detections → 64 |
| **A4** | One attempt per pass | **PASS** — implemented + tested | 5 failing passes ⇒ exactly 5 POSTs at the mock; 3 healthy passes ⇒ 3 POSTs |
| **A9** | Name hygiene | **PASS** — implemented + tested | `RemoteVision.name == "remote-vision-v1"` (class constant, no `://`), appears as `map.model` in the artifact |
| **A8** | Validation bounds | **PASS** — implemented + tested | `0 < timeout_s ≤ min(max_age_s,60)`; labels required; endpoint checks; errors name config key **and** env var |
| **A3** | Plaintext policy | **PASS** — implemented + tested (TLS handshake untested) | https OK; http allowed only loopback / `allow_plaintext_lan:true`; userinfo + query rejected; redirects **not** followed; env proxies ignored |
| **A5** | Budget enforcement | **PASS** — implemented + tested | `max_passes_per_min=2` over a simulated minute (120 submits @0.5 s): exactly 2 POSTs, `skipped["budget"]==118` |

Suite count: **baseline 80 passed → 123 passed** on the patched tree (80 + **43** new).
No existing file was modified by the pack — the M1 80/80 stays green (`pytest-full-suite.txt`).

## What shipped

**`remotevision.py`** (446 lines, stdlib + `cv2`/`numpy`, no new deps):
- constants `ENDPOINT_ENV`/`TOKEN_ENV`/`PROTOCOL=1`/`NAME` (:81-88), `MAX_RESPONSE_BYTES=1 MiB` (:85),
  `MAX_FRAME_PX=8_000_000` (:86), `TIMEOUT_CAP_S=60` (:87);
- `_scrub` (:99) — raw + URL-quoted + base64 token forms (≥4 chars) removed from error text;
- `_is_loopback` (:119) — `localhost` + `ipaddress.is_loopback`;
- `validate_endpoint` (:132) — scheme http/https, no userinfo, no query/fragment, host required,
  plaintext only loopback or opt-in; messages name `semantics.model.endpoint` **and** `JEV_ROVER_VISION_ENDPOINT`;
- `validate_remote_settings` (:179) — `0 < timeout_s ≤ min(max_age_s, 60)`, labels non-empty for real kinds,
  `jpeg_quality ∈ [1,100]`; public so M2's `config._validate_semantics` can call it at load;
- `resolve_endpoint` (:216) — env wins, else constructor arg; empty → error naming both;
- `_NoRedirect` (:228) + `build_opener` (:235) — redirects refused (a cross-host 30x would resend the
  token, threat #9), env proxies ignored (threat #10);
- sanitizers `_clean_model`/`_clean_ms` (:248/:256), entry validator `_parse_entry` (:265),
  px-cap-then-encode `_encode_jpeg` (:292);
- `RemoteVision` (:303) with `name = NAME` (:306, class constant), `telemetry()` (:338),
  `infer`/`_infer_once` (:345/:355): one POST per pass, protocol echo required (:423-428),
  response cap (:412), `MAX_DETECTIONS` truncation (:433), all-invalid → raise (:445).

**`tests/test_remotevision_acceptance.py`** (780 lines, 43 tests): self-contained
`ThreadingHTTPServer` mock on `127.0.0.1:0` with modes `ok / slow / http500 / malformed / schema /
allinvalid / protocol2 / noproto / proto_bool / hostilemodel / many65 / toobig / atcap / redirect`,
driving the **real** `SemanticsWorker`/`SemanticsRunner`. Per-A-item counts:
A1 6 · A2 6 · A3 5 · A4 2 · A5 1 · A6 4 · A7 8 · A8 8 · A9 1 · extra 2 (proxy, sentinel-scanner sanity).

## Exact commands (all in the work clone / verify clone)

```bash
# baseline (prtip, unpatched)
cd <clone> && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  -m pytest -q -p no:cacheprovider            # 80 passed in 15.54s

# acceptance only (43 tests)
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  -m pytest -q -p no:cacheprovider tests/test_remotevision_acceptance.py   # 43 passed in 15.88s

# full patched tree
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  -m pytest -q -p no:cacheprovider            # 123 passed in 30.56s

# independent verification: fresh GitHub clone of the PR branch + git am + full suite
git clone https://github.com/freakymustard67/jev-rover i8-verify2 && cd i8-verify2
git fetch origin review/m1-semantics-audit:prtip && git checkout prtip   # bec1d91
git am .../0001-semantics-M2-RemoteVision-reference-adapter-A1-A9-ac.patch  # -> f8dd6ac
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  -m pytest -q -p no:cacheprovider            # 123 passed in 30.05s (verify-clone transcript)
```

Measured: acceptance 43/43 passed in 15.88 s (dev clone) and 15.90 s (verify clone) — two independent
runs, no flakes; full suite 123 passed in 30.56 s (dev) / 30.05 s (verify).

## Per-A-item evidence detail

- **A6** — `test_a6_sentinel_absent_from_artifacts_stats_and_errors` runs a healthy pass **and** an
  HTTP-500 pass through the real runner with `JEV_ROVER_VISION_TOKEN=s3ntinel-…9f1c`, then scans
  `remoteacc_latest.json`, `events.jsonl`, `json.dumps(stats())`, `last_error`, `telemetry()`:
  zero hits (the mock simultaneously proves the header was present on every request).
  `test_a6_token_rides_only_in_the_authorization_header` asserts `Bearer <sentinel>` on the wire,
  sentinel absent from `path`, from every other header and from the request body.
  `test_a6_sentinel_never_in_error_text_or_exception` covers refused + hostile endpoints
  (`?access_token=…`, `user:<sentinel>@`) — rejected, never echoed. `test_a6_config_token_key_is_rejected`:
  `RoomConfig.from_dict(...)` → `ConfigError: …semantics.model: unknown key(s): token` (config.py:375);
  serialized model keys contain no `token` (config.py:359-365).
  Only in-source occurrence of the sentinel in the whole tree is the test file itself (grep -rlF).
- **A1** — request body asserted field-by-field (protocol/request_id/labels/image_b64 JPEG magic
  `"/9j/"`/width/height/format/timeout_ms); `protocol2`, `noproto`, `proto_bool` all raise and leave
  `last_protocol is None`; host `model` string is stripped of control chars and capped at 64 chars;
  the runner-level test proves telemetry is recorded after a real merged pass.
- **A7** — six params (`ids=timeout,http-500,malformed-json,wrong-schema,all-invalid,connection-refused`),
  each asserting: `RemoteVisionError:` prefix + scenario needle in `last_error`,
  `fail_cooldown_until == tb+5.0`, next submit refused with `skipped["cooldown"]==1`,
  `worker._thread.is_alive()`, `_inflight is None`, and a following pass after the cooldown merging
  2 objects. `test_a7_six_consecutive_failures_keep_worker_healthy` mirrors w4c driver §D/E:
  `passes=7 errors=6 passes_started=13 skipped{cooldown}=6`, thread alive.
  `test_a7_slow_server_never_blocks_the_control_loop`: submit < 50 ms, 60×`poll()+snapshot()`
  < 200 ms while the slow pass is in flight.
- **A2** — over-cap body (1 MiB + 1) raises naming the cap; a body of **exactly** 1 MiB is accepted
  (proves the `read(cap+1)` boundary); frame-px cap proven *before* encode by monkeypatching
  `cv2.imencode` to a bomb — it is never called and the mock sees nothing; 65-entry response →
  `len(dets)==64`, `last_truncated==1`, and a worker-level pass stays error-free.
- **A4** — request count at the mock == accepted passes in both failing and healthy modes.
- **A5** — 120 submits over a synthetic minute → 2 POSTs (budget is honoured even for `force=True`,
  semantics.py:602-606).
- **A9** — static class constant, no per-instance override (`"name" not in vars(rv)`), matches
  `[a-z0-9][a-z0-9._-]*`, and equals `map.model` in the written artifact (semantics.py:541/547 →
  `_save_latest` :417-421).
- **A8** — bounds table (0.0/‑1/inf/30.0/60.0/60.01 with `max_age_s` 30 & 600), labels-missing raises
  naming `semantics.model.labels`, explicit empty `labels=[]` at call time raises, 5 endpoint forms
  rejected with both-source messages, missing-endpoint error names both sources.
- **A3** — LAN plaintext refused at construction (no transport attempted); allowed with
  `allow_plaintext_lan=True` (token attached, path-only URL); loopback plaintext default-allowed
  (live mock); https accepted + token attached via injected transport (TLS handshake itself untested);
  302 redirect → `HTTP 302`, redirect target receives **0** requests; `http_proxy` pointed at a live
  loopback listener + `proxy_bypass` forced False → request still goes direct, proxy sees nothing.

## Decisions & uncertainties

1. **Patch is two new files only.** Wiring into `build_vision`/`config.py`/`PassResult` is deliberately
   *documented* in the module docstring (three exact call sites) rather than done here, so the M1
   suite keeps passing (80/80) and M2 owns the integration diff. `validate_remote_settings` and
   `resolve_endpoint` are public for that purpose.
2. **Env names follow m2-design (D3):** `JEV_ROVER_VISION_ENDPOINT` / `JEV_ROVER_VISION_TOKEN`
   (m2-design.md:346/401), not the w4c prototype's shorter names.
3. **Request schema is the flat `image_b64` form** of this brief (D1); the nested
   `image{format,b64,width,height,scale}` + `input_scale`/`jpeg_max_px` land with the M2 bbox-rescale
   work. Flagged as a known divergence.
4. **All-invalid entries raise** (D2, threat #6) instead of returning an empty room.
5. **Protocol echo is mandatory** (A1): this *breaks* servers that omit it — including the w4c mock,
   which is why the acceptance mock always echoes `protocol`.
6. **`name` has no constructor override** (strictest A9 reading): `RemoteVision.name` is a class
   constant so nothing per-instance can reach the artifact `model` field.
7. **Tokenizer scrub guard:** forms shorter than 4 chars are not scrubbed (they would mangle text);
   a real token does not have this property. Documented in `_scrub`.
8. **`allow_plaintext_lan:true` also allows the token over that plaintext transport** — that is the
   point of the LAN use case (m2-design's example endpoint is `http://192.168.1.20:8080`,
   m2-design.md:434). Use https when the token matters; the error message says exactly that.
9. **Uncertainty:** the HTTPS path is validation + header-policy tested only (injected transport);
   no TLS handshake, no certificate handling was exercised. Similarly the redirect/proxy tests prove
   *our* behaviour (refuse/ignore), not interop with a real proxy/redirecting vendor.

## Limitations (uncovered paths — do not claim more than this)

- **TLS** never exercised (no https listener); certificate/verification errors are untested.
- **IPv6** loopback (`::1`) accepted by the validator (`ipaddress`) but no live IPv6 mock was bound.
- **Proxies**: only "environment proxy is ignored" is proven; real proxy interop is out of scope.
- **Redirects**: only "not followed" is proven (single 302 case; 301/303/307/308 not parametrized).
- **Soak/shape variety**: max 6 consecutive failures per runner (not hundreds); no chunked/streaming
  bodies, no slow-body-only stall, no keep-alive reuse test; mock is HTTP/1.1 with `Content-Length`.
- **`_parse_entry` bbox tolerance** is ±8 px against the *sent* image; the `input_scale` rescale
  contract (m2-design.md:340-342) is not implemented here (scale is always 1.0).
- Python 3.11 only (repo venv); no Windows/macOS runner.
- The pack does not test `run.py` end-to-end with a remote kind (M1 `build_vision` still raises —
  covered by the w4c report, transcript §F).

## How to re-run at M2 integration time

```bash
bash i8-remote-acceptance-evidence/rerun-acceptance.sh /path/to/m2-worktree
# expected: acceptance file 43 passed; full suite 80 + 43 = 123 passed
# (also: `git apply --3way 0001-*.patch` if M2 already edited config.py/semantics.py;
#  the pack only adds two files, so conflicts are unlikely and trivial)
```
Review checklist for the M2 owner: (a) `build_vision` constructs `RemoteVision` with
`cfg.model.labels`/`timeout_s`/`allow_plaintext_lan`; (b) `PassResult` carries `last_model`/`last_ms`;
(c) `config._validate_semantics` calls `validate_remote_settings` for real kinds; (d) the response
schema in the final m2-design states the mandatory `protocol` echo; (e) decide the D1 nested-image
schema before shipping `input_scale`.

## Artifacts (sha256)

| file | sha256 |
|---|---|
| `0001-semantics-M2-RemoteVision-reference-adapter-A1-A9-ac.patch` | `20ec59049142df0cd568e2ed5ee89f2bd117fc5f90da279d8f02ad2126ae2c7d` |
| `remotevision.py` (work clone; == patch content) | `fc13ef0efa0cb65dcad41d45f9fd7a6c7d0a49c2b3ee0047678c3499de703ae4` |
| `tests/test_remotevision_acceptance.py` | `36f1a5e787f457ccd807f48c35525a2770d3fe750001246a759ccdc31e3a8b8c` |
| `pytest-full-suite.txt` (123 passed, dev clone) | `2dca9cf31de41c429af3c77397eed1b7f5bd66f17a080887e11b46a5e282104e` |
| `pytest-full-suite-verifyclone.txt` (123 passed, fresh clone + am) | `70ea331653ff8d55e2560392f6516669848cc32b5edeca656dad4ea383d3187d` |
| `pytest-remotevision-acceptance.txt` (43 passed) | `b6011c105ae49c1bf3261e2b33323c7a5929af550ea199b64cc54a831f4e71c2` |
| `pytest-remotevision-acceptance-verifyclone.txt` (43 passed) | `327106817169a647cfe039636b39c5ecd12ed73b24850dfa3c4dc346a6121b52` |
| `pytest-collected-ids.txt` (43 test ids by A-item) | `d511b9a47be5c1dcf706cb057053f619493bb6aac582cbafd78a3c4b5d385a4b` |
| `rerun-acceptance.sh` | `3a19e8669dd9045cdfec93354790a46acfe1ed22229e2c132a435e4c22891841` |

Read-only guarantees: nothing was written to `/home/freakymustard/jev-rover` (clean tree, HEAD
`9c33ec0`) or `/tmp/opencode`; all runs used the scratch clones under
`/home/freakymustard/.hermes/cache/scratch/c6/` and `--basetemp` inside it.
