from __future__ import annotations

from synthetic_lab.evaluation import run_suite


def test_all_seeded_faults_are_detected_without_healthy_false_positives():
    import asyncio

    rows = asyncio.run(run_suite())
    assert len(rows) == 8
    assert all(row["detected"] for row in rows)
    assert not any(row["healthy_false_positive"] for row in rows)
