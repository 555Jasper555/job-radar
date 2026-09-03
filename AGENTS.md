# job-radar

Aggregates, verifies and ranks AI/agentic/automation engineer job postings from every keyless source

- **Repo**: `C:\Users\endle\MyStuff\job-radar` · private `555Jasper555/job-radar`
- **Runs**: local
- **Data**: `C:\Users\endle\MyStuff\_job-radar-data` (outside the repo, via env `JOB_RADAR_DATA_DIR`)
- **Launcher**: `C:\Users\endle\MyStuff\harness\launchers\job-radar.cmd`

The agent harness (skills, rules, hooks, memory, research protocol, session handoff) is
global — `C:\Users\endle\MyStuff\harness\AGENTS.md`. This file holds only what is specific
to job-radar.

## Conventions

- Default branch `main`, feature work on `feat|fix/<slug>` in a linked worktree
  (Claude Code: `EnterWorktree`). The main checkout is read-only on `main`.
- Commit: `<type>(<scope>): <description>` + `Co-Authored-By: <agent>`.
- Never commit `.env*`, run outputs, or generated files.
- Data never lives in the repo: `C:\Users\endle\MyStuff\_job-radar-data` (outside the repo, via env `JOB_RADAR_DATA_DIR`).

## Verification gate (edit to match the stack)

```
npm run build && npx tsc --noEmit && npm run lint
```

## Notes

(architecture decisions, gotchas — keep this short; the vault holds the long form)
