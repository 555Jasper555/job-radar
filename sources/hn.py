"""job-radar source: Hacker News hiring threads (Algolia HN API, keyless).

Pulls top-level comments from the monthly "Ask HN: Who is hiring?" threads and the
"Ask HN: Freelancer? Seeking freelancer?" threads, parses the conventional
`Company | Role | Location | REMOTE | Full-time | $salary | url` header line, and emits
one row per role that passes common.is_fit().

    set PYTHONIOENCODING=utf-8
    python sources/hn.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/hn.jsonl
"""
from __future__ import annotations

import html as htmllib
import json
import os
import re
import sys
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SOURCE = "hn"
ALGOLIA = "https://hn.algolia.com/api/v1"
DATA_DIR = os.environ.get("JOB_RADAR_DATA_DIR", "C:/Users/endle/MyStuff/_job-radar-data")
ITEM_CACHE = os.path.join(DATA_DIR, "raw", "hn-items")

ROLE_WORDS = [
    "engineer", "engineering", "developer", " dev", "dev ", "scientist", "designer",
    "architect", "manager", "lead ", " lead", "analyst", "researcher", "research", "sre",
    "devops", "swe", "product", "founding", "intern", "cto", "consultant", "specialist",
    "ops ", "operator", "technician", "programmer", "hacker", "role", "position",
    "full-stack", "fullstack", "full stack", "backend", "frontend", "front-end", "back-end",
    "qa ", "data ", "ml ", "ai ", "security", "platform", "mobile", "ios", "android",
    "support", "solutions", "architecture", "staff ", "principal",
]
TYPE_WORDS = [
    "full-time", "full time", "fulltime", "part-time", "part time", "parttime", "contract",
    "contractor", "freelance", "internship", "intern", "permanent", "temporary", "c2c",
    "w2", "1099", "contract-to-hire", "perm", "seasonal", "co-op",
]
GEO_HINT = [
    "remote", "onsite", "on-site", "hybrid", "worldwide", "anywhere", "usa", "u.s.", "us",
    "uk", "eu", "europe", "canada", "india", "germany", "berlin", "london", "york",
    "francisco", "bay area", "seattle", "austin", "boston", "denver", "chicago", "toronto",
    "amsterdam", "paris", "dublin", "singapore", "australia", "sydney", "tokyo", "israel",
    "tel aviv", "poland", "spain", "portugal", "brazil", "mexico", "netherlands", "sweden",
    "zurich", "munich", "timezone", "time zone", "utc", "gmt", "est", "pst", "cet",
    "relocation", "visa", "office", "based in",
]
APPLY_HINT = [
    "apply", "career", "jobs", "job-", "/job", "greenhouse.io", "lever.co", "ashbyhq",
    "workable", "recruitee", "breezy.hr", "bamboohr", "smartrecruiters", "workday",
    "join.com", "hire", "recruit", "notion.site", "gethired", "teamtailor", "rippling",
    "wellfound", "ycombinator.com/companies",
]
URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
SALARY_RE = re.compile(r"[$€£₹]|\b\d{2,3}\s?k\b|\bsalary\b|\bcompensation\b|\bequity\b|/hr\b|per hour", re.I)
# A role phrase to fall back on when the header carries no explicit role field.
ROLE_RE = re.compile(
    r"\b(?:(?:senior|sr\.?|staff|principal|lead|junior|jr\.?|founding|entry[- ]level|"
    r"mid[- ]level|head of)\s+){0,2}"
    r"(?:(?:ai|ml|machine learning|llm|agentic|automation|applied ai|generative ai|genai|"
    r"forward[- ]deployed|full[- ]?stack|back[- ]?end|front[- ]?end|platform|infrastructure|"
    r"data|software|product|solutions|devops|site reliability|security|mobile|ios|android|"
    r"systems|research)[\s/&+-]{1,3}){0,3}"
    r"(?:engineer|developer|scientist|architect|researcher)s?\b", re.I)
# Someone advertising themselves rather than a job (these show up in both thread kinds).
SEEKER_RE = re.compile(
    r"^\s*(seeking work\b|looking for (?:work|a (?:new )?(?:role|job|position))\b|"
    r"open to work\b|available for (?:hire|work|contract)\b|hire me\b)|"
    r"\bi(?:'m| am)\s+(?:a|an)\s+[\w\s/-]{0,40}\b"
    r"(?:developer|engineer|designer|freelancer|consultant|architect)\b", re.I)
EQUITY_ONLY_RE = re.compile(r"equity[\s‑-]*only|unpaid|no salary|profit[- ]share only", re.I)
# The "Who wants to be hired?" resume template, which strays into the hiring threads.
SEEKER_TEMPLATE_RE = re.compile(
    r"willing to relocate\s*:|(?:r[ée]sum[ée]|\bcv)\s*:.{0,200}?\bremote\s*:|"
    r"\bremote\s*:.{0,200}?(?:r[ée]sum[ée]|\bcv)\s*:", re.I | re.S)


