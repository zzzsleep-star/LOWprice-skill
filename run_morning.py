#!/usr/bin/env python3
"""Update the prior exchange session, then generate the low-price top ten."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument("--project-root",required=True,type=Path); p.add_argument("--run-date"); p.add_argument("--workers",type=int,default=4)
a=p.parse_args(); here=Path(__file__).resolve().parent
cmd=[sys.executable,str(here/"incremental_update.py"),"--project-root",str(a.project_root),"--workers",str(a.workers)]
if a.run_date: cmd += ["--run-date",a.run_date]
subprocess.run(cmd,check=True)
status=json.loads((a.project_root/"outputs"/"daily_live"/"update_status.json").read_text(encoding="utf-8"))
subprocess.run([sys.executable,str(here/"screen_daily.py"),"--project-root",str(a.project_root),"--as-of",status["target_trade_date"]],check=True)
