# gig-scout — posted-budget work, scanned unattended

The rest of job-radar hunts salaried roles. This folder hunts the other shape of demand: a
stranger who already wrote down a budget for a task Jasper can ship in days. The metric is not
"opportunities found"; it is **qualified applications sent within a few hours of the post**.

```
python gigs/scout.py --days 3                 # first run: last 3 days, ~25 Arctic Shift requests, ~4 min
python gigs/scout.py --days 1                 # daily: only new posts go to new.jsonl / 🆕 on the board
python gigs/scout.py --days 3 --from-cache    # re-filter without spending API budget
powershell -File gigs/daily.ps1 -Days 1       # scout + bounded `claude -p` drafts (the scheduled task)
```

Outputs in `C:/Users/endle/MyStuff/_job-radar-data/gigs/` (override `JOB_RADAR_DATA`):

| File | What |
|---|---|
| `board.md` | ranked, two sections: dollar figure in the post / pay words only |
| `gigs.jsonl` | every kept row (schema in `scout.py` docstring) |
| `new.jsonl` | rows not seen by a previous run — what `daily.ps1` drafts for |
| `drafts/<ts>.md` | paste-ready applications written by `claude -p` from `DRAFT-PROMPT.md` + `proof.md` |
| `seen.json`, `cache/`, `requests.log`, `logs/`, `state/` | bookkeeping |

## What counts as a row

Kept only when ALL hold: a hiring marker (`[Hiring]` / `[TASK]` in the title, or a
listings-only sub such as r/hiring, r/jobbit, or a contract/part-time/hourly comment in the
current HN "Who is hiring" thread) · pay language (a `$` figure → tier `explicit`; budget / rate /
hourly words without a figure → tier `stated`) · at least one of Jasper's lanes (app-build,
app-store, ai-automation, scrape-data, seo-local, content-deck, scripting) · not a self-advert,
not a question, not design/writing/video/tutoring work · not unpaid or equity-only.

Red flags (Telegram-only, "I will pay you first", gift cards, account rental, wire transfer)
subtract 25 points each and print ⚠ on the board; they are not dropped so a human can judge.

Sources: r/forhire, r/slavelabour, r/hiring, r/jobbit, r/freelance_forhire, r/Jobs4Bitcoins,
r/RemoteJobs, r/HireAnAppDeveloper (full recent pulls), r/ClaudeCode, r/ClaudeAI,
r/vibecoding, r/n8n, r/SaaS (`title=hiring` / `title=looking for` searches), HN Who is hiring.
Arctic Shift budget rules from `sources/reddit.py` apply: one sub per request, ≥10 s apart.

## The scheduled task

`gig-scout` (Windows Task Scheduler) runs `daily.ps1 -Days 1` hidden at 07:30, 13:30 and
19:30. It never sends anything. Jasper opens `drafts/`, pastes what he approves, and the only
number anyone tracks is sends. Register / inspect:

```
schtasks /query /tn gig-scout /v /fo list
schtasks /run /tn gig-scout
```

Why this exists: on 2026-09-03 the assistant handed Jasper five Reddit "help me" threads as
money opportunities. None had a budget. A question is not a purchase order; this scout only
surfaces posts where the money is already written down.
