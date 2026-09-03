#!/usr/bin/env python3
"""job-radar source: ZipRecruiter, keyless and headless.

Why this exists (2026-09-03): ZipRecruiter's search endpoints (/jobs-search, /candidate/search)
sit behind a Cloudflare *managed challenge* that no plain HTTP client clears, so the first
collector logged HTTP 403 and moved on. But ZipRecruiter's own LLM manifest
(https://www.ziprecruiter.com/llms.txt) documents a sanctioned, unchallenged surface:

  * directory pages  /Jobs/Remote-<Title>, /Jobs/<Title>/--in-<State>, /Jobs/<Title>/-in-<City>,<ST>, /n/<Title>
  * job pages        /c/<Company>/Job/<Title>/-in-<City>,<ST>?jid=<16 hex>
  * `Accept: text/markdown` on both -> a clean markdown record (company, location, salary,
    employment type, posted date, Status: Active, description) at ~6-10 KB instead of ~550 KB.

Three rules learned probing it: the Cloudflare edge still challenges non-browser TLS
fingerprints, so every request goes through curl_cffi's Chrome impersonation; the
directory pages ignore `page=` / `days=` (always the same top 20), so reach comes from
multiplying title x location slugs, never from paging; and directory pages that answer
HTTP 410 (uncurated slugs) or live under /n/ serve no markdown at all but still carry 20
listings in an ItemList JSON-LD block, so the listing stage reads HTML and only the job
pages use `Accept: text/markdown`.

    set PYTHONIOENCODING=utf-8
    python sources/ziprecruiter.py --out C:/Users/endle/MyStuff/_job-radar-data/raw/ziprecruiter.jsonl [--limit N]

Detail pages are cached at <data>/raw/html-cache/ziprecruiter/<jid>.md so reruns skip refetches.
"""
from __future__ import annotations

import html as htmllib
import json
import os
import random
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover
    cffi_requests = None

DATA_ROOT = os.environ.get("JOB_RADAR_DATA", r"C:/Users/endle/MyStuff/_job-radar-data")
CACHE_DIR = os.path.join(DATA_ROOT, "raw", "html-cache", "ziprecruiter")
BASE = "https://www.ziprecruiter.com"

TITLES = [
    "Ai-Engineer", "Ai-Developer", "Ai-Automation-Engineer", "Agentic-Engineer", "Automation-Engineer",
    "Llm-Engineer", "Applied-Ai-Engineer", "Forward-Deployed-Engineer", "Ai-Solutions-Engineer",
    "Prompt-Engineer", "Ai-Integration-Engineer", "Ai-Operations", "Generative-Ai-Engineer",
    "Machine-Learning-Engineer", "Ai-Specialist", "Ai-Software-Engineer",
]
# (path template, remote flag for rows found there, tag)
VARIANTS = [
    ("/Jobs/Remote-{t}", True, "remote"),
    ("/Jobs/{t}/--in-California", None, "california"),
    ("/Jobs/{t}/-in-San-Francisco,CA", None, "sf"),
    ("/Jobs/{t}/-in-Oakland,CA", None, "oakland"),
    ("/Jobs/{t}/-in-San-Jose,CA", None, "sanjose"),
    ("/Jobs/{t}/-in-Napa,CA", None, "napa"),
    ("/n/{t}", None, "nearme"),
]

STATUS: dict = {"listing_pages": 0, "listing_hits": 0, "cards": 0, "unique": 0, "candidates": 0,
                "details": 0, "cached": 0, "rows": 0, "codes": {}, "notes": []}

