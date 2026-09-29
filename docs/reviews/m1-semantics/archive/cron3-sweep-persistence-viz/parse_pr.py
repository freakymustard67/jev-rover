import json, pathlib
d = pathlib.Path("/home/freakymustard/jev-rover-research/runs/20260928-2220")
pr = json.loads((d / "pr1.json").read_text())
print("PR:", {k: pr.get(k) for k in ["state", "merged", "mergeable", "mergeable_state", "updated_at", "commits", "changed_files", "title"]})
cs = json.loads((d / "pr1-comments.json").read_text())
print("comments:", len(cs))
for c in cs:
    print("-", c["user"]["login"], c["created_at"], repr(c["body"][:300]))
rs = json.loads((d / "pr1-reviews.json").read_text())
print("reviews:", len(rs))
for r in rs:
    print("-", r["user"]["login"], r["state"], r["submitted_at"])