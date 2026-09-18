from __future__ import annotations

from synthetic_lab.evaluation import run_memory_ablation, run_suite


def test_all_seeded_faults_are_detected_without_healthy_false_positives():
    import asyncio

    rows = asyncio.run(run_suite())
    assert len(rows) == 8
    assert all(row["detected"] for row in rows)
    assert not any(row["healthy_false_positive"] for row in rows)


def test_memory_ablation_only_raises_cross_session_suspicion_when_memory_is_available():
    import asyncio

    report = asyncio.run(run_memory_ablation(trials=2))
    assert report["arms"]["memory_off"]["fault_suspicion_rate"] == 0
    assert report["arms"]["memory_on"]["fault_suspicion_rate"] == 1
    assert report["arms"]["memory_on"]["healthy_false_positive_rate"] == 0
    assert report["arms"]["memory_off"]["verifier_confirmation_rate"] == 1
    assert report["arms"]["memory_on"]["verifier_confirmation_rate"] == 1
