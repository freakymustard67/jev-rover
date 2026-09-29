#!/usr/bin/env python3
"""Parse PR #1 + refs state for cron pass #5 (read-only)."""
import json, subprocess, pathlib, urllib.request

RUN = pathlib.Path("/home/freakymustard/jev-rover-research/runs/20260929-0118")
RUN.mkdir(parents=True, exist_ok=True)

def fetch(url, out):
    req = urllib.request.Request(url, headers={"User-Agent": "jev-cron", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    (RUN / out).write_bytes(data)
    return json.loads(data)

pr = fetch("https://api.github.com/repos/freakymustard67/jev-rover/pulls/1", "pr1.json")
print("PR state:", pr.get("state"), "merged:", pr.get("merged"), "mergeable_state:", pr.get("mergeable_state"))
print("commits:", pr.get("commits"), "changed_files:", pr.get("changed_files"))
print("head:", pr.get("head", {}).get("sha"), pr.get("head", {}).get("ref"))
print("updated:", pr.get("updated_at"), "created:", pr.get("created_at"))
print("comments:", pr.get("comments"), "review_comments:", pr.get("review_comments"))

ic = fetch("https://api.github.com/repos/freakymustard67/jev-rover/issues/1/comments?per_page=100", "pr1-comments.json")
print("issue comments:", len(ic))
for c in ic:
    print(" -", c["user"]["login"], c["created_at"], repr(c["body"][:120]))

rc = fetch("https://api.github.com/repos/freakymustard67/jev-rover/pulls/1/reviews?per_page=100", "pr1-reviews.json")
print("reviews:", len(rc))
for c in rc:
    print(" -", c["user"]["login"], c["state"], c.get("submitted_at"), repr((c.get("body") or "")[:120]))

refs = subprocess.run(["git", "-C", "/home/freakymustard/jev-rover", "ls-remote", "origin"],
                      capture_output=True, text=True, timeout=60)
(RUN / "refs.txt").write_text(refs.stdout)
print("--- refs ---")
print(refs.stdout.strip())
