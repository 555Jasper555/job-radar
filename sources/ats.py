#!/usr/bin/env python3
"""job-radar source: ATS job boards (Greenhouse, Lever, Ashby, Workable, SmartRecruiters).

Keyless, headless HTTP only. No browser, no paid API.

    set PYTHONIOENCODING=utf-8
    python sources/ats.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/ats.jsonl

Slug discovery is persisted to sources/ats_slugs.json so reruns are instant:
    python sources/ats.py --probe candidates.json      # probe + merge into ats_slugs.json
    python sources/ats.py --add greenhouse:anthropic   # add a known slug by hand
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SLUGS_PATH = os.path.join(HERE, "ats_slugs.json")
ATSES = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]

S = common.session()

# Workable enforces a per-IP DAILY cap and answers 429 with `Retry-After: ~86400`.
# The shared session honors that header and would sleep for a full day, so Workable gets
# its own session that never retries a 429 and never sleeps on Retry-After: a capped day
# costs one request per board instead of hanging the run.
import requests  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

SW = requests.Session()
SW.headers.update({"User-Agent": common.UA, "Accept": "application/json, */*;q=0.8"})
_wretry = Retry(total=2, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504],
                allowed_methods=["GET", "POST"], respect_retry_after_header=False)
SW.mount("https://", HTTPAdapter(max_retries=_wretry))

_locks = {a: threading.Lock() for a in ATSES}
_last = {a: 0.0 for a in ATSES}


def _sess(ats):
    return SW if ats == "workable" else S


# Workable's public API rate-limits aggressively; the rest tolerate ~3 req/s.
GAP = {"greenhouse": 0.30, "lever": 0.30, "ashby": 0.30,
       "workable": 1.20, "smartrecruiters": 0.50}


def _polite(ats: str) -> None:
    """Serialize + space out requests per ATS host."""
    with _locks[ats]:
        dt = time.time() - _last[ats]
        gap = GAP.get(ats, 0.30)
        if dt < gap:
            time.sleep(gap - dt)
        _last[ats] = time.time()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class _Dead:
    """Stand-in response so a transport failure degrades one board, not the run."""
    status_code = 0
    text = ""

    def json(self):
        return {}


def _get(ats, url, **kw):
    _polite(ats)
    try:
        return _sess(ats).get(url, timeout=kw.pop("timeout", 25), **kw)
    except Exception:
        return _Dead()


def _post(ats, url, **kw):
    _polite(ats)
    try:
        return _sess(ats).post(url, timeout=kw.pop("timeout", 25), **kw)
    except Exception:
        return _Dead()


def iso_day(v) -> str | None:
    """Normalize anything date-ish to YYYY-MM-DD."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            n = float(v)
            if n > 1e11:  # milliseconds
                n /= 1000.0
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
        s = str(v).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        s = str(v)
        return s[:10] if len(s) >= 10 and s[4:5] == "-" else None


def pretty(slug: str) -> str:
    s = slug.replace("-", " ").replace("_", " ").strip()
    return " ".join(w if (w[:1].isupper()) else w.capitalize() for w in s.split())


