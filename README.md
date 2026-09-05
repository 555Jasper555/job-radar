# job-radar

Aggregates every AI / agentic / automation engineer job posting reachable without a key or a
browser, dedupes them, checks each one is still open, scores fit for Jasper, and exports a
ranked board.

**Status (2026-09-04):** in daily use for one person's job search. Two full runs so far —
8,354 raw → 6,627 unique postings (2026-09-02) and 9,757 raw → 7,411 unique (2026-09-03) —
from 16 keyless sources, each row verified open/closed and scored 0–100 with a `why` list
that explains the number. Built by [Jasper Liberti](https://jasper-decks.vercel.app/portfolio-dd366b/)
as one of the data-pipeline pieces behind a self-taught, agent-assisted engineering practice;
the rest of the work (retrieval + MCP server, LLM evaluation, client apps) is on the portfolio.

What is interesting in here if you are skimming:

- `sources/ziprecruiter.py` — the search pages sit behind a Cloudflare managed challenge; the
  site's own `llms.txt` documents an unchallenged directory surface and `Accept: text/markdown`
  job records. Reading the file the site publishes beat every impersonation trick.
- `pipeline/verify.py` — liveness checks per host, so a closed req never reaches the board.
- `pipeline/score.py` — an explainable scorer: every point has a reason string.
- `pipeline/tailor.py` — per-posting résumé coverage against a 281-term keyword map; ABSENT
  terms are listed and never auto-inserted (a human decides what is true).
- `sources/remote_boards.py` — a scraper that writes its file only at the end carries the
  previous run's rows for any board that failed, so one 429 cannot wipe a source.

Data lives outside the repo (`JOB_RADAR_DATA`); nothing personal is committed.

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
We Work Remotely / ai-jobs.net, LinkedIn guest search, SimplyHired, Built In, Dice, ZipRecruiter
(directory pages + `Accept: text/markdown` job records through curl_cffi Chrome impersonation —
its search path is Cloudflare-challenged, the sanctioned surface in its `llms.txt` is not), Reddit
hiring subs via Arctic Shift. Each source script documents what it could and could not reach.

Incremental runs: `JOB_RADAR_DAYS=1` narrows the LinkedIn search window to the last day;
`HTML_BOARDS_ONLY=linkedin` reruns one board and carries the others' rows forward.

Rules: headless only, keyless only, no paid scraping APIs, descriptive User-Agent, polite delays.
Nothing is ever fabricated — every row comes from a fetched response and keeps its source URL.

## Tailoring

```
python -m pipeline.tailor --resume resume.txt --jobs C:/Users/endle/MyStuff/_job-radar-data/scored.jsonl --keyword-map data/keyword-map.json --out C:/Users/endle/MyStuff/_job-radar-data/tailor.jsonl
```

`coverage_pct` is a direction signal for where a résumé's own wording is thin against what a posting actually asks for, not a ranking metric, and it is not comparable across postings with very different requirement-block lengths. Terms marked `status: ABSENT` are listed for awareness only and are NEVER auto-inserted into any résumé—a human decides what's true.
