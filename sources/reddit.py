#!/usr/bin/env python3
"""job-radar source: Reddit hiring posts via the Arctic Shift REST API (keyless, headless).

Anonymous reddit .json and PullPush are dead for agents; Arctic Shift is the only free
full-text Reddit backend that works from this machine. It is ONE per-IP budget, so every
request here is serialized with a >=10s gap and the whole run is logged to
`raw/reddit-requests.log`.

Verified API semantics (probed 2026-09-02):
  - one subreddit per request (comma lists -> 400)
  - `fields` is validated: no `permalink`, no `removed_by_category`; build the URL from id
  - no-query pulls tolerate `limit=100` + `after=<ISO date>` fine (200, full page)
  - text search (`query=` / `title=`) at limit=100 returns HTTP 422
    "Timeout. Maybe slow down a bit" -- text passes therefore use limit=25
  - 422 is transient: back off and retry the identical request

Usage:
  set PYTHONIOENCODING=utf-8
  python sources/reddit.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/reddit.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

API = "https://arctic-shift.photon-reddit.com/api/posts/search"
FIELDS = "id,title,selftext,author,score,num_comments,created_utc,subreddit,link_flair_text"
SOURCE = "reddit"

DATA_DIR = "C:/Users/endle/MyStuff/_job-radar-data/raw"
CACHE_DIR = os.path.join(DATA_DIR, "reddit-cache")
REQ_LOG = os.path.join(DATA_DIR, "reddit-requests.log")

MIN_GAP = 10.0          # seconds between requests -- one per-IP budget
BACKOFFS = [30, 60]     # on 422/429
MAX_CONSEC_FAIL = 3     # abort the whole run after this many in a row

CUTOFF_ISO = common.CUTOFF                       # 2026-07-19
CUTOFF_TS = int(datetime.strptime(CUTOFF_ISO, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp())

# --- subreddit plan -----------------------------------------------------------------
# Dedicated job boards: pull everything recent (no query) and filter locally.
# `pages` caps the cursor walk so a mega-sub cannot eat the whole budget.
JOB_SUBS = [
    ("forhire", 18),
    ("hiring", 8),
    ("jobbit", 10),
    ("RemoteJobs", 6),
    ("AIJobs", 8),
    ("MachineLearningJobs", 4),
    ("cofounder", 4),
]
# General/AI-tool subs: full-text `query=hiring`, then a `title=hiring` sweep.
GENERAL_SUBS = [
    "ClaudeAI", "ClaudeCode", "n8n", "automation", "LangChain", "LocalLLaMA",
    "artificial", "aiagents", "AI_Agents", "Entrepreneur", "startups",
]
# Subs where every post is a job ad, so a title marker is not required.
HIRING_ONLY = {"aijobs", "machinelearningjobs", "remotejobs", "hiring"}
# Explicitly skipped (documented in the run report rather than silently dropped).
SKIPPED = {
    "cscareerquestions": "discussion sub, not postings",
    "slavelabour": "task-marketplace spam; AI-dev tasks there are $5 gigs, not roles",
}

# --- filters ------------------------------------------------------------------------
# Candidates advertising THEMSELVES. r/forhire and r/MachineLearningJobs are full of
# these and they read as hiring posts to a naive marker check.
OFFER_MARKERS = re.compile(
    r"\[\s*(?:for[\s-]?hire|offer|offering|available|task|service|portfolio|resume|cv)\s*\]"
    r"|\bfor hire\b|\bi am available\b|\bi'm available\b|\bopen (?:to|for) work\b"
    r"|\blooking for (?:an? )?(?:opportunit|work\b|a job\b|my next|new role|remote role)"
    r"|\blooking for\b[^.|]{0,45}\b(?:opportunities|roles|jobs)\b"
    r"|\bseeking\b[^.|]{0,45}\b(?:opportunit|new (?:role|position|job)|remote (?:role|work|job))|\b(?:graduate|fresher|bootcamp grad)\b[^.|]{0,30}\|"
    r"|\bavailable for (?:hire|work|freelance|projects?|contract)\b|\bhire me\b"
    r"|\bmy (?:resume|cv|portfolio)\b|\bimmediate joiner\b|\bresume review\b|\[student\]|\b(?:review|critique|roast) my\b"
    r"|\b\d+\+?\s*(?:yrs?|years?) (?:of )?experience\b", re.I)
# Discussion / show-and-tell threads that happen to contain a hiring word.
DISCUSSION_REJECT = re.compile(
    r"\bthoughts on\b|\bneed (?:feedback|advice|help|suggestions|tips|guidance)\b"
    r"|\bwant to learn\b|\bhow do i\b|\bhow to\b|\bis it worth\b|\bshould i\b"
    r"|\bwhat do you think\b|\bany(?:one|body) (?:here|know|used|else)\b|\badvice\b"
    r"|\bdiscussion\b|\brant\b|\bstruggling\b|\bhelp me\b|\bam i\b|\bwhy (?:is|are|do)\b"
    r"|\bmy experience\b|\bjust got\b|\bi built\b|\bi made\b|\bmade a\b|\bfeedback\b|\bwill ai\b|\breplace junior|\busage data\b", re.I)
HIRING_MARKERS = re.compile(
    r"\[\s*hiring\s*\]|\bhiring\b|\blooking for\b|\bneed(?:ed)? an?\b|\bseeking\b"
    r"|\bwe need\b|\bwanted\b|\bjob\b|\brole\b|\bposition\b|\bopening\b", re.I)
HIRING_FLAIR = re.compile(r"hiring|recruit|job|opening|position", re.I)
HIRING_CONTEXT = re.compile(
    r"\bhir(?:e|ing)\b|\blooking for\b|\bneed someone\b|\bpaid\b|\bbudget\b|\bcompensation\b"
    r"|\bsalary\b|\bhourly\b|\brate\b|\bcontract\b|\bapply\b|\bdm me\b|\breach out\b"
    r"|\bfreelanc|\bper hour\b|\bpay\b|\$\d", re.I)
# Deliberately excludes a bare "ai" -- on r/AIJobs every data-labeling gig says "AI",
# which is what made the first pass keep 46 annotation gigs out of 197 posts.
AI_SIGNAL = re.compile(
    r"\bclaude(?:\s?code)?\b|\bcursor\b|\bagent(?:s|ic|ics)?\b|\bn8n\b|\bautomations?\b"
    r"|\bllms?\b|\blangchain\b|\blanggraph\b|\bgpt[-\s]?\d?\b|\bopenai\b|\banthropic\b"
    r"|\brag\b|\bzapier\b|\bmake\.com\b|\bmachine learning\b|\bprompt engineer"
    r"|\bai (?:engineer|developer|dev|automation|integration|workflow|app|tool|product|saas)"
    r"|\bfine[- ]?tun|\bvector (?:db|database|store)\b|\bembedding", re.I)
# The role must be a BUILD role, not a gig. One of these must appear.
ENGINEERING = re.compile(
    r"\bengineer\w*\b|\bdevelopers?\b|\bdev\b|\bprogramm\w*|\bcoder\b|\bsoftware\b"
    r"|\bfull[-\s]?stack\b|\bbackend\b|\bfront[-\s]?end\b|\bpython\b|\btypescript\b"
    r"|\bjavascript\b|\bnode\.?js\b|\breact\b|\bapi\b|\bintegrat\w*|\bn8n\b|\blangchain\b"
    r"|\bclaude code\b|\bcursor\b|\bautomations?\b|\bworkflows?\b|\bpipeline\b|\bscript\w*"
    r"|\bsaas\b|\bmvp\b|\bcodebase\b|\bgithub\b|\btechnical\b|\barchitect\w*|\bbuild\w*",
    re.I)
# Body-scope spam tokens ONLY. Deliberately narrow: a real AI-engineering post says
# "evaluation", "expert" and "course" in passing, so those stay title-scope below.
GIG_REJECT_BODY = re.compile(
    r"\bdata (?:annotation|labell?ing|collection)\b|\bannotators?\b|\bvoice recording\b"
    r"|\bspeech data\b|\bnative speakers?\b|\bdialect\b|\btranscription\b|\bper[-\s]task\b"
    r"|\btaskers?\b|\bmturk\b|\bmicrotask\w*|\bclickwork\w*|\begocentric\b"
    r"|\brecord (?:yourself|your voice)\b|\bstudy participant\b|\bfocus group\b", re.I)
# Title-scope: data-labeling / expert-marketplace farms, course ads, job roundups,
# and non-technical gigs.
GIG_REJECT = re.compile(
    r"\bdata (?:annotation|labell?ing|collection|entry)\b|\bannotator\b|\blabell?ers?\b"
    r"|\bvoice recording\b|\bspeech data\b|\bnative speakers?\b|\bdialect\b|\btranscrib\w*"
    r"|\btranscription\b|\bsurveys?\b|\bper[-\s]task\b|\btaskers?\b|\bcontributors?\b"
    r"|\braters?\b|\bevaluators?\b|\bai trainers?\b|\bstudy participant\b|\bfocus group\b"
    r"|\busability test\w*|\begocentric\b|\brecord (?:yourself|your)\b|\bscreen recording\b"
    r"|\bexperts?\b|\btrainers?\b|\bgeneralists?\b|\bhuman data\b|\bevaluation\b"
    r"|\bcourse\b|\bfoundations of\b|\bcertification\b|\bbootcamp\b|\bwebinar\b|\btutorial\b"
    r"|\b\d+\s+(?:new\s+)?(?:remote\s+)?(?:ai\s+)?(?:jobs|roles)\b|\bjob (?:board|digest|roundup)\b"
    r"|\bvirtual assistant\b|\bappointment setter\b|\bcold call\w*|\bsocial media manager\b"
    r"|\bvideo edit\w*|\bcopywriter\b|\bcontent writer\b|\bghostwriter\b|\bproofread\w*"
    r"|\bmicrotask\w*|\bmturk\b|\bclickwork\w*|\bgig work\b"
    r"|\bbooks?\b|\bauthors?\b|\bpodcast\b|\bnewsletter\b|\bebook\b|\bgiveaway\b|\bAMA\b"
    r"|\bmy experience\b|\banyone else\b|\bwhat do y'?all\b", re.I)
DEAD_AUTHORS = {"[deleted]", "[removed]", "automoderator", "", None}
DEAD_BODY = {"[deleted]", "[removed]"}

MONEY = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k\b)?"
    r"(?:\s*(?:-|–|—|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*(?:k\b)?)?"
    r"(?:\s*(?:/|per\s+)\s*(?:hr|hour|yr|year|mo|month|week|wk|day))?", re.I)
LOC_LINE = re.compile(r"^\W{0,4}location\W{0,4}\s*[:\-–]\s*(.+)$", re.I | re.M)
COMPANY_LINE = re.compile(r"^\W{0,4}company\W{0,4}\s*[:\-–]\s*(.+)$", re.I | re.M)
REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b|\banywhere\b", re.I)


class Budget:
    """Serialized Arctic Shift client: one request at a time, >=10s apart, logged."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.last = 0.0
        self.n = 0
        self.fails = 0            # consecutive
        self.total_422 = 0
        self.total_429 = 0
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== run {common.now()} cutoff={CUTOFF_ISO} ===\n")

    def log(self, line: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get(self, params: dict, cache_key: str) -> list | None:
        """One request (with backoff retries). Returns list of posts, or None on failure."""
        for attempt in range(len(BACKOFFS) + 1):
            waited = max(0.0, MIN_GAP - (time.time() - self.last))
            if waited:
                time.sleep(waited)
            url = API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            t0 = time.time()
            self.last = t0
            self.n += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": common.UA})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    status = resp.status
                    payload = json.load(resp)
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    payload = json.load(exc)
                except Exception:
                    payload = {"data": None, "error": f"HTTP {exc.code}"}
            except Exception as exc:                       # noqa: BLE001
                status = "ERR"
                payload = {"data": None, "error": str(exc)}
            self.last = time.time()
            data, err = payload.get("data"), payload.get("error")
            n = len(data) if isinstance(data, list) else 0
            self.log(f"sub={params.get('subreddit')} q={params.get('query') or params.get('title') or '-'} "
                     f"after={params.get('after')} before={params.get('before') or '-'} "
                     f"limit={params.get('limit')} status={status} count={n} "
                     f"waited={waited:.1f}s elapsed={self.last - t0:.1f}s "
                     f"attempt={attempt + 1}" + (f" err={err}" if err else ""))
            if status == 422:
                self.total_422 += 1
            if status == 429:
                self.total_429 += 1
            if isinstance(data, list):
                self.fails = 0
                self._cache(cache_key, params, data)
                return data
            if attempt < len(BACKOFFS):
                time.sleep(BACKOFFS[attempt])
        self.fails += 1
        self.log(f"  !! giving up: {params.get('subreddit')} (consecutive failures={self.fails})")
        return None

    def _cache(self, key: str, params: dict, data: list) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        with open(os.path.join(CACHE_DIR, f"{safe}.json"), "w", encoding="utf-8") as f:
            json.dump({"params": params, "fetched_at": common.now(), "data": data},
                      f, ensure_ascii=False)


def walk(bud: Budget, sub: str, pages: int, query: dict | None, limit: int, tag: str):
    """Cursor-walk one subreddit newest-first down to the cutoff. Returns (posts, aborted)."""
    seen, cursor = [], None
    for page in range(pages):
        params = {"subreddit": sub, "after": CUTOFF_ISO, "limit": limit, "sort": "desc",
                  "fields": FIELDS}
        if query:
            params.update(query)
        if cursor:
            params["before"] = cursor
        data = bud.get(params, f"{sub}__{tag}__p{page}")
        if data is None:
            return seen, bud.fails >= MAX_CONSEC_FAIL
        if not data:
            break
        seen.extend(data)
        oldest = min(int(p.get("created_utc") or 0) for p in data)
        cursor = oldest
        if oldest <= CUTOFF_TS or len(data) < limit:
            break
    return seen, False


def first(pattern: re.Pattern, text: str) -> str:
    """First capture group, stripped of markdown bold/italics and nbsp padding.

    Reddit bodies write "**Location:** Remote", and the naive capture keeps the
    trailing "**" -- which then leaks into `location` and skews `region_of`.
    """
    m = pattern.search(text or "")
    if not (m and m.groups()):
        return ""
    val = (m.group(1) or "").replace("\xa0", " ")
    return val.strip().strip("*_#`:- ").strip()[:160]


def to_row(p: dict) -> dict | None:
    title = (p.get("title") or "").strip()
    body = p.get("selftext") or ""
    author = (p.get("author") or "").strip()
    sub = p.get("subreddit") or ""
    flair = p.get("link_flair_text") or ""
    blob = f"{title}\n{body}"

    if author.lower() in DEAD_AUTHORS or body.strip().lower() in DEAD_BODY:
        return None
    if OFFER_MARKERS.search(title) or DISCUSSION_REJECT.search(title):
        return None
    # SCHEMA.md drops these outright; apply them even on the tool-signal path below.
    if any(r in title.lower() for r in common.REJECT):
        return None
    if GIG_REJECT.search(title) or GIG_REJECT_BODY.search(body[:2500]):
        return None

    # A hiring-only sub is not a licence to keep every thread in it: the article and
    # show-and-tell posts there carry no marker, so demand hiring language in the body.
    marked = bool(HIRING_MARKERS.search(title)) or bool(HIRING_FLAIR.search(flair)) or (
        sub.lower() in HIRING_ONLY and bool(HIRING_CONTEXT.search(body)))
    if not marked:
        return None
    # Either the schema's own fit test passes, or the body names a real tool/stack in a
    # hiring context (covers "[Hiring] need someone to build n8n automations"). The
    # second path demands an engineering word in the TITLE -- without that anchor the
    # expert-marketplace farms ("Tax Review Specialists", "Client Call Manager") walk in
    # on a body that happens to say "AI" and "$/hr".
    fit_schema = common.is_fit(title, body)
    fit_body = bool(AI_SIGNAL.search(blob) and HIRING_CONTEXT.search(body or title)
                    and ENGINEERING.search(title))
    if not (fit_schema or fit_body):
        return None
    if not AI_SIGNAL.search(blob) or not ENGINEERING.search(blob):
        return None

    ts = int(p.get("created_utc") or 0)
    posted_at = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else None
    if not common.recent(posted_at):
        return None

    url = f"https://www.reddit.com/r/{sub}/comments/{p.get('id')}/"
    loc = first(LOC_LINE, body)
    if not loc and REMOTE_RE.search(blob):
        loc = "Remote"
    company = first(COMPANY_LINE, body) or f"u/{author}"
    money = [m.group(0).strip() for m in MONEY.finditer(blob)][:3]
    notes = f"r/{sub} · score {p.get('score', 0)} · {p.get('num_comments', 0)} comments"
    if flair:
        notes += f" · flair: {flair}"

    return common.record(
        SOURCE, title, company, url,
        location=loc,
        remote=True if REMOTE_RE.search(blob) else None,
        salary=" | ".join(money) or None,
        posted_at=posted_at,
        description=body,
        tags=[t for t in [sub, flair] if t],
        open_status="unverified",
        notes=notes,
        native_id=p.get("id"),
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="job-radar Reddit source (Arctic Shift)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from-cache", action="store_true",
                    help="rebuild rows from raw/reddit-cache/ without spending API budget")
    args = ap.parse_args()

    if args.from_cache:
        raw: dict[str, dict] = {}
        seen_per_sub: dict[str, int] = {}
        for f in sorted(glob.glob(os.path.join(CACHE_DIR, "*.json"))):
            with open(f, encoding="utf-8") as fh:
                page = json.load(fh)
            for p in page.get("data", []):
                if p.get("id") and p["id"] not in raw:
                    raw[p["id"]] = p
                    s = p.get("subreddit", "?")
                    seen_per_sub[s] = seen_per_sub.get(s, 0) + 1
        emit(args, raw, seen_per_sub, None, False)
        return

    bud = Budget(REQ_LOG)
    raw: dict[str, dict] = {}
    seen_per_sub: dict[str, int] = {}
    aborted = False

    def absorb(sub: str, posts: list) -> None:
        seen_per_sub[sub] = seen_per_sub.get(sub, 0) + len(posts)
        for p in posts:
            if p.get("id"):
                raw.setdefault(p["id"], p)

    for sub, pages in JOB_SUBS:
        posts, stop = walk(bud, sub, pages, None, 100, "all")
        if not posts and not stop and sub != sub.lower():
            posts, stop = walk(bud, sub.lower(), pages, None, 100, "all-lc")
            sub = sub.lower() if posts else sub
        absorb(sub, posts)
        print(f"r/{sub}: {len(posts)} posts seen", file=sys.stderr)
        if stop:
            aborted = True
            break

    if not aborted:
        for sub in GENERAL_SUBS:
            for tag, q in (("q-hiring", {"query": "hiring"}), ("t-hiring", {"title": "hiring"})):
                posts, stop = walk(bud, sub, 2, q, 25, tag)
                absorb(sub, posts)
                if stop:
                    aborted = True
                    break
            print(f"r/{sub}: {seen_per_sub.get(sub, 0)} posts seen", file=sys.stderr)
            if aborted:
                break

    emit(args, raw, seen_per_sub, bud, aborted)


def emit(args, raw: dict, seen_per_sub: dict, bud, aborted: bool) -> None:
    rows, by_sub = [], {}
    for p in raw.values():
        row = to_row(p)
        if row:
            rows.append(row)
            s = p.get("subreddit", "?")
            by_sub[s] = by_sub.get(s, 0) + 1
    rows.sort(key=lambda r: r["posted_at"] or "", reverse=True)
    if args.limit:
        rows = rows[:args.limit]
    common.write_jsonl(args.out, rows)

    print(f"\nrequests={bud.n if bud else 0} 422s={bud.total_422 if bud else 0} "
          f"429s={bud.total_429 if bud else 0} aborted={aborted}", file=sys.stderr)
    print(f"raw posts seen={len(raw)} kept={len(rows)}", file=sys.stderr)
    for s, n in sorted(seen_per_sub.items(), key=lambda kv: -kv[1]):
        print(f"  seen r/{s}: {n}  kept: {by_sub.get(s, 0)}", file=sys.stderr)
    print("skipped: " + "; ".join(f"r/{k} ({v})" for k, v in SKIPPED.items()), file=sys.stderr)
    for r in rows[:10]:
        print(f"  {r['notes'].split(' ·')[0]} · {r['title'][:70]} · {r['company']} · "
              f"{r['posted_at']} · {r['url']}", file=sys.stderr)


if __name__ == "__main__":
    main()
