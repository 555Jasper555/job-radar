"""job-radar — one command: scrape every source, merge, verify, score, export.

    python run.py                 # everything
    python run.py --skip-sources  # pipeline only (reuse raw/)
    python run.py --only hn,ats   # a subset of sources, then the pipeline

Data dir: C:/Users/endle/MyStuff/_job-radar-data  (override with JOB_RADAR_DATA)
"""
from __future__ import annotations
import argparse, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("JOB_RADAR_DATA", r"C:/Users/endle/MyStuff/_job-radar-data")
SOURCES = ["hn", "ats", "remote_boards", "html_boards", "reddit"]
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def run(args: list[str], label: str) -> int:
    t = time.time()
    print(f"\n=== {label}: {' '.join(args[1:])}", flush=True)
    rc = subprocess.call(args, cwd=ROOT, env=ENV)
    print(f"=== {label} exit {rc} in {time.time() - t:.0f}s", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-sources", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--min-score", type=int, default=35)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(os.path.join(DATA, "raw"), exist_ok=True)
    if not a.skip_sources:
        names = [s for s in a.only.split(",") if s] or SOURCES
        for s in names:
            script = os.path.join(ROOT, "sources", f"{s}.py")
            if not os.path.exists(script):
                print(f"!! no source script {script}")
                continue
            run([PY, script, "--out", os.path.join(DATA, "raw", f"{s}.jsonl")], s)
    merged = os.path.join(DATA, "merged.jsonl")
    verified = os.path.join(DATA, "verified.jsonl")
    scored = os.path.join(DATA, "scored.jsonl")
    run([PY, "pipeline/merge.py", "--raw", os.path.join(DATA, "raw"), "--out", merged], "merge")
    run([PY, "pipeline/verify.py", "--in", merged, "--out", verified, "--workers", str(a.workers)], "verify")
    run([PY, "pipeline/score.py", "--in", verified, "--out", scored], "score")
    run([PY, "pipeline/export.py", "--in", scored, "--outdir", os.path.join(DATA, "out"),
         "--min-score", str(a.min_score)], "export")


if __name__ == "__main__":
    main()
