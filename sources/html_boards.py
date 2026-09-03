#!/usr/bin/env python3
"""job-radar source: public HTML job boards.

Boards: LinkedIn (guest jobs API), SimplyHired, Built In, Dice, plus one-shot probes of
Indeed, Glassdoor, Google Jobs. ZipRecruiter lives in sources/ziprecruiter.py.

LinkedIn notes (probed 2026-09-03): the guest search pages 10 cards at a time and stops at
start=300 per query; f_TPR honours r86400 / r259200 / r604800 windows, so JOB_RADAR_DAYS=1
gives a cheap daily incremental run.

Headless, keyless HTTP only. No browser, no CDP, no cookies, no login, no paid API.
Detail pages are cached under <data>/raw/html-cache/<source>/<id>.html so reruns
skip refetches.

    set PYTHONIOENCODING=utf-8
    python sources/html_boards.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/html_boards.jsonl
"""
from __future__ import annotations

import html as htmllib
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_ROOT = r"C:/Users/endle/MyStuff/_job-radar-data"
CACHE_ROOT = os.path.join(DATA_ROOT, "raw", "html-cache")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

QUERIES = [
    "AI engineer", "AI developer", "AI automation engineer", "agentic engineer",
    "automation engineer AI", "LLM engineer", "applied AI engineer",
    "forward deployed engineer", "AI solutions engineer", "prompt engineer",
    "AI integration engineer", "AI operations",
]

STATUS: dict[str, dict] = {}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class HostBlocked(Exception):
    pass


class Fetcher:
    """Polite keyless HTTP. 1.5-3s per host, exponential backoff on 429,
    gives up on a host after 3 consecutive blocks."""

    BLOCK_LIMIT = 3

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update(BASE_HEADERS)
        self.last: dict[str, float] = {}
        self.blocks: dict[str, int] = {}
        self.counts: dict[str, int] = {}

    def _host(self, url: str) -> str:
        return urllib.parse.urlsplit(url).netloc.lower()

    def _wait(self, host: str) -> None:
        gap = random.uniform(1.5, 3.0)
        prev = self.last.get(host, 0.0)
        delta = time.time() - prev
        if delta < gap:
            time.sleep(gap - delta)
        self.last[host] = time.time()

    @staticmethod
    def _blocked(resp: requests.Response) -> str | None:
        if resp.status_code in (401, 403, 429, 999):
            return f"HTTP {resp.status_code}"
        body = resp.text[:4000].lower()
        if "just a moment" in body or "cf-challenge" in body or "enable javascript and cookies" in body:
            return "cloudflare challenge"
        final = resp.url.lower()
        if "/authwall" in final or "/login" in final or "/checkpoint/" in final or "signup?" in final:
            return f"redirect to {final[:80]}"
        return None

    def get(self, url: str, *, referer: str | None = None, timeout: int = 30,
            tries: int = 3, host_key: str | None = None) -> requests.Response:
        host = host_key or self._host(url)
        if self.blocks.get(host, 0) >= self.BLOCK_LIMIT:
            raise HostBlocked(f"{host}: {self.BLOCK_LIMIT} consecutive blocks, stopped")
        headers = {}
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        backoff = 30.0
        last_exc = None
        for attempt in range(tries):
            self._wait(host)
            try:
                r = self.s.get(url, headers=headers, timeout=timeout)
            except Exception as e:  # network hiccup
                last_exc = e
                time.sleep(min(backoff, 60))
                backoff *= 2
                continue
            self.counts[host] = self.counts.get(host, 0) + 1
            why = self._blocked(r)
            if why is None:
                self.blocks[host] = 0
                return r
            if r.status_code == 429 or "challenge" in why:
                log(f"   [{host}] {why} -> backing off {backoff:.0f}s (attempt {attempt + 1}/{tries})")
                if attempt < tries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            self.blocks[host] = self.blocks.get(host, 0) + 1
            r.block_reason = why  # type: ignore[attr-defined]
            return r
        raise HostBlocked(f"{host}: network failures ({last_exc})")


def cache_path(source: str, key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120]
    return os.path.join(CACHE_ROOT, source, f"{safe}.html")


