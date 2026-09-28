#!/usr/bin/env bash
# Create the venv and install dependencies.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q pytest

echo
echo "Ready. Next:"
echo "  cp .env.example .env        # then paste your TypeSafe key"
echo "  .venv/bin/python -m pytest  # run the test suite"
echo "  .venv/bin/python run.py --config config/room.synthetic.json \\"
echo "      --source synthetic --mission patrol --seconds 30 --no-jev"
echo
echo "Real room:"
echo "  .venv/bin/python calibrate.py cameras"
echo "  .venv/bin/python calibrate.py tag --id 0 --size 0.15"
echo "  cp config/room.example.json config/room.json"
echo "  .venv/bin/python calibrate.py floor --config config/room.json"
