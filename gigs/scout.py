#!/usr/bin/env python3
"""gig-scout — find people who ALREADY posted a budget for work Jasper can ship.

The job-radar sources hunt salaried roles. This scout hunts the other shape of demand:
short paid tasks and contracts ("[Hiring] need a Next.js dashboard, $400", "[TASK] scrape
500 Google Maps listings, $30", HN contract/part-time posts with an hourly rate). Every
row keeps its source URL; nothing is fabricated; help-seekers ("how do I…") are rejected
on purpose — a question is not a purchase order.

    python gigs/scout.py --days 3                 # scan, write board + jsonl
    python gigs/scout.py --days 1 --from-cache    # re-filter the last fetch, no API spend

Outputs (in $JOB_RADAR_DATA/gigs, default C:/Users/endle/MyStuff/_job-radar-data/gigs):
    gigs.jsonl        every kept row (SCHEMA below)
    board.md          ranked, human-readable, paste-ready
    seen.json         ids already surfaced (so a daily run only shows NEW rows in "new")
    cache/            raw API pages (for --from-cache)

Row schema: id, source, sub, title, url, author, posted_at, age_h, comments, tier
("explicit" = a dollar figure in the post, "stated" = pay words but no figure), money
(list of money strings), amount_usd (best parse or null), fit (list of Jasper's lanes),
apply (comment|dm|link|email), score, red_flags, body (first 1500 chars).

Arctic Shift rules (verified in sources/reddit.py): ONE sub per request, >=10s between
requests, limit 100 for no-query pulls, limit 25 for text queries, back off on 422/429.
"""
from __future__ import annotations

import argparse
import glob
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sources"))
from reddit import OFFER_MARKERS, DISCUSSION_REJECT, MONEY  # noqa: E402

UA = "gig-scout/0.1 (personal freelance search; contact endlesslime9@gmail.com)"
API = "https://arctic-shift.photon-reddit.com/api/posts/search"
FIELDS = "id,title,selftext,author,score,num_comments,created_utc,subreddit,link_flair_text"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{}"
DATA = os.environ.get("JOB_RADAR_DATA", r"C:/Users/endle/MyStuff/_job-radar-data")

MIN_GAP = 10.0
BACKOFFS = [30, 60]

# (subreddit, mode, pages). mode: "marker" = title must carry [Hiring]/[Task]-style marker;
# "all" = every post is a listing; "query" = text search `hiring`/`looking for` (limit 25).
SUBS = [
    ("forhire", "marker", 3),
    ("FreelanceDevelopers", "marker", 1),
    ("slavelabour", "marker", 3),
    ("hiring", "all", 2),
    ("jobbit", "all", 2),
    ("freelance_forhire", "marker", 1),
    ("Jobs4Bitcoins", "marker", 1),
    ("RemoteJobs", "all", 1),
    ("HireAnAppDeveloper", "all", 1),
    ("ClaudeCode", "query", 1),
    ("ClaudeAI", "query", 1),
    ("vibecoding", "query", 1),
    ("n8n", "query", 1),
    ("SaaS", "query", 1),
]

HIRING_MARK = re.compile(r"\[\s*(?:hiring|task|request|paid|job|contract|bounty)\s*\]", re.I)
HIRING_WORDS = re.compile(
    r"\bhiring\b|\blooking for (?:an? |someone|a dev|a developer|help)|\bneed (?:an? |someone)"
    r"|\bseeking\b|\bwanted\b|\bpaid (?:gig|task|work|project)\b|\bwill pay\b|\bbudget\b", re.I)
PAY_WORDS = re.compile(
    r"\bbudget\b|\bpaid\b|\bpay(?:ing|ment)?\b|\bper hour\b|/\s?hr\b|/\s?hour\b|\bhourly\b"
    r"|\busd\b|\bfixed[- ]price\b|\bcompensat|\brate\b|\bsalary\b|\bbounty\b|\bretainer\b", re.I)

# Jasper's lanes -> keyword nets. Fit needs at least one lane.
LANES = {
    "app-build": r"\b(?:web ?app|mobile app|ios app|android app|react native|expo|flutter|next\.?js|supabase|dashboard|saas|mvp|prototype|full[- ]?stack|frontend|backend|api)\b",
    "app-store": r"\b(?:app store|testflight|play store|google play|apple review|xcode|capacitor|pwa)\b",
    "ai-automation": r"\b(?:ai|llm|gpt|chatgpt|claude|openai|anthropic|agent|agents|agentic|rag|n8n|zapier|make\.com|automation|automate|workflow|chatbot|bot|prompt|mcp)\b",
    "scrape-data": r"\b(?:scrap(?:e|ing|er)|crawl|lead list|leads|google maps|data extraction|csv|spreadsheet|enrich|apollo|directory)\b",
    "seo-local": r"\b(?:seo|google business|gbp|google maps ranking|local search|citations|backlinks)\b",
    "content-deck": r"\b(?:transcri(?:be|ption)|meeting notes|summar(?:y|ize)|slide deck|presentation|pitch deck|powerpoint)\b",
    "scripting": r"\b(?:python|script|node|typescript|javascript|sql|excel macro|browser extension|chrome extension|telegram bot|discord bot)\b",
}
LANE_RX = {k: re.compile(v, re.I) for k, v in LANES.items()}