# --------------------------------------------------------------------------- probes
def probe_greenhouse(slug):
    r = _get("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if r.status_code != 200:
        return None
    return len(r.json().get("jobs") or [])


def probe_lever(slug):
    r = _get("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if r.status_code != 200:
        return None
    j = r.json()
    return len(j) if isinstance(j, list) else None


def probe_ashby(slug):
    r = _get("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r.status_code != 200:
        return None
    try:
        return len(r.json().get("jobs") or [])
    except Exception:
        return None


def probe_workable(slug):
    r = _post("workable", f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
              json={"query": "", "location": [], "department": [], "worktype": [], "remote": []},
              headers={"Content-Type": "application/json"})
    if r.status_code != 200:
        return None
    try:
        return len(r.json().get("results") or [])
    except Exception:
        return None


def probe_smartrecruiters(slug):
    r = _get("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
    if r.status_code != 200:
        return None
    try:
        return int(r.json().get("totalFound") or 0)
    except Exception:
        return None


PROBES = {"greenhouse": probe_greenhouse, "lever": probe_lever, "ashby": probe_ashby,
          "workable": probe_workable, "smartrecruiters": probe_smartrecruiters}


# --------------------------------------------------------------------------- fetchers
def fetch_greenhouse(slug, note):
    out = []
    r = _get("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if r.status_code != 200:
        return out, f"HTTP {r.status_code}"
    company = pretty(slug)
    try:
        m = _get("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if m.status_code == 200:
            company = (m.json().get("name") or company).strip()
    except Exception:
        pass
    for j in r.json().get("jobs") or []:
        title = j.get("title") or ""
        desc = common.clean(html.unescape(j.get("content") or ""))
        if not common.is_fit(title, desc):
            continue
        posted = iso_day(j.get("first_published") or j.get("updated_at"))
        if not common.recent(posted):
            continue
        loc = ((j.get("location") or {}).get("name") or "")
        meta = " ".join(str((mm or {}).get("value") or "") for mm in (j.get("metadata") or []))
        tags = [(d or {}).get("name", "") for d in (j.get("departments") or []) if (d or {}).get("name")]
        out.append(common.record(
            "greenhouse", title, company, j.get("absolute_url") or "",
            apply_url=j.get("absolute_url"), location=loc,
            remote=True if "remote" in (loc + " " + title).lower() else None,
            salary=meta if "$" in meta else None, posted_at=posted, description=desc,
            tags=tags, open_status="open", notes=note, native_id=str(j.get("id") or ""),
        ))
    return out, None


def fetch_lever(slug, note):
    out = []
    r = _get("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if r.status_code != 200:
        return out, f"HTTP {r.status_code}"
    j = r.json()
    if not isinstance(j, list):
        return out, "bad payload"
    company = pretty(slug)
    for p in j:
        title = p.get("text") or ""
        cat = p.get("categories") or {}
        body = p.get("descriptionPlain") or p.get("description") or ""
        extra = "\n\n".join(f"{(l or {}).get('text','')}\n{common.clean((l or {}).get('content',''))}"
                            for l in (p.get("lists") or []))
        desc = common.clean(f"{body}\n\n{extra}\n\n{p.get('additionalPlain') or ''}")
        if not common.is_fit(title, desc):
            continue
        posted = iso_day(p.get("createdAt"))
        if not common.recent(posted):
            continue
        loc = cat.get("location") or ""
        wp = (p.get("workplaceType") or "").lower()
        sal = None
        sr = p.get("salaryRange") or {}
        if sr.get("min"):
            sal = f"{sr.get('currency','USD')} {sr.get('min')} - {sr.get('max')} {sr.get('interval','')}".strip()
        tags = [t for t in [cat.get("team"), cat.get("department"), cat.get("commitment")] if t]
        out.append(common.record(
            "lever", title, company, p.get("hostedUrl") or "",
            apply_url=p.get("applyUrl"), location=loc,
            remote=True if wp == "remote" else (False if wp == "onsite" else
                   (True if "remote" in loc.lower() else None)),
            employment_type=common.emp_type(cat.get("commitment") or "") if cat.get("commitment") else None,
            salary=sal, posted_at=posted, description=desc, tags=tags,
            open_status="open", notes=note, native_id=str(p.get("id") or ""),
        ))
    return out, None


def fetch_ashby(slug, note):
    out = []
    r = _get("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if r.status_code != 200:
        return out, f"HTTP {r.status_code}"
    j = r.json()
    if not isinstance(j, dict):
        return out, "bad payload"
    # the posting API carries no org name; the public board's <title> is "<Org> Jobs"
    company = pretty(slug)
    h = _get("ashby", f"https://jobs.ashbyhq.com/{slug}")
    m = re.search(r"<title>(.*?)</title>", getattr(h, "text", "") or "", re.S)
    if m:
        name = html.unescape(m.group(1)).strip()
        name = re.sub(r"\s+Jobs$", "", name).strip()
        if name:
            company = name
    for p in j.get("jobs") or []:
        title = p.get("title") or ""
        desc = common.clean(p.get("descriptionPlain") or html.unescape(p.get("descriptionHtml") or ""))
        if not common.is_fit(title, desc):
            continue
        posted = iso_day(p.get("publishedAt") or p.get("updatedAt"))
        if not common.recent(posted):
            continue
        comp = p.get("compensation") or {}
        sal = comp.get("compensationTierSummary")
        if not isinstance(sal, str):
            sal = None
        tags = [t for t in [p.get("department"), p.get("team")] if t]
        out.append(common.record(
            "ashby", title, company, p.get("jobUrl") or "",
            apply_url=p.get("applyUrl"), location=p.get("location") or "",
            remote=p.get("isRemote"), employment_type=common.emp_type(p.get("employmentType") or ""),
            salary=sal, posted_at=posted, description=desc, tags=tags,
            open_status="open", notes=note, native_id=str(p.get("id") or ""),
        ))
    return out, None


WORKABLE_MAX_PAGES = 15


def fetch_workable(slug, note):
    out = []
    body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    listings, seen_codes, token, first = [], set(), None, None
    for _ in range(WORKABLE_MAX_PAGES):
        b = dict(body)
        if token:
            b["token"] = token
        r = _post("workable", url, json=b, headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            if first is None:
                return out, f"HTTP {r.status_code}"
            break
        j = r.json()
        if first is None:
            first = j
        page = j.get("results") or []
        new = [p for p in page if p.get("shortcode") not in seen_codes]
        if not new:
            break
        seen_codes.update(p.get("shortcode") for p in new)
        listings.extend(new)
        token = j.get("nextPage")
        if not token or len(listings) >= (first.get("total") or 0):
            break
    company = pretty(slug)
    n_detail = 0
    for p in listings:
        title = p.get("title") or ""
        posted = iso_day(p.get("published") or p.get("createdAt"))
        if not common.recent(posted):
            continue
        # cheap reject before spending a detail request
        if not (common.is_fit(title, "") or any(g in title.lower() for g in common.GENERIC_TITLE)):
            continue
        code = p.get("shortcode") or ""
        desc = ""
        if n_detail < 80:
            n_detail += 1
            d = _get("workable", f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{code}")
            if d.status_code == 200:
                dj = d.json()
                desc = common.clean(html.unescape(" ".join(str(dj.get(k) or "") for k in
                                    ("description", "requirements", "benefits"))))
                if isinstance(dj.get("company"), dict):
                    company = dj["company"].get("title") or company
        if not common.is_fit(title, desc):
            continue
        lo = p.get("location") or {}
        loc = ", ".join(x for x in [lo.get("city"), lo.get("region"), lo.get("country")] if x)
        out.append(common.record(
            "workable", title, company, f"https://apply.workable.com/{slug}/j/{code}/",
            apply_url=f"https://apply.workable.com/{slug}/j/{code}/apply/", location=loc,
            remote=bool(p.get("remote")) if p.get("remote") is not None else None,
            employment_type=common.emp_type(p.get("type") or ""), posted_at=posted,
            description=desc, tags=[t for t in [p.get("department")] if t],
            open_status="open", notes=note, native_id=code,
        ))
    return out, None


SR_QUERIES = ["AI", "automation", "LLM", "agentic", "machine learning", "forward deployed"]


def fetch_smartrecruiters(slug, note):
    """Small boards are fetched whole; large ones are narrowed with keyword queries so a
    900-posting enterprise board costs 6 requests instead of 9 pages of irrelevant rows."""
    out = []
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    r = _get("smartrecruiters", base + "?limit=100")
    if r.status_code != 200:
        return out, f"HTTP {r.status_code}"
    j = r.json()
    total = int(j.get("totalFound") or 0)
    listings, seen_ids = [], set()

    def take(items):
        for p in items or []:
            if p.get("id") and p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                listings.append(p)

    take(j.get("content"))
    if total > 100:
        for q in SR_QUERIES:
            rq = _get("smartrecruiters", f"{base}?limit=100&q={quote(q)}")
            if rq.status_code == 200:
                take(rq.json().get("content"))

    n_detail = 0
    for p in listings:
        title = p.get("name") or ""
        posted = iso_day(p.get("releasedDate") or p.get("createdOn"))
        if not common.recent(posted):
            continue
        if not (common.is_fit(title, "") or any(g in title.lower() for g in common.GENERIC_TITLE)):
            continue
        pid = p.get("id") or ""
        comp_obj = p.get("company") or {}
        company = comp_obj.get("name") or pretty(slug)
        ident = comp_obj.get("identifier") or slug
        url = f"https://jobs.smartrecruiters.com/{ident}/{pid}"
        desc = ""
        if n_detail < 80:
            n_detail += 1
            d = _get("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}")
            if d.status_code == 200:
                secs = ((d.json().get("jobAd") or {}).get("sections") or {})
                desc = common.clean(html.unescape(" ".join(
                    str((secs.get(k) or {}).get("text") or "") for k in
                    ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))))
        if not common.is_fit(title, desc):
            continue
        lo = p.get("location") or {}
        loc = ", ".join(x for x in [lo.get("city"), lo.get("region"), lo.get("country")] if x)
        out.append(common.record(
            "smartrecruiters", title, company, url, apply_url=url, location=loc,
            remote=bool(lo.get("remote")) if lo.get("remote") is not None else None,
            employment_type=common.emp_type((p.get("typeOfEmployment") or {}).get("label") or ""),
            posted_at=posted, description=desc,
            tags=[t for t in [(p.get("function") or {}).get("label"),
                              (p.get("industry") or {}).get("label")] if t],
            open_status="open", notes=note, native_id=str(pid),
        ))
    return out, None


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "workable": fetch_workable, "smartrecruiters": fetch_smartrecruiters}


# --------------------------------------------------------------------------- slug store
def load_slugs() -> dict:
    if os.path.exists(SLUGS_PATH):
        with open(SLUGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    else:
        d = {}
    return {a: sorted(set(d.get(a) or [])) for a in ATSES}


def save_slugs(d: dict) -> None:
    with open(SLUGS_PATH, "w", encoding="utf-8") as f:
        json.dump({a: sorted(set(d.get(a) or [])) for a in ATSES}, f, indent=2)
        f.write("\n")


def run_probes(cands: dict) -> dict:
    """cands: {ats: [slug, ...]} -> {ats: [confirmed slugs]} (200 + >=1 live job)."""
    found = {a: [] for a in ATSES}

    def worker(ats):
        for slug in (cands.get(ats) or []):
            try:
                n = PROBES[ats](slug)
            except Exception as e:
                log(f"  probe err {ats}/{slug}: {type(e).__name__}")
                continue
            if n:
                found[ats].append(slug)
                log(f"  HIT {ats}/{slug} ({n} jobs)")

    with ThreadPoolExecutor(max_workers=len(ATSES)) as ex:
        list(ex.map(worker, ATSES))
    return found


# --------------------------------------------------------------------------- main
def main():
    import argparse
    p = argparse.ArgumentParser(description="ATS job boards -> jsonl")
    p.add_argument("--out")
    p.add_argument("--probe", help="JSON file {ats: [candidate slugs]} to probe and merge")
    p.add_argument("--add", action="append", default=[], help="ats:slug to add directly")
    p.add_argument("--only", help="comma-separated ATS subset to fetch")
    p.add_argument("--append", action="store_true", help="merge into an existing --out file")
    a = p.parse_args()

    slugs = load_slugs()

    for spec in a.add:
        ats, _, slug = spec.partition(":")
        if ats in slugs and slug:
            slugs[ats] = sorted(set(slugs[ats] + [slug]))

    if a.probe:
        with open(a.probe, encoding="utf-8") as f:
            cands = json.load(f)
        cands = {k: [s for s in (v or []) if s not in slugs.get(k, [])] for k, v in cands.items()}
        log(f"probing {sum(len(v) for v in cands.values())} candidates...")
        hits = run_probes(cands)
        for ats in ATSES:
            slugs[ats] = sorted(set(slugs[ats] + hits[ats]))
        log("probe hits: " + ", ".join(f"{x}={len(hits[x])}" for x in ATSES))

    if a.add or a.probe:
        save_slugs(slugs)
    if not a.out:
        log("board totals: " + ", ".join(f"{x}={len(slugs[x])}" for x in ATSES))
        return

    notes_path = os.path.join(HERE, "ats_slug_paths.json")
    paths = {}
    if os.path.exists(notes_path):
        with open(notes_path, encoding="utf-8") as f:
            paths = json.load(f)

    targets = [x.strip() for x in a.only.split(",")] if a.only else list(ATSES)
    targets = [t for t in targets if t in ATSES]

    rows, failures = [], []
    if a.append and os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        log(f"appending to {len(rows)} existing rows")
    lock = threading.Lock()

    def worker(ats):
        for slug in slugs[ats]:
            note = "slug via " + (paths.get(ats, {}).get(slug) or "curated-list probe")
            try:
                got, err = FETCHERS[ats](slug, note)
            except Exception as e:
                with lock:
                    failures.append(f"{ats}/{slug}: {type(e).__name__}: {e}")
                continue
            if err:
                with lock:
                    failures.append(f"{ats}/{slug}: {err}")
                continue
            with lock:
                rows.extend(got)
                if got:
                    log(f"  {ats}/{slug}: +{len(got)}")

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
        list(ex.map(worker, targets))

    seen, dedup = set(), []
    for r in rows:
        if r["id"] in seen or not r["url"]:
            continue
        seen.add(r["id"])
        dedup.append(r)
    dedup.sort(key=lambda r: (r.get("posted_at") or ""), reverse=True)
    common.write_jsonl(a.out, dedup)
    for f in failures:
        log("FAIL " + f)
    log(f"failures: {len(failures)}")


if __name__ == "__main__":
    main()
