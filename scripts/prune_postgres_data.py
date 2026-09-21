"""Preview or apply scoped retention cleanup for Synthetic User Lab data.

The command is intentionally dry-run by default. It deletes only records in a
named PostgreSQL schema whose parent runs are older than the requested period;
it never touches a database until ``--apply`` is present.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than-days", type=int, required=True, help="Delete runs created before this many days ago.")
    parser.add_argument("--schema", default="public", help="PostgreSQL schema containing sul_* tables.")
    parser.add_argument("--apply", action="store_true", help="Perform the deletion. Without this flag, only report the candidate count.")
    args = parser.parse_args()
    if args.older_than_days < 1:
        parser.error("--older-than-days must be at least 1")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", args.schema):
        parser.error("--schema must be a simple PostgreSQL identifier")
    dsn = os.getenv("SUL_POSTGRES_DSN")
    if not dsn:
        parser.error("SUL_POSTGRES_DSN is required")
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Install the PostgreSQL extra: pip install -e '.[postgres]'") from exc

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    with psycopg.connect(dsn, options=f"-c search_path={args.schema}") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sul_runs WHERE created_at < %s", (cutoff,))
            run_ids = [row[0] for row in cursor.fetchall()]
            print(f"Retention candidate runs: {len(run_ids)} (created before {cutoff.isoformat()})")
            if not args.apply or not run_ids:
                return 0
            # Child records have explicit deletion order because migrations do
            # not rely on database-wide cascade behavior.
            for table in ("sul_findings", "sul_expectations", "sul_memory", "sul_events", "sul_sessions"):
                cursor.execute(f"DELETE FROM {table} WHERE run_id = ANY(%s)", (run_ids,))
            cursor.execute("DELETE FROM sul_runs WHERE id = ANY(%s)", (run_ids,))
        connection.commit()
    print(f"Deleted {len(run_ids)} run(s) and their associated durable records from schema {args.schema!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
