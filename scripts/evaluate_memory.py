"""Run the deterministic memory-on versus memory-off evaluation."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from synthetic_lab.evaluation import run_memory_ablation
from synthetic_lab.config import Settings
from synthetic_lab.llm import build_local_model


async def evaluate(trials: int, real_model: bool):
    model = build_local_model(Settings()) if real_model else None
    try:
        return await run_memory_ablation(trials, model=model)
    finally:
        if model is not None:
            await model.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("outputs/memory-ablation.json"))
    parser.add_argument("--real-model", action="store_true", help="measure the configured local model and its reported token usage")
    args = parser.parse_args()
    payload = asyncio.run(evaluate(args.trials, args.real_model))
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
