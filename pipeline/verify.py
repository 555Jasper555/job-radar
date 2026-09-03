"""Verify each posting is still open by fetching it. Headless HTTP only.

    python pipeline/verify.py --in <data>/merged.jsonl --out <data>/verified.jsonl [--workers 6]

Sets `verify` = {"status": open|closed|unknown, "http": code, "reason": str, "checked_at": iso}.
Rows whose source API only lists live jobs (open_status == "open") are marked open without a
fetch unless --refetch-open is passed. LinkedIn is checked through the guest jobPosting endpoint
(404 = closed). Reddit/HN rows stay "unknown" (the post is a declaration; no closed signal exists).
"""
from __future__ import annotations
import argparse, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlsplit
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sources"))
import common  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

CLOSED = [
    "no longer accepting applications", "this job is no longer available", "position has been filled",
    "job has expired", "this posting is closed", "this job has closed", "no longer active",
    "job is no longer open", "this position is closed", "posting has expired", "job not found",
    "the job you are looking for is no longer", "this job has been filled", "job posting is unavailable",
    "sorry, this job is closed", "this opportunity is closed", "we are no longer accepting",
    "position is no longer available", "this role has been filled", "role is no longer open",
]
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
_host_lock: dict[str, threading.Lock] = {}
_lock = threading.Lock()


def host_lock(host: str) -> threading.Lock:
    with _lock:
        return _host_lock.setdefault(host, threading.Lock())


def check(row: dict, sess) -> dict:
    src = row.get("source", "")
    url = row.get("apply_url") if src == "hn" and row.get("apply_url") else row.get("url")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if src in ("reddit",):
        return {"status": "unknown", "http": None, "reason": "reddit post; no closed signal", "checked_at": now}
    if src in ("linkedin", "dice", "simplyhired", "builtin", "ziprecruiter") and len(row.get("description") or "") > 200:
        # the collector already fetched the detail page (404 = gone, ZipRecruiter says Status: Active) minutes ago;
        # don't hammer LinkedIn twice, and a plain-requests refetch of ZipRecruiter only meets the Cloudflare challenge
        return {"status": "open", "http": 200, "reason": "detail page fetched at collection time", "checked_at": now}
    if src == "linkedin":
        m = re.search(r"/jobs/view/(\d+)", url or "")
        if m:
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
    host = urlsplit(url).netloc
    with host_lock(host):
        try:
            r = sess.get(url, timeout=25, allow_redirects=True, headers={"User-Agent": BROWSER_UA})
            time.sleep(1.2 if "linkedin" in host else 0.4)
        except Exception as e:  # noqa: BLE001
            return {"status": "unknown", "http": None, "reason": f"fetch error: {type(e).__name__}", "checked_at": now}
    code = r.status_code
    body = r.text[:400000].lower() if r.headers.get("content-type", "").startswith(("text", "application/json")) else ""
    if code == 404 or code == 410:
        return {"status": "closed", "http": code, "reason": "404/410", "checked_at": now}
    if code in (401, 403, 429, 999) or "authwall" in r.url or "login" in r.url:
        return {"status": "unknown", "http": code, "reason": f"blocked ({code}) -> {r.url[:80]}", "checked_at": now}
    if code >= 500:
        return {"status": "unknown", "http": code, "reason": "server error", "checked_at": now}
    hit = next((p for p in CLOSED if p in body), None)
    if hit:
        return {"status": "closed", "http": code, "reason": f'page says "{hit}"', "checked_at": now}
    if src == "hn" and not row.get("apply_url"):
        return {"status": "unknown", "http": code, "reason": "HN comment only; no apply page to check", "checked_at": now}
    return {"status": "open", "http": code, "reason": "200 and no closed phrase", "checked_at": now}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--refetch-open", action="store_true")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.inp, encoding="utf-8") if l.strip()]
    prev = {}
    if os.path.exists(a.out):
        for l in open(a.out, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                if r.get("verify"):
                    prev[r["id"]] = r["verify"]
    sess = common.session()
    todo = []
    for r in rows:
        if r["id"] in prev and prev[r["id"]]["status"] != "unknown":
            r["verify"] = prev[r["id"]]
        elif r.get("open_status") == "open" and not a.refetch_open:
            r["verify"] = {"status": "open", "http": None, "reason": "source API lists live postings only",
                           "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        else:
            todo.append(r)
    print(f"verifying {len(todo)} of {len(rows)} rows", file=sys.stderr)
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(check, r, sess): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                r["verify"] = f.result()
            except Exception as e:  # noqa: BLE001
                r["verify"] = {"status": "unknown", "http": None, "reason": f"exc {type(e).__name__}", "checked_at": None}
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(todo)}", file=sys.stderr)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tally = {}
    for r in rows:
        tally[r["verify"]["status"]] = tally.get(r["verify"]["status"], 0) + 1
    print(json.dumps({"rows": len(rows), "fetched": len(todo), "verify": tally}, indent=1))


if __name__ == "__main__":
    main()
