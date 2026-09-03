"""Build LinkedIn rows from the guest detail pages already cached by html_boards.py.

    python sources/linkedin_cache.py --out <data>/raw/linkedin_partial.jsonl

Used while the full html_boards run is still fetching (its file is written only at the end),
and as a free rebuild path afterwards. Card metadata (search-result remote flag) is not in the
cache, so `remote` is inferred from the page text.
"""
from __future__ import annotations
import glob, html as htmllib, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402
from html_boards import li_parse_detail, CACHE_ROOT  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def first(pattern: str, page: str) -> str:
    m = re.search(pattern, page, re.S)
    return common.clean(htmllib.unescape(m.group(1)), 300) if m else ""


def main():
    a = common.cli("LinkedIn rows from cached guest detail pages")
    files = glob.glob(os.path.join(CACHE_ROOT, "linkedin", "*.html"))
    rows, skipped = [], 0
    for path in files:
        jid = os.path.splitext(os.path.basename(path))[0]
        page = open(path, encoding="utf-8", errors="ignore").read()
        title = first(r'class="top-card-layout__title[^"]*"[^>]*>(.*?)</h[12]>', page) \
            or first(r"<title>(.*?)</title>", page).split(" | ")[0]
        company = first(r'class="topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', page) \
            or first(r'class="topcard__flavor"[^>]*>(.*?)</span>', page)
        location = first(r'class="topcard__flavor topcard__flavor--bullet"[^>]*>(.*?)</span>', page)
        posted_rel = first(r'class="posted-time-ago__text[^"]*"[^>]*>(.*?)</span>', page)
        if not title:
            skipped += 1
            continue
        info = li_parse_detail(page)
        desc = common.clean(info["description"])
        if not common.is_fit(title, desc):
            skipped += 1
            continue
        head = f"{title} {location} {desc[:1500]}".lower()
        remote = True if re.search(r"\bremote\b|work from home|wfh", head) else None
        crit = info["criteria"]
        posted = None
        m = re.search(r"(\d+)\s+(hour|day|week|month)", posted_rel)
        if m:
            from datetime import date, timedelta
            n, unit = int(m.group(1)), m.group(2)
            days = {"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
            posted = (date(2026, 9, 2) - timedelta(days=days)).isoformat()
        rows.append(common.record(
            "linkedin", title, company, f"https://www.linkedin.com/jobs/view/{jid}/",
            apply_url=info["apply_url"], location=location, remote=remote,
            employment_type=common.emp_type(crit.get("employment type", "")) if crit.get("employment type") else None,
            salary=info["salary"], posted_at=posted, description=desc,
            tags=[v for k, v in crit.items() if k in ("job function", "industries")],
            open_status="unverified", native_id=jid,
            notes="linkedin guest detail (cache build)" + (f"; posted '{posted_rel}'" if posted_rel else "")
                  + (f"; seniority(li)={crit['seniority level']}" if crit.get("seniority level") else ""),
        ))
    common.write_jsonl(a.out, rows)
    print(f"cached pages {len(files)}, kept {len(rows)}, skipped {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
