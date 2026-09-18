"""Run the two-persona ownership-transfer scenario in healthy and fault modes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from synthetic_lab.evaluation import run_ownership_transfer


async def main() -> int:
    payload = {
        "healthy": await run_ownership_transfer(),
        "seeded_owner_transfer_leak": await run_ownership_transfer(fault="owner_transfer_leak"),
    }
    path = Path("outputs/ownership-transfer-evaluation.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