# ---------------------------------------------------------------- thread discovery
def target_months(n: int) -> list[tuple[int, int]]:
    """The current month plus the n-1 preceding months, as (year, month)."""
    out, y, m = [], date.today().year, date.today().month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def month_title(y: int, m: int) -> str:
    return f"({datetime(y, m, 1).strftime('%B')} {y})"


def discover_threads(sess, months: int) -> list[dict]:
    """Return [{id, title, kind}] for the hiring + freelancer threads of recent months."""
    wanted = [month_title(y, m) for y, m in target_months(months)]
    oldest = int(time.time()) - 86400 * 31 * (months + 2)
    found: dict[str, dict] = {}

    # 1. "Who is hiring?" — always posted by the whoishiring bot.
    url = (f"{ALGOLIA}/search_by_date?tags=story,author_whoishiring"
           f"&numericFilters=created_at_i>{oldest}&hitsPerPage=60")
    for h in sess.get(url, timeout=60).json().get("hits", []):
        t = h.get("title") or ""
        if "who is hiring" in t.lower() and any(w in t for w in wanted):
            found[h["objectID"]] = {"id": h["objectID"], "title": t, "kind": "hiring",
                                    "comments": h.get("num_comments") or 0}

    # 2. "Freelancer? Seeking freelancer?" — since Nov 2025 this is posted by a HUMAN
    #    (jon_north), not the whoishiring bot, so it must be found by title, not author.
    url = (f"{ALGOLIA}/search_by_date?query=%22Seeking%20freelancer%22&tags=story"
           f"&numericFilters=created_at_i>{oldest}&hitsPerPage=60")
    for h in sess.get(url, timeout=60).json().get("hits", []):
        t = h.get("title") or ""
        if "seeking freelancer" in t.lower() and any(w in t for w in wanted):
            found[h["objectID"]] = {"id": h["objectID"], "title": t, "kind": "freelancer",
                                    "comments": h.get("num_comments") or 0}
    return sorted(found.values(), key=lambda d: -int(d["id"]))


def fetch_item(sess, item_id: str) -> dict:
    """Fetch the full comment tree, cached on disk so reruns don't refetch."""
    os.makedirs(ITEM_CACHE, exist_ok=True)
    path = os.path.join(ITEM_CACHE, f"{item_id}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    data = sess.get(f"{ALGOLIA}/items/{item_id}", timeout=120).json()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    time.sleep(1.0)
    return data


# ---------------------------------------------------------------- comment parsing
def to_text(raw_html: str) -> str:
    t = re.sub(r"<p>", "\n\n", raw_html or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return htmllib.unescape(t)


def has_role_word(s: str) -> bool:
    return any(w in f" {s.lower()} " for w in ROLE_WORDS)


def is_type_field(s: str) -> bool:
    core = re.sub(r"[^a-z0-9/\- ]", " ", s.lower())
    core = re.sub(r"\d+\s*[-–]\s*\d+\s*(hrs?|hours?)?/?\s*(wk|week)?", " ", core)
    core = re.sub(r"\s+", " ", core).strip(" /-")
    if not core or len(core) > 40:
        return False
    tokens = [x.strip() for x in re.split(r"[/,]| and ", core) if x.strip()]
    # Word boundaries again: a substring test makes "w2" match "(YC W24)".
    return bool(tokens) and all(
        any(re.search(rf"\b{re.escape(w)}\b", x) for w in TYPE_WORDS) for x in tokens)


GEO_RE = re.compile(r"\b(?:" + "|".join(re.escape(g).replace(r"\ ", " ") for g in GEO_HINT) + r")\b", re.I)


def is_geo_field(s: str) -> bool:
    # Word boundaries matter: a substring test makes "us" match "Industries"/"Cluster".
    if GEO_RE.search(s):
        return True
    return bool(re.search(r"[A-Za-z]+,\s*[A-Z]{2}\b", s))


def remote_flag(header: str, body: str) -> bool | None:
    h = header.lower()
    if re.search(r"\b(not|no)\s+remote\b|\bremote:\s*no\b|\bnon-remote\b", h):
        return False
    if "remote" in h:
        return True
    if re.search(r"\bonsite\b|\bon-site\b|\bin-office\b", h):
        return False
    if "hybrid" in h:
        return False
    b = body.lower()[:1200]
    if re.search(r"\bremote:\s*(yes|ok|possible|friendly)\b|\bfully remote\b|\bremote-first\b", b):
        return True
    return None


def split_fields(line: str) -> list[str]:
    """Split the header on '|', ignoring pipes inside brackets ('REMOTE (US | EU)')."""
    out, buf, depth = [], [], 0
    for ch in line:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf).strip())
    return [f for f in out if f]


