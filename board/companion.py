"""Write the vault companion .md for the board (LLM-readable text version of the page).

    python board/companion.py --jobs <jobs.json> --out "<vault md path>" --share <url> --top 40
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--share", required=True)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--time", default=datetime.now().strftime("%H:%M"))
    ap.add_argument("--refreshed", default=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    help="when this data refresh ran (the note keeps its 2026-09-02 identity)")
    ap.add_argument("--artifacts", default="Lead Data/2026-09-02 AI Job Radar/ai-job-radar-2026-09-02.csv",
                    help="vault path of this run's CSV")
    ap.add_argument("--notes", default="AI Job Radar — Run Notes",
                    help="basename of this run's vault run-notes note (wikilink target)")
    a = ap.parse_args()
    rows = json.load(open(a.jobs, encoding="utf-8"))
    n = len(rows)
    open_n = sum(1 for r in rows if r.get("verify") == "open")
    remote_n = sum(1 for r in rows if r.get("remote"))
    from datetime import timedelta
    fresh_cut = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    fresh = sum(1 for r in rows if (r.get("posted_at") or "") >= fresh_cut)
    b80 = sum(1 for r in rows if r["score"] >= 80)
    b65 = sum(1 for r in rows if r["score"] >= 65)
    src = Counter(s for r in rows for s in r["sources"])
    typ = Counter(r["employment_type"] for r in rows if r["score"] >= 65)
    lines = [
        "---", "date: 2026-09-02", f"time: {a.time}", "type: deliverable",
        "tags: [finance, consulting, automation]", "---", "",
        "# AI Job Radar — 2026-09-02", "",
        f"> [[AI Job Radar — 2026-09-02.html]] — right-click → Open in default app. Share URL (applied/skipped ticks persist per browser): {a.share}", "",
        "Every AI / agentic / automation engineering posting reachable without a login, pulled by the `job-radar` pipeline "
        "(`MyStuff/job-radar`, data in `MyStuff/_job-radar-data`), deduped across sources, open-checked, and scored 0–100 for Jasper's "
        "profile (self-taught AI automation engineer, Napa, remote or Bay Area, contract or full-time, entry to mid). "
        "This widens the eleven hand-found roles in [[Get Hired Week — Action Deck 2026-09-02]] to the whole reachable market; "
        "the money plan itself is unchanged and lives in [[Money War Room — 2026-09-02]]. Index: [[Home]].", "",
        "## Numbers on the page", "",
        "| Metric | Count |", "|---|---|",
        f"| Postings on the board (score ≥ 35) | {n:,} |",
        f"| Verified open at build time | {open_n:,} |",
        f"| Remote | {remote_n:,} |",
        f"| Posted in the last 7 days | {fresh:,} |",
        f"| Score 80+ | {b80:,} |",
        f"| Score 65+ | {b65:,} |",
        f"| Of the 65+: full-time / contract / part-time | {typ.get('full-time', 0)} / {typ.get('contract', 0)} / {typ.get('part-time', 0)} |", "",
        "Sources on the board: " + ", ".join(f"{k} {v}" for k, v in src.most_common()) + ".", "",
        "## How to use it", "",
        "1. Start at the ten strongest contacts at the top. Apply ↗ opens the posting; Applied ✓ drops it off the scope and the next one moves up.",
        "2. The board below defaults to score 50+, remote only, US / CA / worldwide only, senior and staff hidden. Loosen the chips when the top runs dry.",
        "3. Every score has a why expander: the parts that built it (title match, seniority, remote, self-taught wording, years demanded, age).",
        "4. Export applied as text copies a title — company — url list for the war-room note.", "",
        "## Scoring rubric (pipeline/score.py)", "",
        "- Title family: agentic / AI automation / forward deployed / AI engineer / AI operations +32; applied AI / LLM / AI solutions / prompt / GenAI +26; AI platform +18; ML engineer +12; generic dev title with an AI-heavy body +10; a bare 'automation engineer' with no AI wording is treated as QA/RPA (+8).",
        "- Seniority: entry +22, mid +16, unknown +12, senior +3, staff −12. Role-family penalties for manager / marketer / scientist / QA / SRE / data engineer / architect / campus programs, and for job-seeker posts.",
        "- Place: remote (US/worldwide) +20, Bay Area +15, remote but EU-scoped −6, onsite elsewhere −22. Contract +8, full-time +6.",
        "- Body: self-taught-friendly wording +10, names Claude Code / Cursor / agents +8, n8n / Zapier +4, stack overlap +6; 8+ years −14, 5+ years −7, PhD required −12, clearance −20, offshore-preferred wording −18.",
        "- Freshness: ≤7 days +10, ≤14 +6, ≤30 +2, older −5. Verified open +5; a closed page zeroes the row.", "",
        "## Sources and honesty", "",
        f"- **Data refreshed {a.refreshed}.** The page keeps its 2026-09-02 name and share URL; every refresh rebuilds it from the full raw set, so ticks made in a browser survive.",
        "- **Worked (keyless, headless):** Hacker News Who is hiring (Sept + Aug 2026, Algolia); Greenhouse, Lever, Ashby, SmartRecruiters boards (461 boards discovered by web search, a curated AI-company probe, and 4 Firecrawl calls); Himalayas, Jobicy, Arbeitnow, RemoteOK, Remotive, We Work Remotely, Working Nomads, Jobspresso; Wellfound (public role pages); LinkedIn guest search (12 queries × remote-US and Bay Area, past 30 days, walked to the guest API's 300-result ceiling per query, every detail page fetched); SimplyHired, Built In, Dice (server-rendered pages with JSON-LD); ZipRecruiter (its search box is behind a Cloudflare challenge, but the directory pages its own `llms.txt` publishes are open — 16 titles × 7 locations, each job page read as a markdown record with a posted date and an Active flag); Reddit hiring subs via Arctic Shift (82 requests, serialized).",
        "- **Blocked:** Indeed, Glassdoor (Cloudflare challenges); Workable (per-IP daily cap, 49 live boards saved for a retry after 24 h); Y Combinator Work at a Startup (logged-out search key returns zero hits); Otta / Welcome to the Jungle, ai-jobs.net, remote.io (JS shells, no keyless feed). ZipRecruiter's official job-search MCP answers five results a call and rate-limits after a handful, so it is not used.",
        "- **Verify = open** means the posting page returned 200 without a closed phrase, or the source API only lists live jobs. **Unknown** means there was no page to check (a Hacker News comment or a Reddit post). Seven postings came back closed and were dropped.",
        "- Himalayas rate-limited a 45-day backfill (429s); the board carries its 2026-08-31 → 09-03 window. Jobgether rows on Lever are an aggregator reposting partner companies' roles; they are marked and scored −6.", "",
        f"## Top {a.top} at build time", "",
        "| # | Score | Role | Company | Type | Where | Posted | Link |", "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows[: a.top], 1):
        where = "Remote" if r.get("remote") else (r.get("location") or "?")
        if r.get("remote") and r.get("region") not in ("worldwide", "unknown"):
            where += f" ({r['region']})"
        lines.append(f"| {i} | {r['score']} | {r['title'][:60].replace('|', '/')} | {r['company'][:36].replace('|', '/')} | {r['employment_type']} | "
                     f"{where[:34].replace('|', '/')} | {r.get('posted_at') or '?'} | [open]({r['url']}) |")
    lines += ["", f"Full ranked list: `{a.artifacts}` (see [[{a.notes}]]).", "",
              "Design: hello-kitty, cyber-minimal-technical family, a range-ring radar contact log: the masthead scope plots every posting at a radius equal to its distance from score 100, and the score-band chips are its rings.", ""]
    open(a.out, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print(f"wrote {a.out} ({n} rows, top {a.top})")


if __name__ == "__main__":
    main()
