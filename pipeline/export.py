"""Export scored jobs to CSV, a compact JSON for the board page, and a markdown table.

    python pipeline/export.py --in <data>/scored.jsonl --outdir <data>/out [--min-score 40]
"""
from __future__ import annotations
import argparse, csv, json, os, sys

sys.stdout.reconfigure(encoding="utf-8")
BASE_COLS = ["score", "title", "company", "seniority", "employment_type", "remote", "region", "location", "salary",
             "posted_at", "verify_status", "sources", "url", "apply_url", "why"]
COLS = BASE_COLS + ["coverage_pct", "top_missing"]


def load_tailor(inp: str, tailor_path: str | None = None) -> tuple[dict[str, dict], bool]:
    path = tailor_path or os.path.join(os.path.dirname(os.path.abspath(inp)), "tailor.jsonl")
    if tailor_path is None and not os.path.exists(path):
        return {}, False
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return {str(row["id"]): row for row in rows}, True


def top_missing(row: dict) -> str:
    missing = sorted(row.get("missing") or [], key=lambda item: item.get("count_in_posting", 0), reverse=True)
    return ";".join(item["term"] for item in missing[:5])


def export(inp: str, outdir: str, min_score: int = 0, tailor_path: str | None = None) -> dict:
    rows = [json.loads(l) for l in open(inp, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r["score"] >= min_score and (r.get("verify") or {}).get("status") != "closed"]
    tailor, has_tailor = load_tailor(inp, tailor_path)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "jobs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS if has_tailor else BASE_COLS)
        for r in rows:
            values = [r["score"], r["title"], r["company"], r["seniority"], r["employment_type"], r["remote"],
                      r["region"], r["location"], r.get("salary") or "", r.get("posted_at") or "",
                      (r.get("verify") or {}).get("status"), "|".join(r.get("sources") or []), r["url"],
                      r.get("apply_url") or "", "; ".join(r["why"])]
            if has_tailor:
                tailored = tailor.get(str(r.get("id")))
                values.extend([tailored.get("coverage_pct") if tailored else "", top_missing(tailored) if tailored else ""])
            w.writerow(values)
    compact = [{k: r.get(k) for k in ["id", "score", "title", "company", "seniority", "employment_type", "remote",
                                      "region", "location", "salary", "salary_min", "salary_max", "posted_at",
                                      "sources", "url", "apply_url", "why", "tags"]}
               | {"verify": (r.get("verify") or {}).get("status"), "snippet": (r.get("description") or "")[:600]}
               for r in rows]
    if has_tailor:
        for item in compact:
            tailored = tailor.get(str(item.get("id")))
            item["coverage_pct"] = tailored.get("coverage_pct") if tailored else None
            item["top_missing"] = top_missing(tailored) if tailored else None
    json.dump(compact, open(os.path.join(outdir, "jobs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    lines = ["| # | Score | Role | Company | Type | Where | Posted | Open? | Link |", "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        where = "Remote" if r.get("remote") else (r.get("location") or "?")
        if r.get("remote") and r.get("region") not in ("worldwide", "unknown"):
            where += f" ({r['region']})"
        lines.append(f"| {i} | {r['score']} | {r['title'][:60]} | {r['company'][:40]} | {r['employment_type']} | {where[:40]} | "
                     f"{r.get('posted_at') or '?'} | {(r.get('verify') or {}).get('status')} | [open]({r['url']}) |")
    open(os.path.join(outdir, "jobs.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    by_src = {}
    for r in rows:
        for s in r.get("sources") or []:
            by_src[s] = by_src.get(s, 0) + 1
    return {"exported": len(rows), "by_source": by_src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--tailor")
    a = ap.parse_args()
    print(json.dumps(export(a.inp, a.outdir, a.min_score, a.tailor), indent=1))


if __name__ == "__main__":
    main()
