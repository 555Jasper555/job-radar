#!/usr/bin/env python3
"""job-radar source: remote job boards (keyless HTTP APIs + RSS).

One function per board. Each is wrapped by run_board() so a single broken board
never kills the run. Status for every board goes to stderr.

    set PYTHONIOENCODING=utf-8
    python sources/remote_boards.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/remote_boards.jsonl
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("sources", 1)[0] + "sources")
import common  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

S = common.session()
BROWSERISH = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

STATUS: list[dict] = []


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def iso(ts) -> str | None:
    """Epoch seconds / ms / ISO-ish string -> YYYY-MM-DD."""
    if ts in (None, "", 0, "0"):
        return None
    try:
        if isinstance(ts, str) and not ts.strip().isdigit():
            t = ts.strip()
            m = re.match(r"(\d{4}-\d{2}-\d{2})", t)
            if m:
                return m.group(1)
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(t, fmt).date().isoformat()
                except ValueError:
                    continue
            return None
        n = int(ts)
        if n > 10_000_000_000:  # milliseconds
            n //= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def get(url, *, headers=None, timeout=60, tries=2):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, headers=headers, timeout=timeout)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + i * 3)
    raise last  # type: ignore[misc]


def keep(rows, source, seen_total, note=""):
    STATUS.append({"board": source, "seen": seen_total, "kept": len(rows), "note": note})
    return rows


# --------------------------------------------------------------------------- #
# Boards
# --------------------------------------------------------------------------- #

def board_remoteok() -> list[dict]:
    out, seen, codes = [], 0, []
    # The bare /api feed is diluted with non-tech aggregate listings; the tag
    # feeds are where the engineering roles actually live.
    tags = ["", "ai", "machine-learning", "engineer", "dev", "python",
            "backend", "senior", "software", "saas"]
    for tag in tags:
        url = "https://remoteok.com/api" + (f"?tag={tag}" if tag else "")
        r = get(url)
        codes.append(str(r.status_code))
        if r.status_code != 200:
            continue
        items = r.json()
        time.sleep(0.4)
        for j in items:
            if not isinstance(j, dict) or "position" not in j:
                continue
            seen += 1
            title, desc = j.get("position", ""), j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("date") or j.get("epoch"))
            if not common.recent(posted):
                continue
            loc = j.get("location", "") or ""
            sal = j.get("salary") or ""
            if not sal and j.get("salary_min"):
                sal = f"${j.get('salary_min')} - ${j.get('salary_max')}"
            out.append(common.record(
                "remoteok", title, j.get("company", ""), j.get("url", ""),
                apply_url=j.get("apply_url"), location=loc, remote=True,
                salary=sal or None, posted_at=posted, description=desc,
                tags=j.get("tags") or [], open_status="open",
                native_id=str(j.get("id") or ""),
            ))
    return keep(out, "remoteok", seen, f"HTTP {'/'.join(codes)}")


def board_remotive() -> list[dict]:
    out, seen, codes = [], 0, []
    for url in ("https://remotive.com/api/remote-jobs?category=software-dev&limit=500",
                "https://remotive.com/api/remote-jobs?search=ai&limit=500",
                "https://remotive.com/api/remote-jobs?search=machine+learning&limit=200",
                "https://remotive.com/api/remote-jobs?search=llm&limit=200",
                "https://remotive.com/api/remote-jobs?search=agent&limit=200",
                "https://remotive.com/api/remote-jobs?search=automation&limit=200",
                "https://remotive.com/api/remote-jobs?category=data&limit=200"):
        r = get(url)
        codes.append(str(r.status_code))
        if r.status_code != 200:
            continue
        for j in r.json().get("jobs", []):
            seen += 1
            title, desc = j.get("title", ""), j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("publication_date"))
            if not common.recent(posted):
                continue
            out.append(common.record(
                "remotive", title, j.get("company_name", ""), j.get("url", ""),
                location=j.get("candidate_required_location", "") or "", remote=True,
                employment_type=common.emp_type(j.get("job_type", "") or title),
                salary=j.get("salary") or None, posted_at=posted, description=desc,
                tags=j.get("tags") or [], open_status="open",
                native_id=str(j.get("id") or ""),
            ))
    return keep(out, "remotive", seen, f"HTTP {'/'.join(codes)}")


def board_himalayas() -> list[dict]:
    # The API caps a page at 20 regardless of `limit`, ignores every filter
    # param, and is strictly newest-first. Page until we run past the cutoff.
    out, seen, code = [], 0, "-"
    cursor, pages, stale_pages = None, 0, 0
    while pages < 3000:
        url = "https://himalayas.app/jobs/api?limit=20"
        if cursor:
            url += f"&cursor={cursor}"
        r = get(url)
        code = str(r.status_code)
        if r.status_code != 200:
            break
        d = r.json()
        jobs = d.get("jobs", [])
        if not jobs:
            break
        page_dates = [iso(j.get("pubDate")) for j in jobs]
        fresh = [p for p in page_dates if p and p >= common.CUTOFF]
        stale_pages = stale_pages + 1 if not fresh else 0
        stop = stale_pages >= 3
        for j in jobs:
            seen += 1
            title, desc = j.get("title", ""), j.get("description", "") or j.get("excerpt", "") or ""
            posted = iso(j.get("pubDate"))
            if not common.is_fit(title, desc):
                continue
            if not common.recent(posted):
                continue
            locs = j.get("locationRestrictions") or []
            loc = ", ".join(locs) if locs else "Worldwide"
            smin, smax = j.get("minSalary"), j.get("maxSalary")
            sal = f"{smin} - {smax} {j.get('currency') or ''}".strip() if smin else None
            sen = j.get("seniority") or []
            out.append(common.record(
                "himalayas", title, j.get("companyName", ""), j.get("guid") or j.get("applicationLink", ""),
                apply_url=j.get("applicationLink"), location=loc, remote=True,
                employment_type=common.emp_type(j.get("employmentType", "") or ""),
                salary=sal, posted_at=posted, description=desc,
                tags=(j.get("categories") or [])[:12] + (sen if isinstance(sen, list) else []),
                open_status="open",
            ))
        cursor = d.get("nextCursor")
        pages += 1
        if not cursor or stop:
            break
        time.sleep(0.08)
    floor = min([r["posted_at"] for r in out if r["posted_at"]], default="-")
    return keep(out, "himalayas", seen, f"HTTP {code}, {pages} pages x20, back to {floor}")


def board_jobicy() -> list[dict]:
    out, seen, codes = [], 0, []
    urls = [
        "https://jobicy.com/api/v2/remote-jobs?count=100&industry=engineering",
        "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev",
        "https://jobicy.com/api/v2/remote-jobs?count=100&tag=artificial%20intelligence",
        "https://jobicy.com/api/v2/remote-jobs?count=100&tag=machine%20learning",
        "https://jobicy.com/api/v2/remote-jobs?count=100&tag=automation",
        "https://jobicy.com/api/v2/remote-jobs?count=100",
    ]
    for url in urls:
        r = get(url)
        codes.append(str(r.status_code))
        if r.status_code != 200:
            continue
        for j in r.json().get("jobs", []):
            seen += 1
            title = j.get("jobTitle", "")
            desc = j.get("jobDescription", "") or j.get("jobExcerpt", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("pubDate"))
            if not common.recent(posted):
                continue
            geo = j.get("jobGeo", "") or ""
            jt = j.get("jobType")
            jt = ", ".join(jt) if isinstance(jt, list) else (jt or "")
            smin, smax = j.get("annualSalaryMin"), j.get("annualSalaryMax")
            sal = f"{smin} - {smax} {j.get('salaryCurrency') or 'USD'}" if smin else None
            lvl = j.get("jobLevel")
            lvl = [lvl] if isinstance(lvl, str) and lvl else (lvl or [])
            out.append(common.record(
                "jobicy", title, j.get("companyName", ""), j.get("url", ""),
                location=geo, remote=True, employment_type=common.emp_type(jt),
                salary=sal, posted_at=posted, description=desc,
                tags=(j.get("jobIndustry") or []) + lvl, open_status="open",
                native_id=str(j.get("id") or ""),
            ))
        time.sleep(0.3)
    return keep(out, "jobicy", seen, f"HTTP {'/'.join(codes)}")


def board_arbeitnow() -> list[dict]:
    out, seen, code = [], 0, "-"
    for page in range(1, 26):
        r = get(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        code = str(r.status_code)
        if r.status_code != 200:
            break
        data = r.json().get("data", [])
        if not data:
            break
        page_dates = [iso(j.get("created_at")) for j in data]
        if page_dates and not any(p and p >= common.CUTOFF for p in page_dates):
            break
        for j in data:
            seen += 1
            title, desc = j.get("title", ""), j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("created_at"))
            if not common.recent(posted):
                continue
            out.append(common.record(
                "arbeitnow", title, j.get("company_name", ""), j.get("url", ""),
                location=j.get("location", "") or "", remote=bool(j.get("remote")),
                employment_type=common.emp_type(" ".join(j.get("job_types") or []) or title),
                posted_at=posted, description=desc, tags=j.get("tags") or [],
                open_status="open", native_id=str(j.get("slug") or "")[:60],
            ))
        time.sleep(0.3)
    return keep(out, "arbeitnow", seen, f"HTTP {code}")


def board_workingnomads() -> list[dict]:
    out, seen = [], 0
    r = get("https://www.workingnomads.com/api/exposed_jobs/")
    if r.status_code == 200:
        for j in r.json():
            seen += 1
            title, desc = j.get("title", ""), j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("pub_date"))
            if not common.recent(posted):
                continue
            cats = j.get("category_name") or ""
            out.append(common.record(
                "workingnomads", title, j.get("company_name", ""), j.get("url", ""),
                location=j.get("location", "") or "", remote=True,
                posted_at=posted, description=desc,
                tags=[c.strip() for c in str(cats).split(",") if c.strip()],
                open_status="open",
            ))
    return keep(out, "workingnomads", seen, f"HTTP {r.status_code}")


# ---------------------------- RSS helpers --------------------------------- #

def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    for it in root.iter():
        if it.tag.split("}")[-1] != "item":
            continue
        d: dict = {}
        for ch in it:
            tag = ch.tag.split("}")[-1]
            txt = "".join(ch.itertext()).strip()
            if tag in d and txt:
                d[tag] = d[tag] + " | " + txt
            else:
                d[tag] = txt
        items.append(d)
    return items


def board_wwr() -> list[dict]:
    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/remote-jobs.rss",
    ]
    out, seen, codes = [], 0, []
    for f in feeds:
        r = get(f, headers=BROWSERISH)
        codes.append(str(r.status_code))
        if r.status_code != 200:
            continue
        for it in parse_rss(r.text):
            seen += 1
            raw_title = it.get("title", "")
            company, title = "", raw_title
            if ":" in raw_title:
                company, title = raw_title.split(":", 1)
                company, title = company.strip(), title.strip()
            desc = it.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(it.get("pubDate"))
            if not common.recent(posted):
                continue
            loc = it.get("region") or it.get("category") or ""
            out.append(common.record(
                "weworkremotely", title, company, it.get("link", ""),
                location=loc, remote=True, posted_at=posted, description=desc,
                tags=[t for t in [it.get("category", "")] if t], open_status="open",
                notes="company parsed from RSS title prefix" if company else "company not in feed",
            ))
        time.sleep(0.3)
    return keep(out, "weworkremotely", seen, f"HTTP {'/'.join(codes)}")


def board_jobspresso() -> list[dict]:
    out, seen = [], 0
    r = get("https://jobspresso.co/?feed=job_feed", headers=BROWSERISH)
    if r.status_code == 200:
        for it in parse_rss(r.text):
            seen += 1
            title = it.get("title", "")
            desc = it.get("encoded") or it.get("description", "") or ""
            company = it.get("company") or it.get("creator") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(it.get("pubDate"))
            if not common.recent(posted):
                continue
            out.append(common.record(
                "jobspresso", title, company, it.get("link", ""),
                location=it.get("location", "") or "Remote", remote=True,
                employment_type=common.emp_type(it.get("job_type", "") or title),
                posted_at=posted, description=desc, open_status="open",
            ))
    return keep(out, "jobspresso", seen, f"HTTP {r.status_code}")


def board_aijobs() -> list[dict]:
    """ai-jobs.net / aijobs.net — probe every published feed shape."""
    tried, out, seen = [], [], 0
    # The site is a django-htmx SPA; every path below returns the identical
    # 10,836-byte shell to a non-browser client, htmx headers included.
    for url in ("https://aijobs.net/feed/", "https://ai-jobs.net/feed/",
                "https://aijobs.net/rss/", "https://aijobs.net/api/",
                "https://aijobs.net/hiring/jobs/", "https://aijobs.net/api/list/"):
        try:
            r = get(url, headers={**BROWSERISH, "HX-Request": "true"}, tries=1)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url} ERR {type(e).__name__}")
            continue
        body = r.text[:200].lstrip().lower()
        if r.status_code == 200 and (body.startswith("<?xml") or body.startswith("<rss")):
            for it in parse_rss(r.text):
                seen += 1
                title, desc = it.get("title", ""), it.get("description", "") or ""
                if not common.is_fit(title, desc):
                    continue
                posted = iso(it.get("pubDate"))
                if not common.recent(posted):
                    continue
                out.append(common.record(
                    "aijobs", title, it.get("creator") or "", it.get("link", ""),
                    location="", remote=None, posted_at=posted, description=desc,
                    open_status="unverified",
                ))
            tried.append(f"{url} 200 RSS")
            break
        tried.append(f"{url} {r.status_code} {'HTML-SPA' if body.startswith('<!doctype') else 'other'}")
    return keep(out, "aijobs", seen, "; ".join(tried))


def board_remoteio() -> list[dict]:
    tried = []
    for url in ("https://www.remote.io/remote-jobs.rss", "https://remote.io/api/jobs",
                "https://remoteco.com/feed"):
        try:
            r = get(url, headers=BROWSERISH, tries=1)
            body = r.text[:120].lstrip().lower()
            tried.append(f"{url} {r.status_code} "
                         f"{'RSS' if body.startswith(('<?xml', '<rss')) else 'HTML/none'}")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url} ERR {type(e).__name__}")
    return keep([], "remote.io", 0, "; ".join(tried))


def board_ycombinator() -> list[dict]:
    """Work at a Startup: public Algolia creds are embedded in /jobs page source."""
    notes = []
    r = get("https://www.workatastartup.com/jobs", headers=BROWSERISH)
    notes.append(f"/jobs HTTP {r.status_code}")
    if r.status_code != 200:
        return keep([], "ycombinator", 0, "; ".join(notes))
    m = re.search(r'AlgoliaOpts\s*=\s*(\{.*?\})', r.text, re.S)
    if not m:
        notes.append("no AlgoliaOpts in page source")
        return keep([], "ycombinator", 0, "; ".join(notes))
    opts = json.loads(m.group(1))
    app, key = opts.get("app"), opts.get("key")
    notes.append(f"AlgoliaOpts app={app} tag_filters={opts.get('tag_filters')}")
    out, seen = [], 0
    for q in ("ai engineer", "agent", "llm", "automation engineer", "applied ai", ""):
        try:
            rr = S.post(
                f"https://{app}-dsn.algolia.net/1/indexes/WaaSPublicCompanyJob_production/query",
                headers={"X-Algolia-API-Key": key, "X-Algolia-Application-Id": app},
                json={"query": q, "hitsPerPage": 100}, timeout=45)
        except Exception as e:  # noqa: BLE001
            notes.append(f"algolia ERR {type(e).__name__}")
            break
        if rr.status_code != 200:
            notes.append(f"algolia {rr.status_code}")
            break
        d = rr.json()
        hits = d.get("hits", [])
        if not hits:
            continue
        for j in hits:
            seen += 1
            title = j.get("title") or j.get("role") or ""
            desc = j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            url = j.get("url") or f"https://www.workatastartup.com/jobs/{j.get('id', '')}"
            out.append(common.record(
                "ycombinator", title, j.get("company_name") or j.get("companyName", ""), url,
                location=j.get("location", "") or "", remote=bool(j.get("remote")),
                salary=j.get("salary_range") or None, posted_at=iso(j.get("created_at")),
                description=desc, open_status="unverified",
                native_id=str(j.get("id") or j.get("objectID") or ""),
            ))
    if not out and seen == 0:
        notes.append("index WaaSPublicCompanyJob_production exists but returns nbHits=0 "
                     "for the logged-out secured key (tagFilters=[[none]]) — login required")
    return keep(out, "ycombinator", seen, "; ".join(notes))


def board_wellfound() -> list[dict]:
    """Wellfound role pages ship the job list inside __NEXT_DATA__ (apolloState)."""
    pages = [
        "https://wellfound.com/role/r/ai-engineer",
        "https://wellfound.com/role/r/machine-learning-engineer",
        "https://wellfound.com/role/r/ai-developer",
        "https://wellfound.com/role/r/prompt-engineer",
        "https://wellfound.com/role/l/machine-learning-engineer/san-francisco",
        "https://wellfound.com/role/r/automation-engineer",
        "https://wellfound.com/role/l/ai-engineer/san-francisco",
    ]
    out, seen, notes = [], 0, []
    for p in pages:
        try:
            r = get(p, headers=BROWSERISH, tries=1)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{p.rsplit('/', 1)[-1]} ERR")
            continue
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if r.status_code != 200 or not m:
            notes.append(f"{p.rsplit('/', 1)[-1]} {r.status_code} no-NEXT_DATA")
            continue
        try:
            data = json.loads(m.group(1))["props"]["pageProps"]["apolloState"]["data"]
        except Exception:  # noqa: BLE001
            notes.append(f"{p.rsplit('/', 1)[-1]} apolloState missing")
            continue
        job2co = {}
        for k, v in data.items():
            if k.startswith("StartupResult:") and isinstance(v, dict):
                for ref in v.get("highlightedJobListings") or []:
                    if isinstance(ref, dict) and ref.get("__ref"):
                        job2co[ref["__ref"]] = v.get("name", "")
        n = 0
        for k, j in data.items():
            if not k.startswith("JobListingSearchResult:") or not isinstance(j, dict):
                continue
            seen += 1
            n += 1
            title, desc = j.get("title", ""), j.get("description", "") or ""
            if not common.is_fit(title, desc):
                continue
            posted = iso(j.get("liveStartAt"))
            if not common.recent(posted):
                continue
            jid = k.split(":", 1)[1]
            locs = j.get("locationNames") or []
            acc = j.get("acceptedRemoteLocationNames") or []
            loc = ", ".join(locs or acc) or ("Remote" if j.get("remote") else "")
            comp = j.get("compensation")
            out.append(common.record(
                "wellfound", title, job2co.get(k, ""),
                f"https://wellfound.com/jobs/{jid}-{j.get('slug', '')}",
                location=loc, remote=bool(j.get("remote")),
                employment_type=common.emp_type(j.get("jobType", "") or title),
                salary=comp if isinstance(comp, str) else None,
                posted_at=posted, description=desc, open_status="unverified",
                native_id=jid,
                notes="from __NEXT_DATA__ apolloState on a public role page",
            ))
        notes.append(f"{p.rsplit('/', 1)[-1]} 200 {n} listings")
        time.sleep(1.0)
    return keep(out, "wellfound", seen, "; ".join(notes))


def board_otta() -> list[dict]:
    """Otta is now Welcome to the Jungle. One attempt at public search JSON."""
    tried = []
    for url in ("https://api.welcometothejungle.com/api/v1/organizations",
                "https://www.welcometothejungle.com/en/jobs?query=ai%20engineer",
                "https://app.welcometothejungle.com/api/v1/jobs?query=ai"):
        try:
            r = get(url, headers=BROWSERISH, tries=1)
            body = r.text[:120].lstrip().lower()
            kind = "JSON" if body.startswith(("{", "[")) else "HTML"
            tried.append(f"{url.split('//')[1][:48]} {r.status_code} {kind}")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url.split('//')[1][:48]} ERR {type(e).__name__}")
    tried.append("no keyless job-search endpoint; org list only, no postings")
    return keep([], "otta", 0, "; ".join(tried))


BOARDS = [
    ("remoteok", board_remoteok),
    ("remotive", board_remotive),
    ("himalayas", board_himalayas),
    ("jobicy", board_jobicy),
    ("arbeitnow", board_arbeitnow),
    ("workingnomads", board_workingnomads),
    ("weworkremotely", board_wwr),
    ("jobspresso", board_jobspresso),
    ("aijobs", board_aijobs),
    ("remote.io", board_remoteio),
    ("ycombinator", board_ycombinator),
    ("wellfound", board_wellfound),
    ("otta", board_otta),
]


def main() -> None:
    args = common.cli("remote job boards -> jsonl")
    all_rows: list[dict] = []
    for name, fn in BOARDS:
        t0 = time.time()
        try:
            rows = fn()
            all_rows.extend(rows)
            st = next((s for s in STATUS if s["board"] == name), {})
            log(f"[{name}] ok  seen={st.get('seen', '?')} kept={st.get('kept', len(rows))} "
                f"({time.time() - t0:.1f}s) {st.get('note', '')}")
        except Exception as e:  # noqa: BLE001
            STATUS.append({"board": name, "seen": 0, "kept": 0, "note": f"FAILED {type(e).__name__}: {e}"})
            log(f"[{name}] FAILED {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)

    seen_urls, deduped = set(), []
    for r in all_rows:
        u = (r.get("url") or "").rstrip("/")
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)
        deduped.append(r)

    if args.limit:
        deduped = deduped[: args.limit]
    common.write_jsonl(args.out, deduped)

    log("\n=== per-board ===")
    for s in STATUS:
        log(f"{s['board']:<16} seen={s['seen']:<6} kept={s['kept']:<5} {s['note']}")
    log(f"total rows (pre-dedupe) {len(all_rows)} -> after URL dedupe {len(deduped)}")


if __name__ == "__main__":
    main()