RED_FLAGS = re.compile(
    r"\btelegram (?:only|me)\b|\bwhatsapp (?:only|me)\b|\bi will pay (?:you )?(?:first|upfront)\b"
    r"|\bpay (?:you )?upfront\b|\bverification code\b|\brent(?:ing)? (?:your|an?) account\b"
    r"|\bmystery shop|\bcheck cashing\b|\bwire transfer\b|\bgift cards?\b|\bequity[- ]only\b"
    r"|\brev(?:enue)?[- ]share only\b|\bunpaid\b|\bfor exposure\b|\bfree trial task\b"
    r"|\bcrypto only\b|\bcashapp me\b", re.I)
NOT_FOR_HIM = re.compile(
    r"\bvoice ?over\b|\bvideo edit|\bthumbnail|\blogo\b|\billustrat|\bwriter\b|\bessay\b|\bhomework"
    r"|\btutor\b|\bvirtual assistant\b|\bcold call|\bappointment sett|\btranslat|\b3d\b|\bunity\b|\bunreal\b"
    r"|\bshopify theme\b|\bwordpress theme\b|\bfigma only\b|\bux research\b|\bphotograph|\bmusic\b|\bsinging\b"
    r"|\bonlyfans\b|\bdating\b|\bacademic\b|\bthesis\b|\bexam\b"
    # non-engineering roles that mention AI/data in passing
    r"|\bugc\b|\bcreators?\b|\bclipper\b|\bsocial media\b|\bengagers?\b|\bbilling\b|\bsourcing\b|\bffe\b|\bff&e\b"
    r"|\boutreach\b|\bsales(?:person)?\b|\bmarketer\b|\baffiliate\b|\bimage review\b|\bassessors?\b|\bsupport rep"
    r"|\bcustomer (?:support|service)\b|\brecruit|\bmanager\b|\bcoordinator\b|\bspecialist\b|\bcopy-paste\b"
    r"|\bcommission[- ]based\b|\bcommission only\b|\breferral\b|\bvacation\b|\bsalesforce\b|\bworkday\b"
    # someone advertising a service under a task/hiring tag
    r"|\bon your behalf\b|\bi will (?:do|build|make|write|apply|manually)\b|\bwill manually\b", re.I)
# A row must be engineering-shaped somewhere in the post, not just "uses AI".
DEV_ANCHOR = re.compile(
    r"\bbuild\w*\b|\bdevelop\w*\b|\bengineer\w*\b|\bcoder\b|\bprogramm\w*|\bscript\w*|\bscrap\w+|\bautomat\w+"
    r"|\bintegrat\w+|\bapi\b|\bapps?\b|\bwebsite\b|\bweb app\b|\bdashboard\b|\bbots?\b|\bpython\b|\bjavascript\b"
    r"|\btypescript\b|\breact\b|\bnode\b|\bnext\.?js\b|\bsupabase\b|\bn8n\b|\bzapier\b|\bllm\b|\brag\b|\bagents?\b"
    r"|\bmcp\b|\bclaude code\b|\bcursor\b|\bprompt engineer|\bai research\b|\bevaluator\b|\bdata (?:pipeline|extraction)\b", re.I)
# Yearly salaries are job-radar's business (sources/*.py), not a gig.
SALARY_CEILING = 20000
# Not disqualifiers, but the reader should see them before spending a send.
WALLS = re.compile(
    r"\bsenior\b|\bstaff engineer\b|\b\d\+?\s*(?:yrs?|years?)(?: of)? (?:real |professional )?(?:experience|[a-z]+ (?:backend|experience))"
    r"|\bgmt\s*(?:to|-)\s*gmt\+\d|\b(?:est|et|cet|ist) (?:hours|overlap)\b|\bdegree in\b|\bus[- ]based only\b|\beu only\b", re.I)
APPLY_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
APPLY_LINK = re.compile(r"https?://\S+", re.I)
DM_WORDS = re.compile(r"\bdm\b|\bpm me\b|\bmessage me\b|\bchat\b", re.I)