HIRING_CUE_RE = re.compile(
    r"(?:hiring|looking for|seeking|we need|join(?:ing)? (?:us|our)|roles?\s*:|"
    r"positions?\s*:|open (?:roles?|positions?)|apply)[^.]{0,60}$", re.I)


def guess_title(text: str) -> str | None:
    """Pull a role phrase out of the post body when the header states no role.

    Prefers a phrase that is itself an AI/automation fit, then one that follows hiring
    language — otherwise prose like "architects behind the World Trade Center" wins.
    """
    cands = []
    for m in ROLE_RE.finditer(text[:6000]):
        c = m.group(0).strip()
        if 4 <= len(c) <= 90:
            cands.append((c, bool(HIRING_CUE_RE.search(text[max(0, m.start() - 70):m.start()]))))
    if not cands:
        return None
    for want_ai, want_cue in ((True, True), (True, False), (False, True), (False, False)):
        for c, cue in cands:
            if (common.is_fit(c) == want_ai) and (cue == want_cue):
                return c
    return cands[0][0]


COMPANY_SENTENCE_RE = re.compile(
    r"^\s*([A-Z][\w&.'\-]*(?:\s+[A-Z][\w&.'\-]*){0,3})\s+"
    r"(?:is|are|was|builds?|makes?|helps?|provides?)")


def company_from_text(text: str) -> str:
    """Prose posts usually open "<Company> is/builds ..." — use that before a domain."""
    m = COMPANY_SENTENCE_RE.match(text.strip()[:200])
    if m and 2 <= len(m.group(1)) <= 60:
        return m.group(1).strip(" .,")
    m = re.search(r"([A-Z][\w&.'\-]{1,30}(?:\s+[A-Z][\w&.'\-]{1,30}){0,2})\s+is hiring",
                  text[:300])
    return m.group(1).strip(" .,") if m else ""


SKIP_HOST = ("news.ycombinator", "greenhouse.io", "lever.co", "ashbyhq", "workable",
             "notion.site", "google.com", "linkedin.com", "docs.google", "techcrunch",
             "forbes", "bloomberg", "medium.com", "substack", "github.com", "twitter.com",
             "x.com", "youtube.com", "wellfound.com", "teamtailor", "recruitee",
             "smartrecruiters", "bamboohr", "breezy.hr", "kula.ai")


def company_from_urls(urls: list[str]) -> str:
    for u in urls:
        m = re.match(r"https?://(?:www\.|jobs\.|careers\.|boards\.|apply\.)?([^/]+)", u, re.I)
        if not m:
            continue
        host = m.group(1)
        if any(h in host for h in SKIP_HOST):
            continue
        return host.split(".")[0]
    return ""


def clean_company(s: str) -> str:
    s = URL_RE.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"\s*\|\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—•*,")
    return s[:120]


def split_roles(field: str) -> list[str]:
    parts = [p.strip(" -–—•*") for p in re.split(r",|;| & (?=[A-Z][a-z]+ (?:Engineer|Developer))", field)]
    roles = [p for p in parts if p and has_role_word(p)]
    if len(roles) <= 1:
        one = field.strip(" -–—•*")
        return [one] if one else []
    return [r[:120] for r in roles]


