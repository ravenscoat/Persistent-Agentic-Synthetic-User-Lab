from pathlib import Path


def test_oracle_migration_is_present_and_non_destructive() -> None:
    migration = Path(__file__).parents[2] / "migrations" / "001_initial_oracle.sql"
    text = migration.read_text(encoding="utf-8")
    assert "DROP TABLE" not in text.upper()
    assert "SUL_RUNS" in text.upper()
    assert "SUL_MEMORY" in text.upper()
