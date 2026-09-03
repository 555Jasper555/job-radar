"""Export scored jobs to CSV, a compact JSON for the board page, and a markdown table.

    python pipeline/export.py --in <data>/scored.jsonl --outdir <data>/out [--min-score 40]
"""
from __future__ import annotations
import argparse, csv, json, os, sys

sys.stdout.reconfigure(encoding="utf-8")
COLS = ["score", "title", "company", "seniority", "employment_type", "remote", "region", "location", "salary",
        "posted_at", "verify_status", "sources", "url", "apply_url", "why"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--min-score", type=int, default=0)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.inp, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r["score"] >= a.min_score and (r.get("verify") or {}).get("status") != "closed"]
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "jobs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        for r in rows:
            w.writerow([r["score"], r["title"], r["company"], r["seniority"], r["employment_type"], r["remote"],
                        r["region"], r["location"], r.get("salary") or "", r.get("posted_at") or "",
                        (r.get("verify") or {}).get("status"), "|".join(r.get("sources") or []), r["url"],
                        r.get("apply_url") or "", "; ".join(r["why"])])
    compact = [{k: r.get(k) for k in ["id", "score", "title", "company", "seniority", "employment_type", "remote",
                                      "region", "location", "salary", "salary_min", "salary_max", "posted_at",
                                      "sources", "url", "apply_url", "why", "tags"]}
               | {"verify": (r.get("verify") or {}).get("status"), "snippet": (r.get("description") or "")[:600]}
               for r in rows]
    json.dump(compact, open(os.path.join(a.outdir, "jobs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    lines = ["| # | Score | Role | Company | Type | Where | Posted | Open? | Link |", "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        where = "Remote" if r.get("remote") else (r.get("location") or "?")
        if r.get("remote") and r.get("region") not in ("worldwide", "unknown"):
            where += f" ({r['region']})"
        lines.append(f"| {i} | {r['score']} | {r['title'][:60]} | {r['company'][:40]} | {r['employment_type']} | {where[:40]} | "
                     f"{r.get('posted_at') or '?'} | {(r.get('verify') or {}).get('status')} | [open]({r['url']}) |")
    open(os.path.join(a.outdir, "jobs.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    by_src = {}
    for r in rows:
        for s in r.get("sources") or []:
            by_src[s] = by_src.get(s, 0) + 1
    print(json.dumps({"exported": len(rows), "by_source": by_src}, indent=1))


if __name__ == "__main__":
    main()
