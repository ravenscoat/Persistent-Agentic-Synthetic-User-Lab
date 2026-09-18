"""Run the deterministic memory-on versus memory-off evaluation."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from synthetic_lab.evaluation import run_memory_ablation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("outputs/memory-ablation.json"))
    args = parser.parse_args()
    payload = asyncio.run(run_memory_ablation(args.trials))
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