HN_CONTRACT = re.compile(r"\bcontract|\bfreelanc|\bpart[- ]time|\bhourly|/hr\b|\bper hour|\bcontractor", re.I)
HN_AI = re.compile(r"\bAI\b|\bLLM|\bagent|\bClaude|\bRAG\b|\bautomation|\bagentic|\bML\b", re.I)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------- fetch
class Arctic:
    def __init__(self, cache_dir: str, log_path: str):
        self.cache_dir, self.log_path = cache_dir, log_path
        self.last, self.n, self.fails = 0.0, 0, 0
        os.makedirs(cache_dir, exist_ok=True)

    def log(self, line: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{now_utc().isoformat(timespec='seconds')} {line}\n")

    def get(self, params: dict, key: str) -> list | None:
        for attempt in range(len(BACKOFFS) + 1):
            wait = max(0.0, MIN_GAP - (time.time() - self.last))
            if wait:
                time.sleep(wait)
            url = API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            self.last = time.time()
            self.n += 1
            status, payload = "ERR", {}
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as r:
                    status, payload = r.status, json.load(r)
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    payload = json.load(e)
                except Exception:
                    payload = {}
            except Exception as e:  # noqa: BLE001
                payload = {"error": str(e)}
            data = payload.get("data")
            self.log(f"sub={params.get('subreddit')} q={params.get('query') or params.get('title') or '-'} "
                     f"status={status} count={len(data) if isinstance(data, list) else 0} attempt={attempt + 1}")
            if isinstance(data, list):
                self.fails = 0
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
                with open(os.path.join(self.cache_dir, f"{safe}.json"), "w", encoding="utf-8") as f:
                    json.dump({"params": params, "fetched_at": now_utc().isoformat(), "data": data}, f)
                return data
            if attempt < len(BACKOFFS):
                time.sleep(BACKOFFS[attempt])
        self.fails += 1
        return None


def fetch_reddit(days: int, cache_dir: str, log_path: str) -> dict[str, dict]:
    after = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ar = Arctic(cache_dir, log_path)
    raw: dict[str, dict] = {}
    for sub, mode, pages in SUBS:
        queries = [None] if mode != "query" else [{"title": "hiring"}, {"title": "looking for"}]
        for q in queries:
            cursor = None
            for page in range(pages):
                params = {"subreddit": sub, "after": after, "limit": 25 if q else 100,
                          "sort": "desc", "fields": FIELDS}
                if q:
                    params.update(q)
                if cursor:
                    params["before"] = cursor
                tag = f"{sub}__{(q or {}).get('title', 'all').replace(' ', '-')}__p{page}"
                data = ar.get(params, tag)
                if not data:
                    break
                for p in data:
                    if p.get("id"):
                        raw.setdefault(p["id"], {**p, "_mode": mode})
                cursor = min(int(p.get("created_utc") or 0) for p in data)
                if len(data) < params["limit"]:
                    break
        print(f"  r/{sub}: {sum(1 for p in raw.values() if p.get('subreddit', '').lower() == sub.lower())} raw",
              file=sys.stderr)
        if ar.fails >= 3:
            print("  !! Arctic Shift: 3 consecutive failures, stopping", file=sys.stderr)
            break
    print(f"  arctic requests={ar.n}", file=sys.stderr)
    return raw


def load_cache(cache_dir: str) -> dict[str, dict]:
    raw: dict[str, dict] = {}
    modes = {s.lower(): m for s, m, _ in SUBS}
    for f in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            page = json.load(fh)
        for p in page.get("data", []):
            if p.get("id"):
                raw.setdefault(p["id"], {**p, "_mode": modes.get((p.get("subreddit") or "").lower(), "marker")})
    return raw


def fetch_hn(days: int, cache_dir: str) -> list[dict]:
    """Latest 'Who is hiring' thread: contract / part-time / hourly comments only."""
    try:
        q = urllib.parse.urlencode({"query": "\"who is hiring\"", "tags": "story,author_whoishiring", "hitsPerPage": 1})
        with urllib.request.urlopen(urllib.request.Request(HN_SEARCH + "?" + q, headers={"User-Agent": UA}), timeout=60) as r:
            hit = json.load(r)["hits"][0]
        with urllib.request.urlopen(urllib.request.Request(HN_ITEM.format(hit["objectID"]), headers={"User-Agent": UA}), timeout=90) as r:
            thread = json.load(r)
        with open(os.path.join(cache_dir, f"hn-{hit['objectID']}.json"), "w", encoding="utf-8") as f:
            json.dump(thread, f)
    except Exception as e:  # noqa: BLE001
        print(f"  !! HN fetch failed: {e}", file=sys.stderr)
        return []
    return hn_rows(thread, days)


def hn_cached(cache_dir: str, days: int) -> list[dict]:
    files = sorted(glob.glob(os.path.join(cache_dir, "hn-*.json")))
    if not files:
        return []
    with open(files[-1], encoding="utf-8") as f:
        return hn_rows(json.load(f), days)


def hn_rows(thread: dict, days: int) -> list[dict]:
    rows = []
    title = thread.get("title") or "Who is hiring?"
    cutoff = now_utc() - timedelta(days=max(days, 35))  # the thread is monthly; keep the month
    for k in thread.get("children", []):
        text = htmlmod.unescape(re.sub(r"<[^>]+>", "\n", k.get("text") or ""))
        if not text.strip() or not k.get("created_at"):
            continue
        created = datetime.fromisoformat(k["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            continue
        if not (HN_CONTRACT.search(text) and HN_AI.search(text)):
            continue
        first = text.strip().splitlines()[0][:160]
        rows.append({"id": f"hn{k['id']}", "source": "hn", "sub": f"HN {title[8:]}", "title": first,
                     "url": f"https://news.ycombinator.com/item?id={k['id']}", "author": k.get("author"),
                     "created": created, "comments": len(k.get("children", [])), "body": text, "_mode": "all",
                     "flair": ""})
    print(f"  HN {title}: {len(rows)} contract/AI comments", file=sys.stderr)
    return rows


# ----------------------------------------------------------------------------- filter
def parse_amount(money: list[str]) -> float | None:
    best = None
    for m in money:
        nums = re.findall(r"\d[\d,]*(?:\.\d+)?", m)
        if not nums:
            continue
        v = max(float(n.replace(",", "")) for n in nums)   # "$60-80/hr" -> 80
        if re.search(r"\d\s*k\b", m, re.I):
            v *= 1000
        if best is None or v > best:
            best = v
    return best


def classify(row: dict) -> dict | None:
    title, body, sub, mode = row["title"], row.get("body") or "", row["sub"], row.get("_mode", "marker")
    blob = f"{title}\n{body}"
    if row.get("author") in ("[deleted]", "[removed]", "AutoModerator", None) or body.strip() in ("[deleted]", "[removed]"):
        return None
    if OFFER_MARKERS.search(title) and not HIRING_MARK.search(title):
        return None
    if DISCUSSION_REJECT.search(title) and not HIRING_MARK.search(title):
        return None
    if NOT_FOR_HIM.search(title):
        return None
    hiring = bool(HIRING_MARK.search(title)) or (mode in ("all",) and bool(HIRING_WORDS.search(blob))) \
        or (mode == "query" and bool(HIRING_WORDS.search(title)))
    if row["source"] == "hn":
        hiring = True
    if not hiring:
        return None
    lanes = [k for k, rx in LANE_RX.items() if rx.search(blob)]
    if not lanes or not DEV_ANCHOR.search(blob):
        return None
    money = []
    for m in MONEY.finditer(blob):
        tail = blob[m.end():m.end() + 10]
        if not re.search(r"\d", m.group(0)) or re.match(r"\s*(?:[MB]\b|million|billion)", tail, re.I):
            continue                                  # "$8M seed", "$2.7B" are not a budget
        money.append(m.group(0).strip())
    money = money[:4]
    amount = parse_amount(money)
    if amount and amount >= SALARY_CEILING:
        return None                                   # a salaried role: job-radar's lane, not a gig
    if money:
        tier = "explicit"
    elif PAY_WORDS.search(blob) or (row["source"] == "hn" and HN_CONTRACT.search(blob)):
        tier = "stated"                               # HN "Contract / Part-time / hourly" is a pay shape
    else:
        return None
    walls = sorted({w.lower() for w in WALLS.findall(blob)})
    flags = sorted({m.group(0).lower() for m in RED_FLAGS.finditer(blob)})
    if "equity-only" in " ".join(flags) or "unpaid" in flags:
        return None
    apply = "comment"
    if APPLY_EMAIL.search(body):
        apply = "email"
    elif APPLY_LINK.search(body) and row["source"] == "hn":
        apply = "link"
    elif DM_WORDS.search(blob):
        apply = "dm"
    age_h = (now_utc() - row["created"]).total_seconds() / 3600
    score = 0
    score += 40 if tier == "explicit" else 15
    if row["source"] == "hn" and HN_CONTRACT.search(blob):
        score += 15                                   # a named company offering contract/part-time work
    if amount:
        score += min(30, int(amount / 20))          # $600 -> +30
    score += 8 * min(3, len(lanes))
    score += 15 if age_h < 24 else 8 if age_h < 72 else 0
    score += 8 if row.get("comments", 0) <= 3 else 0
    score -= 25 * len(flags)
    return {
        "id": row["id"], "source": row["source"], "sub": row["sub"], "title": title[:200], "url": row["url"],
        "author": row.get("author"), "posted_at": row["created"].strftime("%Y-%m-%d %H:%M"),
        "age_h": round(age_h, 1), "comments": row.get("comments", 0), "tier": tier, "money": money,
        "amount_usd": amount, "fit": lanes, "apply": apply, "score": score, "red_flags": flags, "walls": walls,
        "flair": row.get("flair") or "", "body": body[:1500],
    }


def reddit_rows(raw: dict[str, dict]) -> list[dict]:
    out = []
    for p in raw.values():
        ts = int(p.get("created_utc") or 0)
        out.append({"id": p["id"], "source": "reddit", "sub": f"r/{p.get('subreddit')}", "title": (p.get("title") or "").strip(),
                    "url": f"https://www.reddit.com/r/{p.get('subreddit')}/comments/{p['id']}/", "author": p.get("author"),
                    "created": datetime.fromtimestamp(ts, tz=timezone.utc), "comments": p.get("num_comments") or 0,
                    "body": p.get("selftext") or "", "_mode": p.get("_mode", "marker"), "flair": p.get("link_flair_text") or ""})
    return out


# ----------------------------------------------------------------------------- output
def write_board(rows: list[dict], new_ids: set[str], path: str, days: int) -> None:
    def line(r: dict) -> str:
        money = " · ".join(r["money"][:2]) if r["money"] else "pay stated, no figure"
        flag = " ⚠ " + ", ".join(r["red_flags"]) if r["red_flags"] else ""
        walls = ", ".join(r.get("walls") or []) or "—"
        new = "🆕 " if r["id"] in new_ids else ""
        title = r["title"][:80].replace("|", "·").replace("[", "").replace("]", "")
        return (f"| {r['score']} | {new}[{title}]({r['url']}) | {r['sub']} | {money} | "
                f"{', '.join(r['fit'])} | {r['apply']} | {walls} | {r['age_h']:.0f}h · {r['comments']}c{flag} |")
    hdr = "| score | post | where | money | lane | apply | walls | age |\n|---|---|---|---|---|---|---|---|"
    explicit = [r for r in rows if r["tier"] == "explicit"]
    stated = [r for r in rows if r["tier"] == "stated"]
    md = [f"# gig-scout board — {now_utc().astimezone().strftime('%Y-%m-%d %H:%M')} (last {days} days)", "",
          f"{len(explicit)} posts with a dollar figure · {len(stated)} with pay words only · {len(new_ids)} new since last run.",
          "A row is a stranger who already posted a budget. Reply where the `apply` column says; never DM a comment-only sub.", "",
          "## Dollar figure in the post", "", hdr, *[line(r) for r in explicit], "",
          "## Pay mentioned, no figure", "", hdr, *[line(r) for r in stated], ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="gig-scout: posted-budget work for Jasper")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(DATA, "gigs"))
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--min-score", type=int, default=30)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)

    if a.from_cache:
        raw = load_cache(cache)
        hn = hn_cached(cache, a.days)
    else:
        raw = fetch_reddit(a.days, cache, os.path.join(a.out, "requests.log"))
        hn = fetch_hn(a.days, cache)
    cutoff = now_utc() - timedelta(days=a.days)
    cands = [r for r in reddit_rows(raw) if r["created"] >= cutoff] + hn
    rows = [c for c in (classify(r) for r in cands) if c and c["score"] >= a.min_score]
    rows.sort(key=lambda r: (-r["score"], r["age_h"]))

    seen_path = os.path.join(a.out, "seen.json")
    seen = set(json.load(open(seen_path, encoding="utf-8"))) if os.path.exists(seen_path) else set()
    new_ids = {r["id"] for r in rows} - seen
    with open(os.path.join(a.out, "gigs.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(a.out, "new.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            if r["id"] in new_ids:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_board(rows, new_ids, os.path.join(a.out, "board.md"), a.days)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen | {r["id"] for r in rows}), f)
    print(f"candidates={len(cands)} kept={len(rows)} explicit={sum(r['tier'] == 'explicit' for r in rows)} "
          f"new={len(new_ids)} -> {a.out}", file=sys.stderr)
    for r in rows[:15]:
        print(f"  {r['score']:>3} {r['sub']:<22} {(' · '.join(r['money'][:1]) or '-'):<14} {r['title'][:70]}  {r['url']}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
