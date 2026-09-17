"""Benchmark fresh browser runs using the configured real Ollama model."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def run_once(timeout: int) -> dict[str, object]:
    started = time.perf_counter()
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).with_name("run_e2e.py")), "--real-model"], capture_output=True, text=True, timeout=timeout)
        row: dict[str, object] = {"exit_code": completed.returncode, "timed_out": False, "duration_seconds": round(time.perf_counter() - started, 2)}
        try:
            report = json.loads(completed.stdout)
            row["agent"] = report.get("agent")
            row["verification"] = report.get("verification")
            row["completed"] = completed.returncode == 0 and report.get("signup_verified") is True and report.get("agent", {}).get("status") == "completed"
            row["actions"] = report.get("actions", [])
        except json.JSONDecodeError:
            row["completed"] = False
            row["error_tail"] = (completed.stderr or completed.stdout)[-1000:]
        return row
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "timed_out": True, "duration_seconds": timeout, "completed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--output", type=Path, default=Path("artifacts/real-model-benchmark.json"))
    args = parser.parse_args()
    if args.runs < 1 or args.timeout < 1:
        parser.error("runs and timeout must be positive")
    rows = [run_once(args.timeout) for _ in range(args.runs)]
    payload = {"runs": rows, "run_count": len(rows), "completion_rate": sum(bool(row["completed"]) for row in rows) / len(rows), "verification_confirmed": sum(row.get("verification", {}).get("verdict") == "confirmed" for row in rows if isinstance(row.get("verification"), dict))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload["completion_rate"] >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
