"""Run the deterministic demo scenarios and report detection metrics.

This deliberately does not ask an LLM for the verdict.  It is the regression
test/evaluation harness for the agent's independent verification layer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from synthetic_lab.demo.scenarios import SCENARIOS
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.verification.invariants import DemoVerificationContext, DemoVerifier

FAULTS = {
    "trial_return": (None, "trial_expires_day_5"),
    "payment_retry": (None, "duplicate_charge"),
    "ownership_transfer": (None, "owner_transfer_leak"),
    "interrupted_onboarding": (None, "onboarding_resets"),
}


async def run_case(scenario_id: str, fault: str | None) -> dict[str, object]:
    store = DemoStore(fault=fault)
    account_id = "acct-eval"
    store.create_account(account_id, "eval@example.test", "not-a-real-password")
    operation_id = "purchase-1" if scenario_id == "payment_retry" else None
    try:
        result = SCENARIOS[scenario_id](store, account_id, **({"operation_id": operation_id} if operation_id else {}))
        verified = await DemoVerifier().check(result.invariant_id, DemoVerificationContext(store, account_id, operation_id))
        expected_verdict = "confirmed" if fault else "satisfied"
        return {
            "scenario_id": scenario_id,
            "fault": fault,
            "verdict": verified.verdict,
            "expected_verdict": expected_verdict,
            "expected": verified.expected,
            "actual": verified.actual,
            "detected": verified.verdict == expected_verdict,
            "healthy_false_positive": fault is None and verified.verdict != "satisfied",
        }
    finally:
        store.close()


async def run_suite() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario_id, (healthy, fault) in FAULTS.items():
        rows.append(await run_case(scenario_id, healthy))
        rows.append(await run_case(scenario_id, fault))
    return rows


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
