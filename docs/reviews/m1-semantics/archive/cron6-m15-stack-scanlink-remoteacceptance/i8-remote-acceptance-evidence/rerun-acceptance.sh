#!/usr/bin/env bash
# Re-run the M2 RemoteVision acceptance pack (A1-A9) against a jev-rover tree.
#
# Usage:
#   bash rerun-acceptance.sh /path/to/jev-rover          # apply patch, then run
#   PATCH=0 bash rerun-acceptance.sh /path/to/jev-rover  # tree already patched
#   PY=/path/to/python bash rerun-acceptance.sh ...      # non-default interpreter
#
# Notes for M2 integration time:
#  * The patch is a clean `git am` on PR-tip bec1d91 (verified in a fresh clone).
#    If M2 has already touched build_vision/config.py/semantics.py, apply with
#    `git apply --3way 0001-*.patch` and expect a trivial conflict at worst:
#    the pack only *adds* remotevision.py and tests/test_remotevision_acceptance.py.
#  * Expected counts: acceptance file 43 passed; full suite 80 (M1 baseline) + 43.
#  * Loopback only (127.0.0.1 + one dead loopback port). No TLS/proxy/IPv6 paths
#    are exercised by design; see i8-remote-acceptance.md "Limitations".
set -euo pipefail

TREE=${1:?usage: rerun-acceptance.sh /path/to/jev-rover}
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-"$TREE/.venv/bin/python"}
PATCH=${PATCH:-1}

cd "$TREE"
if [ "$PATCH" = "1" ]; then
  git am "$HERE/0001-semantics-M2-RemoteVision-reference-adapter-A1-A9-ac.patch" \
    || { echo "git am failed - retry: git apply --3way $HERE/0001-*.patch" >&2; exit 1; }
fi
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_remotevision_acceptance.py
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider
