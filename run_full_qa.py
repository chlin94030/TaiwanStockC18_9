"""Offline regression runner for Alpha Radar V18.8 frozen rank, compact UX, OOS gates.

This suite intentionally avoids live-network assertions.  It verifies architecture,
price provenance, Doctor decisions, research fallbacks, app render paths and syntax.
Real-market walk-forward validation remains in the separate GitHub workflows.
"""
from __future__ import annotations
import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = [
    "test_v188_rank_contract.py",
    "test_v187_experience.py",
    "test_v186_optimization.py",
    "test_v18_multifactor.py",
    "test_v184_integrated.py",
    "test_v185_experience.py",
    "test_architecture.py",
    "test_doctor_v16_7.py",
    "test_verified_price_v16_7_1.py",
    "test_full_qa_v16_7_2.py",
    "test_panic_reversal_v16_8.py",
    "test_v17_goal_complete.py",
    "test_v17_1_sync_briefing.py",
    "test_v17_3_mobile_board.py",
    "test_v174_integrity.py",
    "test_v17_5_experience.py",
    "test_research_data_v16_6_2.py",
    "test_research_details.py",
    "test_research_preload_v16_6_2.py",
    "test_research_ui_v16_6_2.py",
    "smoke_app_import.py",
    "smoke_decision_views.py",
    "smoke_all_views_v16_7_2.py",
    "smoke_app_main.py",
]

def run(cmd):
    print("$", " ".join(map(str, cmd)), flush=True)
    env = dict(os.environ)
    env["ALPHA_RADAR_DISABLE_GLOBAL_NETWORK"] = "1"
    env["ALPHA_RADAR_DISABLE_NEWS_NETWORK"] = "1"
    return subprocess.run(cmd, cwd=ROOT, check=False, env=env).returncode


def main() -> int:
    if run([sys.executable, "-m", "compileall", "-q", "."]):
        print("FAIL compileall")
        return 1
    for test in TESTS:
        rc = run([sys.executable, test])
        if rc:
            print("FAIL", test)
            return rc
    print("ALL V18.8 FIXED RANK + OOS HONESTY + MARKET/READINESS + ORIGINAL CORE QA PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
