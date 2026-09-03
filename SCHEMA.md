# job-radar — shared contract for every source scraper

Every source is a Python script at `sources/<name>.py`, runnable headlessly:

    set PYTHONIOENCODING=utf-8
    python sources/<name>.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/<name>.jsonl

No browser, no Chrome, no CDP, no paid API, no cookies. Keyless HTTP only. Descriptive
User-Agent: `job-radar/0.1 (personal job search; contact endlesslime9@gmail.com)`.
Use `sources/common.py` (requests session with retries, keyword filter, `record()`, `write_jsonl()`).

## One JSON object per line — fields

| field | type | notes |
|---|---|---|
| `id` | str | `<source>:<native id or sha1(url)[:12]>` |
| `source` | str | script name, e.g. `hn`, `greenhouse`, `remoteok`, `linkedin` |
| `title` | str | as posted |
| `company` | str | as posted |
| `url` | str | canonical public posting URL (the page a human opens) |
| `apply_url` | str/null | direct apply link if different |
| `location` | str | raw location text |
| `remote` | bool/null | true if remote is allowed |
| `region` | str | `worldwide` / `us` / `us-ca` / `eu` / `other` / `unknown` — best effort |
| `employment_type` | str | `full-time` / `contract` / `part-time` / `internship` / `unknown` |
| `salary` | str/null | raw salary text |
| `salary_min` / `salary_max` | int/null | annual USD if derivable, else null |
| `posted_at` | str/null | ISO date `YYYY-MM-DD`; null if unknown (say why in `notes`) |
| `description` | str | posting body, plain text / markdown, max 8000 chars |
| `tags` | list[str] | source tags / skills |
| `seniority` | str | `entry` / `mid` / `senior` / `staff` / `unknown` (from title words) |
| `open_status` | str | `open` if the source API only lists live jobs or the page said so; else `unverified` |
| `fetched_at` | str | ISO timestamp |
| `notes` | str/null | anything a human needs to know (e.g. "date from search snippet") |

## Fit keywords (title OR description) — `common.is_fit(title, description)`

Title hits (any): ai engineer, ai developer, ai automation, automation engineer, agentic,
ai agent, agent engineer, llm, applied ai, forward deployed, ai solutions, genai,
generative ai, ai product, ai operations, ai ops, prompt engineer, workflow automation,
ai integration, machine learning engineer, ai specialist, ai consultant, claude, n8n,
langchain, rag.
Plain "software engineer"/"full-stack"/"developer" titles count ONLY if the description
mentions llm / agents / claude / openai / gpt / langchain / rag / ai-native / copilot.

Keep everything that is a fit. Do NOT filter by seniority or location — the ranker does that.
Do filter out: sales, recruiter, marketing, "AI trainer" data-labeling gigs, foreign-language
speech-data collection, anything requiring a PhD in the title.

## Recency

Keep postings dated within 45 days (on or after 2026-07-19). If a source has no dates,
keep everything the source says is live and set `posted_at: null` + a note.