def parse_comment(c: dict, thread: dict) -> list[dict]:
    raw = c.get("text")
    if not raw or not c.get("author"):
        return []                                     # dead / deleted
    text = to_text(raw)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return []
    header = lines[0]

    if thread["kind"] == "freelancer":
        # These threads mix "SEEKING WORK" (job seekers) with "SEEKING FREELANCER"
        # (people hiring). Only the latter is a posting.
        if not re.match(r"\s*seeking\s+freelancer", header, re.I):
            return []
        header = re.sub(r"^\s*seeking\s+freelancer\s*[|:,-]?\s*", "", header, flags=re.I)
        if not header:
            header = lines[1] if len(lines) > 1 else ""
    elif SEEKER_RE.search(text[:300]) or SEEKER_TEMPLATE_RE.search(text[:700]):
        return []                                     # a job seeker, not a posting
    if EQUITY_ONLY_RE.search(text[:400]) and not re.search(r"\$\s?\d{2,3}\s?k|\$\s?\d{2,3},\d{3}", text[:600]):
        return []                                     # unpaid / equity-only, not a job

    urls, seen = [], set()
    for u in re.findall(r'href="([^"]+)"', raw) + URL_RE.findall(htmllib.unescape(raw)):
        u = htmllib.unescape(u).rstrip(".,);")
        if u.lower().startswith("http") and u not in seen:
            seen.add(u)
            urls.append(u)

    fields = split_fields(header)
    # A poster who ran prose straight into the header line leaves one very long field;
    # that is body text, not a header field.
    prose = [f for f in fields if len(f) > 90]
    fields = [f for f in fields if len(f) <= 90]
    note = f"HN thread {thread['id']} — {thread['title']}"

    company = clean_company(fields[0]) if fields else ""
    rest = fields[1:]
    if company and (is_geo_field(company) or is_type_field(company)) and len(company) < 30:
        rest = fields                                 # company field omitted by the poster
        company = ""

    role_field, locs, sal, etype, leftovers = "", [], None, None, []
    for f in rest:
        f = f.strip(" -–—•*")
        if not f:
            continue
        if URL_RE.fullmatch(f) or (URL_RE.match(f) and len(URL_RE.sub("", f).strip()) < 3):
            continue
        if etype is None and is_type_field(f):
            etype = common.emp_type(f)
            continue
        if sal is None and SALARY_RE.search(f) and not has_role_word(f):
            sal = f
            continue
        if has_role_word(f) and not is_type_field(f) and not role_field:
            role_field = URL_RE.sub("", f).strip(" -–—•*")
            continue
        if is_geo_field(f):
            locs.append(URL_RE.sub("", f).strip(" -–—•*"))
            continue
        leftovers.append(f)

    body = "\n".join(lines[1:]) + "\n" + "\n".join(prose)
    remote = remote_flag(header, body)
    location = " / ".join(x for x in locs if x)
    if not location and remote:
        location = "Remote"
    if not company:
        company = company_from_text(text)
        note += "; company not in the header, taken from the post text" if company else ""
    if not company:
        company = company_from_urls(urls)
        note += "; company not in the header, taken from the posted link" if company else ""
    if not company:
        return []                                     # unattributable — do not guess

    if not role_field:
        role_field = guess_title(text) or ""
        note += "; role not in the header, inferred from the post body" if role_field else ""
    if not role_field:
        return []                                     # no role stated anywhere
    if leftovers:
        note += "; unparsed header fields: " + " | ".join(leftovers)[:200]

    url = urls[0] if urls else f"https://news.ycombinator.com/item?id={c['id']}"
    apply_url = next((u for u in urls if any(k in u.lower() for k in APPLY_HINT)), None)
    if apply_url == url:
        apply_url = None
    posted_at = (c.get("created_at") or "")[:10] or None

    roles = split_roles(role_field)
    fitting = [r for r in roles if common.is_fit(r, text)]
    if not fitting and common.is_fit(role_field, text):
        fitting = [role_field]
    if not fitting:
        # Umbrella headers ("Multiple roles", "Fulltime | SF | Onsite") hide the real
        # title in the body — go find it before giving up on the post.
        g = guess_title(text)
        if g and common.is_fit(g, text):
            fitting = [g]
            note += "; role refined from the post body"
    if not fitting:
        return []

    rows = []
    for i, title in enumerate(fitting):
        native = str(c["id"]) if len(fitting) == 1 else f"{c['id']}-{i}"
        rows.append(common.record(
            SOURCE, title, company, url,
            apply_url=apply_url, location=location, remote=remote,
            employment_type=etype, salary=sal, posted_at=posted_at, description=text,
            tags=["hn", thread["kind"]], open_status="unverified", notes=note,
            native_id=native,
        ))
    return rows


# ---------------------------------------------------------------- main
def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = common.cli("Hacker News hiring threads via the Algolia HN API")
    months = int(os.environ.get("HN_MONTHS", "2"))
    sess = common.session()

    threads = discover_threads(sess, months)
    if not threads:
        print("ERROR: no hiring threads discovered", file=sys.stderr)
        sys.exit(1)

    rows, seen_ids, seen_jobs, dupes = [], set(), set(), 0
    for th in threads:
        item = fetch_item(sess, th["id"])
        children = item.get("children") or []
        kept_here = 0
        for c in children:
            for r in parse_comment(c, th):
                if r["id"] in seen_ids or not common.recent(r["posted_at"]):
                    continue
                # Threads run monthly and companies repost; keep only the newest copy.
                job = (r["company"].lower(), r["title"].lower())
                if job in seen_jobs:
                    dupes += 1
                    continue
                seen_ids.add(r["id"])
                seen_jobs.add(job)
                rows.append(r)
                kept_here += 1
        print(f"thread {th['id']} [{th['kind']}] {th['title']}: "
              f"{len(children)} top-level comments -> {kept_here} rows", file=sys.stderr)
        if args.limit and len(rows) >= args.limit:
            rows = rows[:args.limit]
            break

    remote_n = sum(1 for r in rows if r["remote"])
    print(f"total rows kept: {len(rows)} ({remote_n} remote); "
          f"{dupes} cross-month reposts dropped", file=sys.stderr)
    common.write_jsonl(args.out, rows)


if __name__ == "__main__":
    main()
