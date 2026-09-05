"""Register (or replace) the Windows scheduled task "gig-scout".

    python gigs/register_task.py            # 07:30 then every 6 h for 12 h -> 07:30 / 13:30 / 19:30
    python gigs/register_task.py --show     # just print the task
    python gigs/register_task.py --run      # fire it once now (hidden window)

daily.ps1 is addressed by the MAIN checkout path so a merged branch is what runs.
"""
import argparse
import subprocess
import sys

TASK = "gig-scout"
SCRIPT = r"C:\Users\endle\MyStuff\job-radar\gigs\daily.ps1"
TR = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {SCRIPT} -Days 1'


def sh(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True)
    return (p.stdout + p.stderr).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if not (a.show or a.run):
        print(sh(["schtasks", "/create", "/tn", TASK, "/tr", TR, "/sc", "DAILY", "/st", "07:30",
                  "/ri", "360", "/du", "12:01", "/f"]))
    if a.run:
        print(sh(["schtasks", "/run", "/tn", TASK]))
    out = sh(["schtasks", "/query", "/tn", TASK, "/fo", "list", "/v"])
    for line in out.splitlines():
        if any(k in line for k in ("TaskName", "Next Run", "Repeat: Every", "Task To Run", "Status", "Last Run", "Last Result")):
            print(line.strip())
    if "ERROR" in out:
        sys.exit(1)


if __name__ == "__main__":
    main()
