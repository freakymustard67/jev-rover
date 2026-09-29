#!/usr/bin/env bash
# W3B: '--semantics fake' over a config that names kind=local.
# Usage: local_fake_check.sh <clone_dir> <out_file>
set -u
PY=/home/freakymustard/jev-rover/.venv/bin/python
W3B=/home/freakymustard/.hermes/cache/scratch/wave3/w3b
CLONE="$1"
OUTFILE="$2"
cd "$CLONE" || exit 9
export PYTHONDONTWRITEBYTECODE=1
timeout 90 "$PY" run.py --config "$W3B/out/local.json" --source synthetic \
    --mission patrol --no-jev --seconds 3 --semantics fake > "$W3B/out/$OUTFILE" 2>&1
echo "local_fake exit=$? (clone: $CLONE)"