def cached_get(f: Fetcher, source: str, key: str, url: str, *, referer: str | None = None) -> str | None:
    """Return page text, using the on-disk cache when present."""
    p = cache_path(source, key)
    if os.path.exists(p) and os.path.getsize(p) > 800:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            pass
    r = f.get(url, referer=referer)
    if r.status_code != 200 or getattr(r, "block_reason", None):
        return None
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(r.text)
    return r.text


TAG_RE = re.compile(r"<[^>]+>")


def text_of(fragment: str) -> str:
    return htmllib.unescape(TAG_RE.sub(" ", fragment or "")).replace("\xa0", " ").strip()


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def rel_date(text: str) -> str | None:
    """'2 Days Ago' / 'Today' / '3 weeks ago' -> ISO date."""
    t = (text or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in t or "just posted" in t or "hour" in t or "minute" in t:
        return now.date().isoformat()
    if "yesterday" in t:
        return (now - timedelta(days=1)).date().isoformat()
    m = re.search(r"(\d+)\+?\s*(day|week|month|year)", t)
    if not m:
        return None
    n = int(m.group(1))
    mult = {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
    return (now - timedelta(days=n * mult)).date().isoformat()


def maybe_fit(title: str) -> bool:
    """Worth fetching a detail page for: a title-level fit, or a generic dev title."""
    t = (title or "").lower()
    if any(r in t for r in common.REJECT):
        return False
    if common.is_fit(title):
        return True
    return any(g in t for g in common.GENERIC_TITLE)


def jsonld_jobposting(page: str) -> dict | None:
    for m in re.finditer(r'<script[^>]+type="application/ld(?:\+|&#x2B;)json"[^>]*>(.*?)</script>',
                         page, re.S | re.I):
        raw = htmllib.unescape(m.group(1)).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "JobPosting":
                    return node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return None


def jsonld_location(node: dict) -> str:
    loc = node.get("jobLocation")
    parts: list[str] = []
    for item in (loc if isinstance(loc, list) else [loc]):
        if not isinstance(item, dict):
            continue
        addr = item.get("address") or {}
        if isinstance(addr, dict):
            bit = ", ".join(str(addr.get(k)) for k in
                            ("addressLocality", "addressRegion", "addressCountry")
                            if addr.get(k) and isinstance(addr.get(k), str))
            if bit:
                parts.append(bit)
    if node.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return " / ".join(dict.fromkeys(parts))


def jsonld_emp_type(node: dict) -> str | None:
    """schema.org employmentType is FULL_TIME / PART_TIME / CONTRACTOR / INTERN
    (sometimes a list) — normalise before handing it to common.emp_type."""
    v = node.get("employmentType")
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    if not v:
        return None
    return common.emp_type(str(v).replace("_", "-").lower())


def jsonld_salary(node: dict) -> str | None:
    bs = node.get("baseSalary")
    if not isinstance(bs, dict):
        return None
    v = bs.get("value")
    if not isinstance(v, dict):
        return None
    lo, hi, unit = v.get("minValue"), v.get("maxValue"), v.get("unitText") or ""
    cur = bs.get("currency") or "USD"
    if lo and hi:
        return f"{cur} {lo}-{hi} {unit}".strip()
    if v.get("value"):
        return f"{cur} {v['value']} {unit}".strip()
    return None


# --------------------------------------------------------------------------- #
# 1. LinkedIn guest jobs
# --------------------------------------------------------------------------- #
LI_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def li_parse_cards(page: str, remote: bool | None) -> list[dict]:
    out = []
    for chunk in page.split("<li>"):
        m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', chunk)
        if not m:
            continue
        jid = m.group(1)
        t = re.search(r'class="base-search-card__title"[^>]*>(.*?)</h3>', chunk, re.S)
        c = re.search(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', chunk, re.S)
        l = re.search(r'class="job-search-card__location"[^>]*>(.*?)</span>', chunk, re.S)
        d = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', chunk)
        out.append({
            "id": jid,
            "title": squash(text_of(t.group(1))) if t else "",
            "company": squash(text_of(c.group(1))) if c else "",
            "location": squash(text_of(l.group(1))) if l else "",
            "posted_at": d.group(1) if d else None,
            "remote": remote,
        })
    return out


def li_parse_detail(page: str) -> dict:
    info: dict = {"description": "", "criteria": {}, "apply_url": None, "salary": None}
    i = page.find("show-more-less-html__markup")
    if i > -1:
        start = page.find(">", i)
        end = page.find("show-more-less-html__button", start)
        if end == -1:
            end = page.find("</section>", start)
        body = page[start + 1:end if end > start else start + 20000]
        body = re.sub(r"<button\b.*$", "", body, flags=re.S)
        info["description"] = body
    for m in re.finditer(
            r'description__job-criteria-subheader"[^>]*>(.*?)</h3>\s*'
            r'<span class="description__job-criteria-text[^"]*"[^>]*>(.*?)</span>', page, re.S):
        info["criteria"][squash(text_of(m.group(1))).lower()] = squash(text_of(m.group(2)))
    m = re.search(r'<code id="applyUrl"[^>]*>\s*(?:<!--)?\s*"?(https?://[^"\s<]+)', page)
    if m:
        info["apply_url"] = htmllib.unescape(m.group(1)).rstrip('"-')
    m = re.search(r'compensation__salary[^>]*>(.*?)</div>', page, re.S)
    if m:
        info["salary"] = squash(text_of(m.group(1))) or None
    return info


def scrape_linkedin(f: Fetcher, limit: int = 0) -> list[dict]:
    st = STATUS.setdefault("linkedin", {"search_pages": 0, "cards": 0, "unique": 0,
                                        "details": 0, "rows": 0, "codes": {}, "notes": []})
    runs = [
        ("United States", {"f_WT": "2"}, True, "remote"),
        ("San Francisco Bay Area", {}, None, "bayarea"),
    ]
    # JOB_RADAR_DAYS=1 (or 3, 7) narrows the search window for a daily incremental run; default 30 days.
    days = int(os.environ.get("JOB_RADAR_DAYS", "30") or 30)
    tpr = f"r{days * 86400}"
    cards: dict[str, dict] = {}
    for loc, extra, remote, tag in runs:
        for q in QUERIES:
            # guest API returns 10 cards per response and stops at start=300 (probed 2026-09-03: 290 is the last page)
            for start in range(0, 300, 10):
                params = {"keywords": q, "location": loc, "f_TPR": tpr, "start": str(start)}
                params.update(extra)
                url = LI_SEARCH + "?" + urllib.parse.urlencode(params)
                try:
                    r = f.get(url, referer="https://www.linkedin.com/jobs/search")
                except HostBlocked as e:
                    st["notes"].append(str(e))
                    log(f"   linkedin STOP: {e}")
                    return li_finish(f, cards, st, limit)
                st["codes"][str(r.status_code)] = st["codes"].get(str(r.status_code), 0) + 1
                why = getattr(r, "block_reason", None)
                if why:
                    st["notes"].append(f"{tag}/{q}@{start}: {why}")
                    log(f"   linkedin blocked {tag}/{q}@{start}: {why}")
                    break
                st["search_pages"] += 1
                page_cards = li_parse_cards(r.text, remote)
                st["cards"] += len(page_cards)
                if not page_cards:
                    break
                for c in page_cards:
                    if c["id"] not in cards:
                        c["query"] = q
                        c["run"] = tag
                        cards[c["id"]] = c
                    elif remote and cards[c["id"]].get("remote") is not True:
                        cards[c["id"]]["remote"] = True
                if len(page_cards) < 10:  # last page for this query
                    break
            log(f"   linkedin {tag} · {q}: running unique={len(cards)}")
    return li_finish(f, cards, st, limit)


def li_finish(f: Fetcher, cards: dict[str, dict], st: dict, limit: int) -> list[dict]:
    st["unique"] = len(cards)
    rows: list[dict] = []
    todo = [c for c in cards.values()
            if common.recent(c["posted_at"]) and maybe_fit(c["title"])]
    st["candidates"] = len(todo)
    if limit:
        todo = todo[:limit]
    for n, c in enumerate(todo, 1):
        url = f"https://www.linkedin.com/jobs/view/{c['id']}/"
        try:
            page = cached_get(f, "linkedin", c["id"], LI_DETAIL.format(c["id"]),
                              referer="https://www.linkedin.com/jobs/search")
        except HostBlocked as e:
            st["notes"].append(f"detail stage: {e}")
            log(f"   linkedin detail STOP: {e}")
            break
        if not page:
            continue
        st["details"] += 1
        info = li_parse_detail(page)
        desc = common.clean(info["description"])
        if not common.is_fit(c["title"], desc):
            continue
        crit = info["criteria"]
        rows.append(common.record(
            "linkedin", c["title"], c["company"], url,
            apply_url=info["apply_url"], location=c["location"], remote=c["remote"],
            employment_type=common.emp_type(crit.get("employment type", "")) if crit.get("employment type") else None,
            salary=info["salary"], posted_at=c["posted_at"], description=desc,
            tags=[v for k, v in crit.items() if k in ("job function", "industries")],
            open_status="unverified", native_id=c["id"],
            notes=f"linkedin guest search; query={c.get('query')}; run={c.get('run')}"
                  + (f"; seniority(li)={crit['seniority level']}" if crit.get("seniority level") else ""),
        ))
        if n % 25 == 0:
            log(f"   linkedin details {n}/{len(todo)} kept={len(rows)}")
    st["rows"] = len(rows)
    return rows


# --------------------------------------------------------------------------- #
# 2. SimplyHired
# --------------------------------------------------------------------------- #
def scrape_simplyhired(f: Fetcher, limit: int = 0) -> list[dict]:
    st = STATUS.setdefault("simplyhired", {"search_pages": 0, "cards": 0, "unique": 0,
                                           "details": 0, "rows": 0, "codes": {}, "notes": []})
    locations = ["Remote", "Napa, CA", "San Francisco, CA"]
    seen: dict[str, dict] = {}
    for loc in locations:
        for q in QUERIES[:8]:
            url = ("https://www.simplyhired.com/search?"
                   + urllib.parse.urlencode({"q": q, "l": loc, "t": "30"}))
            try:
                r = f.get(url, referer="https://www.simplyhired.com/")
            except HostBlocked as e:
                st["notes"].append(str(e))
                log(f"   simplyhired STOP: {e}")
                return sh_finish(f, seen, st, limit)
            st["codes"][str(r.status_code)] = st["codes"].get(str(r.status_code), 0) + 1
            why = getattr(r, "block_reason", None)
            if why:
                st["notes"].append(f"{loc}/{q}: {why}")
                log(f"   simplyhired blocked {loc}/{q}: {why}")
                continue
            st["search_pages"] += 1
            keys = re.findall(r'data-jobkey="([^"]+)"', r.text)
            if not keys:
                keys = re.findall(r'href="/job/([A-Za-z0-9_\-]{10,})"', r.text)
            st["cards"] += len(keys)
            for k in keys:
                seen.setdefault(k, {"key": k, "query": q, "location": loc})
    return sh_finish(f, seen, st, limit)


def sh_finish(f: Fetcher, seen: dict[str, dict], st: dict, limit: int) -> list[dict]:
    st["unique"] = len(seen)
    rows: list[dict] = []
    todo = list(seen.values())[:limit] if limit else list(seen.values())
    for c in todo:
        url = f"https://www.simplyhired.com/job/{c['key']}"
        try:
            page = cached_get(f, "simplyhired", c["key"], url, referer="https://www.simplyhired.com/")
        except HostBlocked as e:
            st["notes"].append(f"detail stage: {e}")
            break
        if not page:
            continue
        st["details"] += 1
        node = jsonld_jobposting(page)
        if not node:
            continue
        title = squash(node.get("title") or "")
        org = node.get("hiringOrganization") or {}
        company = squash(org.get("name") if isinstance(org, dict) else "") or ""
        desc = common.clean(node.get("description") or "")
        if not common.is_fit(title, desc):
            continue
        posted = (node.get("datePosted") or "")[:10] or None
        if not common.recent(posted):
            continue
        loc = jsonld_location(node) or c["location"]
        rows.append(common.record(
            "simplyhired", title, company, url, location=loc,
            remote=("remote" in loc.lower() or node.get("jobLocationType") == "TELECOMMUTE"),
            employment_type=jsonld_emp_type(node),
            salary=jsonld_salary(node), posted_at=posted, description=desc,
            open_status="unverified", native_id=c["key"],
            notes=f"simplyhired search; query={c['query']}; loc={c['location']}",
        ))
    st["rows"] = len(rows)
    return rows


# --------------------------------------------------------------------------- #
# 3. Built In
# --------------------------------------------------------------------------- #
BUILTIN_SITES = [
    ("builtin.com", "https://builtin.com/jobs/remote/dev-engineering/search", True),
    ("www.builtinsf.com", "https://www.builtinsf.com/jobs/dev-engineering/search", None),
]


def bi_parse_cards(page: str, base: str, remote: bool | None) -> list[dict]:
    out = []
    for chunk in page.split('data-id="job-card"')[1:]:
        m = re.search(r'href="(/job/[^"]+/(\d+))"[^>]*data-id="job-card-title"[^>]*>(.*?)</a>', chunk, re.S)
        if not m:
            m2 = re.search(r'data-id="job-card-title"[^>]*data-alias="(/job/[^"]+/(\d+))"', chunk)
            if not m2:
                continue
            href, jid = m2.group(1), m2.group(2)
            t = re.search(r'data-id="job-card-title"[^>]*>(.*?)</a>', chunk, re.S)
            title = squash(text_of(t.group(1))) if t else ""
        else:
            href, jid, title = m.group(1), m.group(2), squash(text_of(m.group(3)))
        c = re.search(r'data-id="company-title"[^>]*>(.*?)</a>', chunk, re.S)
        company = squash(text_of(c.group(1))) if c else ""
        age = re.search(r'fa-clock[^>]*></i>\s*([^<]{1,40})<', chunk)
        posted = rel_date(age.group(1)) if age else None
        out.append({"id": jid, "url": base + href, "title": title, "company": company,
                    "posted_at": posted, "remote": remote})
    return out


def scrape_builtin(f: Fetcher, limit: int = 0) -> list[dict]:
    st = STATUS.setdefault("builtin", {"search_pages": 0, "cards": 0, "unique": 0,
                                       "details": 0, "rows": 0, "codes": {}, "notes": []})
    cards: dict[str, dict] = {}
    for host, search_url, remote in BUILTIN_SITES:
        base = "https://" + host
        for q in QUERIES[:8]:
            for page_n in range(1, 4):
                params = {"search": q, "daysSinceUpdated": "30"}
                if page_n > 1:
                    params["page"] = str(page_n)
                url = search_url + "?" + urllib.parse.urlencode(params)
                try:
                    r = f.get(url, referer=base + "/jobs")
                except HostBlocked as e:
                    st["notes"].append(str(e))
                    log(f"   builtin STOP: {e}")
                    return bi_finish(f, cards, st, limit)
                st["codes"][str(r.status_code)] = st["codes"].get(str(r.status_code), 0) + 1
                why = getattr(r, "block_reason", None)
                if why:
                    st["notes"].append(f"{host}/{q} p{page_n}: {why}")
                    break
                st["search_pages"] += 1
                pc = bi_parse_cards(r.text, base, remote)
                st["cards"] += len(pc)
                if not pc:
                    break
                for c in pc:
                    c["query"] = q
                    c["site"] = host
                    cards.setdefault(c["id"], c)
            log(f"   builtin {host} · {q}: running unique={len(cards)}")
    return bi_finish(f, cards, st, limit)


def bi_finish(f: Fetcher, cards: dict[str, dict], st: dict, limit: int) -> list[dict]:
    st["unique"] = len(cards)
    rows: list[dict] = []
    todo = [c for c in cards.values() if common.recent(c["posted_at"]) and maybe_fit(c["title"])]
    st["candidates"] = len(todo)
    if limit:
        todo = todo[:limit]
    for n, c in enumerate(todo, 1):
        try:
            page = cached_get(f, "builtin", c["id"], c["url"], referer="https://builtin.com/jobs")
        except HostBlocked as e:
            st["notes"].append(f"detail stage: {e}")
            break
        if not page:
            continue
        st["details"] += 1
        node = jsonld_jobposting(page) or {}
        desc = common.clean(node.get("description") or "")
        title = squash(node.get("title") or "") or c["title"]
        org = node.get("hiringOrganization") or {}
        company = squash(org.get("name") if isinstance(org, dict) else "") or c["company"]
        if not common.is_fit(title, desc):
            continue
        posted = (node.get("datePosted") or "")[:10] or c["posted_at"]
        if not common.recent(posted):
            continue
        loc = jsonld_location(node)
        if not loc:
            m = re.search(r'fa-location-dot[^>]*></i>\s*</div>\s*<div[^>]*>\s*<span[^>]*>(.*?)</span>', page, re.S)
            loc = squash(text_of(m.group(1))) if m else ""
        remote = c["remote"]
        if node.get("jobLocationType") == "TELECOMMUTE" or "remote" in loc.lower():
            remote = True
        rows.append(common.record(
            "builtin", title, company, c["url"], location=loc, remote=remote,
            employment_type=jsonld_emp_type(node),
            salary=jsonld_salary(node), posted_at=posted, description=desc,
            open_status="unverified", native_id=c["id"],
            notes=f"builtin {c['site']}; query={c['query']}",
        ))
        if n % 25 == 0:
            log(f"   builtin details {n}/{len(todo)} kept={len(rows)}")
    st["rows"] = len(rows)
    return rows


# --------------------------------------------------------------------------- #
# 4. Dice
# --------------------------------------------------------------------------- #
def dice_parse_cards(page: str) -> list[dict]:
    out = []
    for chunk in page.split('data-testid="job-card"')[1:]:
        g = re.search(r'href="/job-detail/([0-9a-f\-]{20,})"', chunk)
        if not g:
            continue
        t = re.search(r'data-testid="job-search-job-detail-link"[^>]*>(.*?)</a>', chunk, re.S)
        c = re.search(r'data-testid="job-card-company-name"[^>]*>(.*?)</p>', chunk, re.S)
        meta = re.search(r'text-foreground-light">([^<]*?)<!-- --> • <!-- -->([^<]*?)</p>', chunk)
        out.append({
            "id": g.group(1),
            "title": squash(text_of(t.group(1))) if t else "",
            "company": squash(text_of(c.group(1))) if c else "",
            "location": squash(meta.group(1)) if meta else "",
            "posted_at": rel_date(meta.group(2)) if meta else None,
        })
    return out


def scrape_dice(f: Fetcher, limit: int = 0) -> list[dict]:
    st = STATUS.setdefault("dice", {"search_pages": 0, "cards": 0, "unique": 0,
                                    "details": 0, "rows": 0, "codes": {}, "notes": []})
    st["notes"].append("no x-api-key found in 36 dice JS bundles; search results are "
                       "server-rendered HTML, so parsed the page directly instead")
    cards: dict[str, dict] = {}
    runs = [("Remote", True), ("San Francisco, CA", None)]
    for loc, remote in runs:
        for q in QUERIES[:8]:
            for page_n in (1, 2):
                params = {"q": q, "location": loc, "filters.postedDate": "THIRTY"}
                if page_n > 1:
                    params["page"] = str(page_n)
                url = "https://www.dice.com/jobs?" + urllib.parse.urlencode(params)
                try:
                    r = f.get(url, referer="https://www.dice.com/")
                except HostBlocked as e:
                    st["notes"].append(str(e))
                    log(f"   dice STOP: {e}")
                    return dice_finish(f, cards, st, limit)
                st["codes"][str(r.status_code)] = st["codes"].get(str(r.status_code), 0) + 1
                why = getattr(r, "block_reason", None)
                if why:
                    st["notes"].append(f"{loc}/{q} p{page_n}: {why}")
                    break
                st["search_pages"] += 1
                pc = dice_parse_cards(r.text)
                st["cards"] += len(pc)
                if not pc:
                    break
                for c in pc:
                    c["query"] = q
                    c["remote"] = remote
                    cards.setdefault(c["id"], c)
            log(f"   dice {loc} · {q}: running unique={len(cards)}")
    return dice_finish(f, cards, st, limit)


def dice_finish(f: Fetcher, cards: dict[str, dict], st: dict, limit: int) -> list[dict]:
    st["unique"] = len(cards)
    rows: list[dict] = []
    todo = [c for c in cards.values() if common.recent(c["posted_at"]) and maybe_fit(c["title"])]
    st["candidates"] = len(todo)
    if limit:
        todo = todo[:limit]
    for n, c in enumerate(todo, 1):
        url = f"https://www.dice.com/job-detail/{c['id']}"
        try:
            page = cached_get(f, "dice", c["id"], url, referer="https://www.dice.com/jobs")
        except HostBlocked as e:
            st["notes"].append(f"detail stage: {e}")
            break
        if not page:
            continue
        st["details"] += 1
        node = jsonld_jobposting(page) or {}
        desc = common.clean(node.get("description") or "")
        title = squash(node.get("title") or "") or c["title"]
        org = node.get("hiringOrganization") or {}
        company = squash(org.get("name") if isinstance(org, dict) else "") or c["company"]
        if not common.is_fit(title, desc):
            continue
        posted = (node.get("datePosted") or "")[:10] or c["posted_at"]
        if not common.recent(posted):
            continue
        loc = jsonld_location(node) or c["location"]
        remote = True if (node.get("jobLocationType") == "TELECOMMUTE"
                          or "remote" in (loc + " " + title).lower()) else None
        rows.append(common.record(
            "dice", title, company, url, location=loc, remote=remote,
            employment_type=jsonld_emp_type(node),
            salary=jsonld_salary(node), posted_at=posted, description=desc,
            open_status="unverified", native_id=c["id"],
            notes=f"dice search; query={c['query']}; search location filter="
                  + ("Remote" if c["remote"] else "San Francisco, CA"),
        ))
        if n % 25 == 0:
            log(f"   dice details {n}/{len(todo)} kept={len(rows)}")
    st["rows"] = len(rows)
    return rows


# --------------------------------------------------------------------------- #
# 5-8. One-attempt boards
# --------------------------------------------------------------------------- #
def try_urls(f: Fetcher, source: str, urls: list[str]) -> list[dict]:
    """One serious attempt per URL. Records outcome; parses JSON-LD/RSS if we get through."""
    st = STATUS.setdefault(source, {"search_pages": 0, "cards": 0, "unique": 0,
                                    "details": 0, "rows": 0, "codes": {}, "notes": []})
    rows: list[dict] = []
    for url in urls:
        try:
            r = f.get(url, tries=2)
        except HostBlocked as e:
            st["notes"].append(str(e))
            break
        st["codes"][str(r.status_code)] = st["codes"].get(str(r.status_code), 0) + 1
        why = getattr(r, "block_reason", None)
        if why:
            st["notes"].append(f"{url[:70]}: {why}")
            log(f"   {source}: {why} <- {url[:70]}")
            continue
        st["search_pages"] += 1
        body = r.text
        # RSS?
        items = re.findall(r"<item>(.*?)</item>", body, re.S)
        if items:
            st["cards"] += len(items)
            for it in items:
                t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
                lk = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", it, re.S)
                de = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", it, re.S)
                pd = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
                title = squash(text_of(t.group(1))) if t else ""
                link = squash(lk.group(1)) if lk else ""
                desc = common.clean(de.group(1) if de else "")
                if not link or not common.is_fit(title, desc):
                    continue
                posted = None
                if pd:
                    try:
                        posted = datetime.strptime(pd.group(1).strip()[:16], "%a, %d %b %Y").date().isoformat()
                    except Exception:
                        posted = None
                if not common.recent(posted):
                    continue
                rows.append(common.record(source, title, "", link, description=desc,
                                          posted_at=posted, open_status="unverified",
                                          notes=f"{source} RSS feed"))
            continue
        node = jsonld_jobposting(body)
        if node:
            st["cards"] += 1
            title = squash(node.get("title") or "")
            org = node.get("hiringOrganization") or {}
            desc = common.clean(node.get("description") or "")
            if common.is_fit(title, desc):
                rows.append(common.record(
                    source, title, squash(org.get("name") if isinstance(org, dict) else "") or "",
                    node.get("url") or url, location=jsonld_location(node),
                    posted_at=(node.get("datePosted") or "")[:10] or None,
                    description=desc, open_status="unverified", notes=f"{source} JSON-LD"))
        else:
            st["notes"].append(f"{url[:70]}: HTTP 200 but no parseable job data "
                               f"(JS-rendered, {len(body)} bytes)")
            log(f"   {source}: 200 but no job data in {len(body)} bytes <- {url[:70]}")
    st["rows"] = len(rows)
    return rows


def scrape_indeed(f: Fetcher) -> list[dict]:
    return try_urls(f, "indeed", [
        "https://www.indeed.com/jobs?q=AI+engineer&l=Remote&fromage=30",
        "https://www.indeed.com/rss?q=AI+engineer&l=Remote&fromage=30",
    ])


def scrape_glassdoor(f: Fetcher) -> list[dict]:
    return try_urls(f, "glassdoor", [
        "https://www.glassdoor.com/Job/remote-ai-engineer-jobs-SRCH_IL.0,6_IS11047_KO7,18.htm",
    ])


def scrape_google_jobs(f: Fetcher) -> list[dict]:
    return try_urls(f, "google_jobs", [
        "https://www.google.com/search?q=AI+engineer+remote+jobs&ibp=htl;jobs",
    ])


# --------------------------------------------------------------------------- #
BOARDS = [
    ("linkedin", scrape_linkedin, True),
    ("builtin", scrape_builtin, True),
    ("dice", scrape_dice, True),
    ("simplyhired", scrape_simplyhired, True),
    # ZipRecruiter moved to sources/ziprecruiter.py (its search path is Cloudflare-challenged; the
    # directory pages + text/markdown job records are not).
    ("indeed", scrape_indeed, False),
    ("glassdoor", scrape_glassdoor, False),
    ("google_jobs", scrape_google_jobs, False),
]


def main() -> None:
    args = common.cli("job-radar HTML job boards scraper")
    only = os.environ.get("HTML_BOARDS_ONLY", "").strip()
    only_names = [x.strip() for x in only.split(",") if x.strip()]
    f = Fetcher()
    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    if only_names and os.path.exists(args.out):
        # a partial rerun (e.g. HTML_BOARDS_ONLY=linkedin) must not erase the other boards' rows
        with open(args.out, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("source") not in only_names and r["id"] not in seen_ids:
                        seen_ids.add(r["id"])
                        all_rows.append(r)
        log(f"== carrying {len(all_rows)} rows of boards not in HTML_BOARDS_ONLY ==")
    for name, fn, takes_limit in BOARDS:
        if only_names and name not in only_names:
            continue
        log(f"== {name} ==")
        t0 = time.time()
        try:
            rows = fn(f, args.limit) if takes_limit else fn(f)
        except Exception as e:  # one board must never kill the run
            STATUS.setdefault(name, {"notes": []}).setdefault("notes", []).append(
                f"EXCEPTION {type(e).__name__}: {e}")
            log(f"   {name} EXCEPTION {type(e).__name__}: {e}")
            rows = []
        fresh = [r for r in rows if r["id"] not in seen_ids]
        seen_ids.update(r["id"] for r in fresh)
        all_rows.extend(fresh)
        st = STATUS.setdefault(name, {})
        st["rows"] = len(fresh)
        st["seconds"] = round(time.time() - t0, 1)
        log(f"== {name}: {len(fresh)} rows in {st['seconds']}s ==")
        common.write_jsonl(args.out, all_rows)

    common.write_jsonl(args.out, all_rows)
    status_path = os.path.join(os.path.dirname(args.out), "html_boards_status.json")
    with open(status_path, "w", encoding="utf-8") as fh:
        json.dump({"finished_at": common.now(), "total_rows": len(all_rows),
                   "requests_by_host": f.counts, "boards": STATUS}, fh, indent=2)
    log(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "notes"}
                    for k, v in STATUS.items()}, indent=2))
    log(f"TOTAL {len(all_rows)} rows -> {args.out}")
    log(f"status -> {status_path}")


if __name__ == "__main__":
    main()
