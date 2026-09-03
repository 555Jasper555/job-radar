"""Merge every raw/*.jsonl into one deduped jobs list.

    python pipeline/merge.py --raw <data>/raw --out <data>/merged.jsonl

Dedupe keys, in order: exact URL (normalized), then (company, title) normalized. When two rows
collide, keep the one with the richer description and record every source in `sources`.
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys
from urllib.parse import urlsplit, urlunsplit
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sources"))
import common  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

AGGREGATORS = {"jobgether", "lemon io", "toptal", "mercor", "micro1", "turing", "braintrust", "crossover",
               "valce talent solutions", "jobs via dice", "get it", "cybercoders", "jobot", "insight global", "tek systems", "teksystems"}
TRACK = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "source",
         "gh_src", "lever-source", "refcode", "src", "trk", "trackingId", "position", "pageNum"}


def norm_url(u: str) -> str:
    if not u:
        return ""
    p = urlsplit(u.strip())
    q = "&".join(kv for kv in p.query.split("&") if kv and kv.split("=")[0] not in TRACK)
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme.lower() or "https", p.netloc.lower().replace("www.", ""), path, q, ""))


def norm_txt(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|co|corp|the|remote|us|usa)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def richer(a: dict, b: dict) -> dict:
    """Return merged row preferring the one with more description; union sources/tags."""
    keep, other = (a, b) if len(a.get("description") or "") >= len(b.get("description") or "") else (b, a)
    out = dict(keep)
    for k in ("apply_url", "salary", "salary_min", "salary_max", "posted_at", "location"):
        if not out.get(k) and other.get(k):
            out[k] = other[k]
    if out.get("remote") is None and other.get("remote") is not None:
        out["remote"] = other["remote"]
    if out.get("open_status") != "open" and other.get("open_status") == "open":
        out["open_status"] = "open"
    out["tags"] = sorted(set(out.get("tags") or []) | set(other.get("tags") or []))
    out["sources"] = sorted(set(out.get("sources") or [out["source"]]) | set(other.get("sources") or [other["source"]]))
    out["alt_urls"] = sorted(set(out.get("alt_urls") or []) | {other["url"]} | set(other.get("alt_urls") or []))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = []
    per_source = {}
    for f in sorted(glob.glob(os.path.join(a.raw, "*.jsonl"))):
        n = 0
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not r.get("url") or not r.get("title"):
                    continue
                rows.append(r)
                n += 1
        per_source[os.path.basename(f)] = n
    by_url: dict[str, dict] = {}
    for r in rows:
        k = norm_url(r["url"])
        by_url[k] = richer(by_url[k], r) if k in by_url else r
    by_ct: dict[tuple, dict] = {}
    for r in by_url.values():
        k = (norm_txt(r.get("company")), norm_txt(r.get("title")))
        if k[0] in AGGREGATORS:  # one company name, hundreds of distinct partner roles: URL dedupe only
            k = ("", r["url"])
        if k[0] and k[1] and k in by_ct:
            by_ct[k] = richer(by_ct[k], r)
        else:
            by_ct[k if (k[0] and k[1]) else ("", r["url"])] = r
    merged = list(by_ct.values())
    for r in merged:
        r.setdefault("sources", [r["source"]])
        r.setdefault("alt_urls", [])
        r["region"] = common.region_of(r.get("location") or "", r.get("remote"))  # re-derive with the current parser
        if r.get("source") == "reddit" and (r.get("company") or "").startswith("u/"):
            m = re.search(r"\bat\s+([A-Z][\w.&'-]+(?:\s+[A-Z][\w.&'-]+){0,2})", r.get("title") or "")
            if m:
                r["company"] = f"{m.group(1).strip()} (via {r['company']})"
        if norm_txt(r.get("company")) in AGGREGATORS:
            r["aggregator"] = True
            if norm_txt(r.get("company")) == "jobgether":
                r["company"] = "Jobgether (on behalf of a partner)"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in merged:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"per_file": per_source, "raw_total": len(rows), "after_url_dedupe": len(by_url),
                      "after_company_title_dedupe": len(merged)}, indent=1))


if __name__ == "__main__":
    main()
