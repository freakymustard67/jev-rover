#!/usr/bin/env bash
# W3B --trace CLI run. Usage: trace_check.sh <pre|post> [outfile]
set -u
PHASE="$1"
W3B=/home/freakymustard/.hermes/cache/scratch/wave3/w3b
PY=/home/freakymustard/jev-rover/.venv/bin/python
O="$W3B/out/$PHASE"
cd "$W3B/clone" || exit 9
mkdir -p "$O"
export PYTHONDONTWRITEBYTECODE=1
timeout 90 "$PY" run.py --config config/room.synthetic.json --source synthetic \
    --mission patrol --no-jev --seconds 4 --trace > "$O/p5_trace.txt" 2>&1
echo "P5 exit=$?"
