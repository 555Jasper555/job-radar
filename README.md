# job-radar

Aggregates every AI / agentic / automation engineer job posting reachable without a key or a
browser, dedupes them, checks each one is still open, scores fit for Jasper, and exports a
ranked board.

```
python run.py                       # scrape all sources -> merge -> verify -> score -> export
python run.py --skip-sources        # re-run the pipeline on the existing raw/ files
python run.py --only hn,ats         # just those sources, then the pipeline
```

| Stage | Script | Output (in `C:/Users/endle/MyStuff/_job-radar-data`) |
|---|---|---|
| scrape | `sources/<name>.py` | `raw/<name>.jsonl` (one row per posting, see `SCHEMA.md`) |
| merge | `pipeline/merge.py` | `merged.jsonl` (URL + company/title dedupe, sources unioned) |
| verify | `pipeline/verify.py` | `verified.jsonl` (`verify.status` open / closed / unknown) |
| score | `pipeline/score.py` | `scored.jsonl` (0–100 fit with a `why` list) |
| export | `pipeline/export.py` | `out/jobs.csv`, `out/jobs.json`, `out/jobs.md` |

Sources: Hacker News hiring threads (Algolia), Greenhouse / Lever / Ashby / Workable /
SmartRecruiters boards, RemoteOK / Remotive / Himalayas / Jobicy / Arbeitnow / Working Nomads /
We Work Remotely / ai-jobs.net, LinkedIn guest search, SimplyHired, Built In, Dice, Reddit hiring
subs via Arctic Shift. Each source script documents what it could and could not reach.

Rules: headless only, keyless only, no paid scraping APIs, descriptive User-Agent, polite delays.
Nothing is ever fabricated — every row comes from a fetched response and keeps its source URL.
