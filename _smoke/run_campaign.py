"""Dev harness: play the whole campaign in-process and print the outcome."""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nova-backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nova-backend", "scripts"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from autopilot import Autopilot  # noqa: E402

difficulty = sys.argv[1] if len(sys.argv) > 1 else "standard"
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
verbose = "-v" in sys.argv

with TestClient(create_app()) as client:
    pilot = Autopilot(client, verbose=verbose)
    started = time.time()
    try:
        report = pilot.run_campaign("SmokePilot", difficulty=difficulty, seed=seed)
    except Exception as exc:  # noqa: BLE001
        print("\n".join(pilot.log[-25:]))
        print(f"FAILED [{difficulty} seed={seed}]: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    print(f"CAMPAIGN OK [{difficulty} seed={seed}] in {time.time() - started:.1f}s")
    for outcome in report["autopilot"]:
        print(
            f"  {outcome['mission_id']:<24} {outcome['score_pct']:>6}%  {outcome['grade']}  "
            f"mistakes={outcome['mistakes']} steps={outcome['steps']}"
        )
    print(
        json.dumps(
            {k: report[k] for k in ("missions_completed", "missions_total", "average_score_pct", "readiness", "readiness_grade")},
            ensure_ascii=False,
        )
    )
