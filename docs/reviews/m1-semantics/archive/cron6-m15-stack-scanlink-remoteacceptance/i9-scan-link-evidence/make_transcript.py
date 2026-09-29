#!/usr/bin/env python3
"""Combine pre/post crash-repro raw captures into one transcript with commands."""
import pathlib
import subprocess

here = pathlib.Path(__file__).resolve().parent
HDR = """\
=========================================================================
Binary scan-datagram crash: pre-fix RED / post-fix GREEN transcripts
PR tip before fix: bec1d91fe17682ce38d24e334c94c67fa4bcc685
Patched tree:      e1da1a4 (exports 0001/0002 .patch in this directory)
Python:            /home/freakymustard/jev-rover/.venv/bin/python (3.11.16)
Packet:            exact check_parser.py bytes (v6 §2.5):
  53 01 29 00 00 03 00 40 d3 2c 01 00   # msg=0x53 flags=0x01 scan_id=41
                                         # chunk 0/3 first=0 n=64, then 4 sample bytes
                                         # angle=0xD3=-45, range=0x012C=300, status=0
=========================================================================

--- PRE-FIX (commit bec1d91, unpatched link.py) -------------------------
$ PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \\
      crash_repro.py /home/freakymustard/.hermes/cache/scratch/c6/i9-repo
"""
post_cmd = """\

--- POST-FIX (commit e1da1a4) --------------------------------------------
$ PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \\
      crash_repro.py /home/freakymustard/.hermes/cache/scratch/c6/i9-repo
"""

pre = (here / "crash_repro_pre_raw.txt").read_text()
post = (here / "crash_repro_post_raw.txt").read_text()
out = HDR + pre + post_cmd + post
(here / "crash_repro_transcript.txt").write_text(out)
print(out)
