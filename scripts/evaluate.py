"""Run the deterministic demo evaluation and print a JSON report."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from synthetic_lab.evaluation import run_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="also write the JSON report to this path")
    args = parser.parse_args()
    rows = asyncio.run(run_suite())
    payload = {"cases": rows, "case_count": len(rows), "passed": all(row["detected"] for row in rows)}
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
