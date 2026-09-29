#!/usr/bin/env bash
# W3B --trace with the fix applied. Usage: trace_fix_check.sh <clone_dir> <out_file>
set -u
PY=/home/freakymustard/jev-rover/.venv/bin/python
W3B=/home/freakymustard/.hermes/cache/scratch/wave3/w3b
CLONE="$1"
OUTFILE="$2"
cd "$CLONE" || exit 9
export PYTHONDONTWRITEBYTECODE=1
timeout 90 "$PY" run.py --config config/room.synthetic.json --source synthetic \
    --mission patrol --no-jev --seconds 4 --trace > "$W3B/out/$OUTFILE" 2>&1
echo "trace_fixed exit=$? (clone: $CLONE)"
