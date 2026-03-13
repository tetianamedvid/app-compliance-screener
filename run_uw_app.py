#!/usr/bin/env python3
"""
Start UW Lookup app. Run from project folder:  python3 run_uw_app.py

Runs full sync first (validate, fetch profiles from Trino, UW for missing apps), then starts
the dashboard. You only open http://localhost:8501. Set SKIP_STARTUP_SYNC=1 in .env to skip sync.
"""
import os
import sys
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
os.chdir(root)
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUNBUFFERED": "1"}
skip_sync = os.environ.get("SKIP_STARTUP_SYNC", "").strip().lower() in ("1", "true", "yes")

if not skip_sync:
    print("Running full sync (validate → fetch profiles → UW for missing)…", flush=True)
    r = subprocess.run(
        [sys.executable, "-u", str(root / "scripts" / "old_uw" / "run_all.py"), "--fetch-profiles", "--uw-missing", "--uw-scrape", "--force"],
        cwd=str(root),
        env=env,
    )
    if r.returncode != 0:
        print("Sync had issues (return code", r.returncode, "). Starting dashboard anyway.", flush=True)
    else:
        print("Sync done.", flush=True)

print("Starting UW Lookup…", flush=True)
print("Open http://localhost:8501 in your browser. Press Ctrl+C here to stop.", flush=True)
subprocess.run(
    [sys.executable, "-u", "-m", "streamlit", "run", "streamlit_uw.py", "--server.port", "8501"],
    cwd=root,
    env=env,
)