META_RE = re.compile(r"\*\*([A-Za-z ]+):\*\* (.*)")
JID_RE = re.compile(r"[?&]jid=([0-9a-f]{16})")
ANCHOR_RE = re.compile(r'<a[^>]+href="(/c/[^"]+\?jid=[0-9a-f]{16})"[^>]*>(.*?)</a>', re.S)
LDJSON_RE = re.compile(r'type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class HostBlocked(Exception):
    pass


class Fetcher:
    """curl_cffi Chrome impersonation, 1.5-2.5 s gaps, 30 s+ backoff on a Cloudflare challenge,
    stops after 3 consecutive challenges."""

    BLOCK_LIMIT = 3

    def __init__(self) -> None:
        if cffi_requests is None:
            raise SystemExit("curl_cffi is required (pip install curl_cffi): ZipRecruiter challenges plain TLS")
        self.s = cffi_requests.Session(impersonate="chrome")
        self.last = 0.0
        self.blocks = 0
        self.count = 0

    def _wait(self) -> None:
        gap = random.uniform(1.5, 2.5)
        delta = time.time() - self.last
        if delta < gap:
            time.sleep(gap - delta)
        self.last = time.time()

    def get(self, path_or_url: str, accept: str = "text/html") -> tuple[int, str]:
        url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
        backoff = 30.0
        for attempt in range(3):
            if self.blocks >= self.BLOCK_LIMIT:
                raise HostBlocked(f"{self.BLOCK_LIMIT} consecutive Cloudflare challenges, stopped")
            self._wait()
            try:
                r = self.s.get(url, timeout=40, headers={"Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
            except Exception as e:  # network hiccup
                log(f"   ziprecruiter network error {type(e).__name__}: {e}; retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            self.count += 1
            STATUS["codes"][str(r.status_code)] = STATUS["codes"].get(str(r.status_code), 0) + 1
            challenged = r.headers.get("cf-mitigated") == "challenge" or (
                r.status_code == 403 and "just a moment" in r.text[:2000].lower())
            if challenged:
                self.blocks += 1
                log(f"   ziprecruiter Cloudflare challenge on {url[:80]} -> backing off {backoff:.0f}s "
                    f"(strike {self.blocks}/{self.BLOCK_LIMIT})")
                time.sleep(backoff)
                backoff *= 2
                continue
            self.blocks = 0
            return r.status_code, r.text
        raise HostBlocked("gave up after 3 attempts")


def parse_listing(page: str) -> list[dict]:
    """Cards from a directory page's HTML: the ItemList JSON-LD (name + url) first, anchors as a fallback."""
    out: dict[str, dict] = {}
    for raw in LDJSON_RE.findall(page):
        try:
            data = json.loads(htmllib.unescape(raw))
        except Exception:
            continue
        if not (isinstance(data, dict) and data.get("@type") == "ItemList"):
            continue
        for it in data.get("itemListElement") or []:
            if not isinstance(it, dict):
                continue
            url = htmllib.unescape(str(it.get("url") or ""))
            m = JID_RE.search(url)
            if m and m.group(1) not in out:
                out[m.group(1)] = {"jid": m.group(1), "title": htmllib.unescape(str(it.get("name") or "")).strip(),
                                   "url": url, "company": "", "location": "", "salary": None}
    for href, inner in ANCHOR_RE.findall(page):
        m = JID_RE.search(href)
        if not m or m.group(1) in out:
            continue
        title = re.sub(r"<[^>]+>", " ", inner)
        title = re.sub(r"\s+", " ", htmllib.unescape(title)).strip()
        if title:
            out[m.group(1)] = {"jid": m.group(1), "title": title, "url": BASE + htmllib.unescape(href),
                               "company": "", "location": "", "salary": None}
    return list(out.values())


def parse_detail(md: str) -> dict:
    head, _, body = md.partition("## Description")
    meta = {k.strip().lower(): v.strip() for k, v in META_RE.findall(head)}
    title = ""
    m = re.match(r"# (.+)", head.strip())
    if m:
        title = m.group(1).strip()
    posted = None
    if meta.get("posted"):
        try:
            posted = datetime.strptime(meta["posted"], "%B %d, %Y").date().isoformat()
        except ValueError:
            posted = None
    desc = re.sub(r"^### Job Description\s*", "", body.strip())
    return {"title": title, "meta": meta, "posted_at": posted, "description": desc,
            "active": meta.get("status", "").lower() == "active" if meta.get("status") else None}


def maybe_fit(title: str) -> bool:
    t = (title or "").lower()
    if any(r in t for r in common.REJECT):
        return False
    if common.is_fit(title):
        return True
    return any(g in t for g in common.GENERIC_TITLE)


def hourly_to_annual(salary: str | None) -> tuple[int | None, int | None]:
    """'$80 - $120 per hour' -> (166400, 249600); common.parse_salary only understands annual figures."""
    if not salary or "hour" not in salary.lower():
        return None, None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\$\s?(\d{1,3}(?:,\d{3})*)", salary)]
    nums = [n for n in nums if 10 <= n <= 500]
    if not nums:
        return None, None
    return min(nums) * 2080, max(nums) * 2080


def cached_detail(f: Fetcher, jid: str, url: str) -> str | None:
    p = os.path.join(CACHE_DIR, f"{jid}.md")
    if os.path.exists(p) and os.path.getsize(p) > 300:
        STATUS["cached"] += 1
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    code, text = f.get(url, accept="text/markdown")
    if code not in (200, 410) or "**Company:**" not in text[:3000]:
        STATUS["notes"].append(f"detail {jid}: HTTP {code}, {len(text)} bytes, no job record")
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def collect_cards(f: Fetcher) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for t in TITLES:
        for tpl, remote, tag in VARIANTS:
            path = tpl.format(t=t)
            try:
                code, page = f.get(path)
            except HostBlocked as e:
                STATUS["notes"].append(f"listing stage stopped at {path}: {e}")
                log(f"   ziprecruiter STOP: {e}")
                return cards
            STATUS["listing_pages"] += 1
            found = parse_listing(page) if code in (200, 410) else []
            if found:
                STATUS["listing_hits"] += 1
            STATUS["cards"] += len(found)
            for c in found:
                if c["jid"] not in cards:
                    c["remote"] = remote
                    c["query"] = t
                    c["run"] = tag
                    cards[c["jid"]] = c
                elif remote and cards[c["jid"]].get("remote") is not True:
                    cards[c["jid"]]["remote"] = True
        log(f"   ziprecruiter {t}: running unique={len(cards)}")
    return cards


def build_rows(f: Fetcher, cards: dict[str, dict], limit: int) -> list[dict]:
    todo = [c for c in cards.values() if maybe_fit(c["title"])]
    STATUS["candidates"] = len(todo)
    if limit:
        todo = todo[:limit]
    rows: list[dict] = []
    for n, c in enumerate(todo, 1):
        try:
            md = cached_detail(f, c["jid"], c["url"])
        except HostBlocked as e:
            STATUS["notes"].append(f"detail stage stopped at {n}/{len(todo)}: {e}")
            log(f"   ziprecruiter detail STOP: {e}")
            break
        if not md:
            continue
        STATUS["details"] += 1
        d = parse_detail(md)
        if d["active"] is False:
            continue
        title = d["title"] or c["title"]
        desc = common.clean(d["description"])
        if not common.is_fit(title, desc):
            continue
        if not common.recent(d["posted_at"]):
            continue
        meta = d["meta"]
        location = meta.get("location") or c["location"]
        url_l = c["url"].lower()
        remote = c["remote"]
        if "remote" in (title + " " + location).lower() or "/-in-remote," in url_l:
            remote = True
        salary = meta.get("salary") or c.get("salary")
        row = common.record(
            "ziprecruiter", title, meta.get("company") or c["company"], c["url"],
            location=location, remote=remote,
            employment_type=common.emp_type(meta["employment type"]) if meta.get("employment type") else None,
            salary=salary, posted_at=d["posted_at"], description=desc,
            open_status="open" if d["active"] else "unverified", native_id=c["jid"],
            notes=f"ziprecruiter directory page; slug={c['query']}; run={c['run']}"
                  + ("; status=Active at collection" if d["active"] else ""),
        )
        if row["salary_min"] is None:
            row["salary_min"], row["salary_max"] = hourly_to_annual(salary)
        rows.append(row)
        if n % 25 == 0:
            log(f"   ziprecruiter details {n}/{len(todo)} kept={len(rows)}")
    return rows


def previous_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def main() -> None:
    args = common.cli("job-radar ZipRecruiter scraper (directory pages + markdown job records)")
    t0 = time.time()
    f = Fetcher()
    prev = previous_rows(args.out)
    cards = collect_cards(f)
    STATUS["unique"] = len(cards)
    rows = build_rows(f, cards, args.limit)
    aborted = any("stopped" in n for n in STATUS["notes"])
    expected_pages = len(TITLES) * len(VARIANTS)
    if prev and (aborted or STATUS["listing_pages"] < expected_pages // 2):
        # a rate-limited run must not erase the previous collection (lesson from the Himalayas overwrite)
        have = {r["id"] for r in rows}
        carried = [r for r in prev if r["id"] not in have and common.recent(r.get("posted_at"))]
        STATUS["notes"].append(f"carried {len(carried)} rows forward from the previous file (run incomplete)")
        rows.extend(carried)
    STATUS["rows"] = len(rows)
    STATUS["seconds"] = round(time.time() - t0, 1)
    STATUS["requests"] = f.count
    common.write_jsonl(args.out, rows)
    status_path = os.path.join(os.path.dirname(args.out), "ziprecruiter_status.json")
    with open(status_path, "w", encoding="utf-8") as fh:
        json.dump({"finished_at": common.now(), **STATUS}, fh, indent=2)
    log(json.dumps({k: v for k, v in STATUS.items() if k != "notes"}, indent=2))
    for n in STATUS["notes"][:10]:
        log(f"   note: {n}")
    log(f"TOTAL {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
