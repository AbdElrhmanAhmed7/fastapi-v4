"""Validate the bundled mission content pack.

    python scripts/validate_content.py            # human readable
    python scripts/validate_content.py --json     # machine readable
    python scripts/validate_content.py --strict   # warnings fail too

Exit code 0 = clean, 1 = errors (or warnings with --strict).
Wire this into CI so an unwinnable mission can never reach main.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.missions.registry import load_content  # noqa: E402
from app.missions.validation import validate_content  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    args = parser.parse_args()

    content_dir = args.content_dir or get_settings().content_dir
    report = validate_content(load_content(content_dir))

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.render())

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
