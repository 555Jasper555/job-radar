#!/usr/bin/env python3
"""Inject jobs.json into board/template.html and write the finished board page.

    python board/build.py --jobs <jobs.json> --out "<vault html path>"

The template carries hello-kitty's design stamp as its first line; it is copied through
verbatim. Only the placeholders below are replaced:

    /*__JOBS_JSON__*/   inside <script id="jobs-data" type="application/json">
    __BUILT_AT__        human date-time of this build (local clock)
    __TOTAL__ __OPEN__ __REMOTE__ __FRESH7__   counts
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
STAMP_PREFIX = "<!-- design: hello-kitty"


def counts(jobs, now):
    fresh_cut = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    total = len(jobs)
    open_ = sum(1 for j in jobs if j.get("verify") == "open")
    remote = sum(1 for j in jobs if j.get("remote") is True)
    fresh = sum(1 for j in jobs if (j.get("posted_at") or "") >= fresh_cut)
    return total, open_, remote, fresh


def fmt(n):
    return f"{n:,}"


def build(jobs_path: Path, out_path: Path, template_path: Path = TEMPLATE) -> dict:
    template = template_path.read_text(encoding="utf-8")
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    if not isinstance(jobs, list):
        raise SystemExit("jobs.json must be a JSON array")
    if not jobs:
        raise SystemExit("jobs.json is empty — refusing to build an empty board")

    now = datetime.now()
    total, open_, remote, fresh = counts(jobs, now)

    payload = json.dumps(jobs, ensure_ascii=False, separators=(",", ":"))
    # A literal "</script>" (or "<!--") inside the JSON would end the data block early.
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")

    if "/*__JOBS_JSON__*/" not in template:
        raise SystemExit("template is missing the /*__JOBS_JSON__*/ placeholder")

    html = template.replace("/*__JOBS_JSON__*/", payload, 1)
    html = (
        html.replace("__BUILT_AT__", now.strftime("%-d %b %Y, %H:%M") if sys.platform != "win32" else now.strftime("%#d %b %Y, %H:%M"))
        .replace("__TOTAL__", fmt(total))
        .replace("__OPEN__", fmt(open_))
        .replace("__REMOTE__", fmt(remote))
        .replace("__FRESH7__", fmt(fresh))
    )

    # Preserve the stamp verbatim as the very first line of the output.
    head = template.splitlines()[:3]
    stamp = next((l for l in head if l.startswith(STAMP_PREFIX)), None)
    if stamp is None:
        raise SystemExit("template carries no hello-kitty stamp in its first lines — aborting")
    if stamp not in html.splitlines()[:3]:
        raise SystemExit("stamp line was not preserved — aborting")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    return {"total": total, "open": open_, "remote": remote, "fresh7": fresh, "bytes": len(html.encode("utf-8")), "out": str(out_path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", required=True, help="path to out/jobs.json")
    ap.add_argument("--out", required=True, help="output html path (usually the vault Wiki path)")
    ap.add_argument("--template", default=str(TEMPLATE), help="template html (default: board/template.html)")
    args = ap.parse_args(argv)
    info = build(Path(args.jobs), Path(args.out), Path(args.template))
    print(
        f"built {info['out']}\n  postings {info['total']:,} · open {info['open']:,} · remote {info['remote']:,} · "
        f"posted<=7d {info['fresh7']:,} · {info['bytes']/1024/1024:.1f} MB"
    )


if __name__ == "__main__":
    main()
