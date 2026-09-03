# hn.py — Hacker News hiring threads

Keyless Algolia HN API. Discovers this month's and last month's `Ask HN: Who is hiring?`
and `Ask HN: Freelancer? Seeking freelancer?` threads, fetches each full comment tree
(cached at `_job-radar-data/raw/hn-items/<id>.json`, so reruns refetch nothing), parses
the `Company | Role | Location | REMOTE | Full-time | $salary | url` header of every
top-level comment, and emits one row per role that passes `common.is_fit`.

    set PYTHONIOENCODING=utf-8
    python sources/hn.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/hn.jsonl

`HN_MONTHS=3` widens the window. Delete a cached item JSON to pick up new comments.

## Gotchas

- The freelancer thread is no longer posted by the `whoishiring` bot. Since Nov 2025 a
  human (`jon_north`) posts it, so it is found by title, not author. Both 2026-08 and
  2026-09 freelancer threads contained only `SEEKING WORK` (job seekers) and produced
  zero rows; the `SEEKING FREELANCER` parse is in place for when one appears.
- `search_by_date` silently ignores date ordering when a `query` is present — filter
  with `numericFilters=created_at_i>...` instead of trusting hit order.
- Header conventions are loose: posters omit the company, run prose into the header line,
  or put pipes inside parentheses. Splitting ignores bracketed pipes, fields over 90
  chars are treated as body prose, and a missing role is recovered from the body near
  hiring language. A comment with no stated role is dropped rather than guessed at.
- Job seekers post in the hiring threads too; they are filtered on the resume template
  (`Willing to relocate:` / `Résumé:` + `Remote:`) and first-person openers.
- Companies repost the same job monthly; identical company+title keeps the newest only.
