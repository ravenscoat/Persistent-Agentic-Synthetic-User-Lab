"""Apply the PostgreSQL schemas used by the durable lab stores."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from synthetic_lab.demo.postgres_store import PostgresDemoStore


async def main() -> None:
    dsn = os.environ.get("SUL_POSTGRES_DSN", "postgresql://synthetic_lab:synthetic_lab@localhost:5432/synthetic_lab")
    store = PostgresDemoStore(dsn)
    root = Path(__file__).resolve().parents[1]
    await store.apply_migration(str(root / "migrations" / "002_initial_postgres.sql"))
    await store.apply_migration(str(root / "migrations" / "003_demo_business_postgres.sql"))
    print("PostgreSQL schemas applied.")


if __name__ == "__main__":
    asyncio.run(main())
