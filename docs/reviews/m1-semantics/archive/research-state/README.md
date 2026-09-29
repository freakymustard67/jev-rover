# jev-rover research workspace

Hourly, automated, **read-only** verification & improvement over the jev-rover
semantics layer (plan: `~/jev-rover/docs/planning/`, milestones M1–M4). Seeded 2026-09-29; **paused 2026-09-29 (after six full passes)** — resume anytime via the Stop/adjust line below.

Each hour a fresh agent session:
1. checks the repo for new commits — if any, that run verifies the delta first;
2. otherwise takes the top items from `BACKLOG.md`;
3. dispatches 2–3 subagents for verification/improvement (scratch-clone experiments only);
4. archives their reports under `runs/`, updates the ledger;
5. posts a compact digest to the owner's Telegram chat.

Nothing in this pipeline ever modifies `~/jev-rover` or `/tmp/opencode`.

## Layout
- `LEDGER.md` — state + chronological log (read first; append-only, State block updated in place)
- `BACKLOG.md` — open verification/improvement topics (consume from the top)
- `round1-reports/` — the 8 deep-review reports from the first full pass (+ combined bundle, evidence/)
- `runs/` — per-run artifacts: `runs/<YYYYMMDD-HHMM>/REPORT.md` + task reports

## Ops notes
- **Disk-first rule:** every subagent writes its full report to `runs/...` before finishing —
  chat delivery has dropped before (recycled session); disk-first is mandatory.
- **Recovery:** dropped delegation results survive in `~/.hermes/state.db` → table
  `async_delegations` → column `result_json` (read-only query; delegation id prints at dispatch).
- **Tests:** `cd ~/jev-rover && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`
- **Web:** `web_search`/`web_extract` may 403; use `curl` (raw.githubusercontent.com,
  api.github.com, huggingface.co/api, export.arxiv.org, r.jina.ai) or the browser tool.

## Stop / adjust
Tell Hermes in the chat: “pause/stop the jev-rover hourly job”, “make it every 2 hours”, etc.
