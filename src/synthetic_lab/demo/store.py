from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL, trial_start TEXT NOT NULL, trial_days INTEGER NOT NULL DEFAULT 7, plan TEXT NOT NULL DEFAULT 'free', onboarding_step INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memberships (account_id TEXT NOT NULL, member_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(account_id, member_id));
CREATE TABLE IF NOT EXISTS purchase_operations (operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL, account_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, created_at TEXT NOT NULL);
"""


@dataclass(frozen=True)
class PurchaseResult:
    operation_id: str
    charges: int
    total_cents: int


class DemoStore:
    """Small resettable business state store used by the demo and verifiers."""

    def __init__(self, path: str | Path = ":memory:", *, fault: str | None = None) -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.fault = fault
        self.business_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def close(self) -> None:
        self.connection.close()

    def advance_days(self, days: int) -> datetime:
        if days < 0:
            raise ValueError("business time cannot move backwards")
        self.business_time += timedelta(days=days)
        return self.business_time

    def create_account(self, account_id: str, email: str, password: str) -> None:
        self.connection.execute("INSERT INTO accounts(id,email,password,trial_start) VALUES (?,?,?,?)", (account_id, email, password, self.business_time.isoformat()))
        self.connection.commit()

    def account(self, account_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError(account_id)
        return dict(row)

    def trial_active(self, account_id: str) -> bool:
        account = self.account(account_id)
        duration = 5 if self.fault == "trial_expires_day_5" else account["trial_days"]
        started = datetime.fromisoformat(account["trial_start"])
        return self.business_time < started + timedelta(days=duration)

    def purchase(self, account_id: str, operation_id: str, amount_cents: int) -> PurchaseResult:
        if amount_cents <= 0:
            raise ValueError("amount must be positive")
        now = self.business_time.isoformat()
        self.connection.execute("INSERT OR IGNORE INTO purchase_operations(operation_id,account_id,amount_cents,created_at) VALUES (?,?,?,?)", (operation_id, account_id, amount_cents, now))
        count = 2 if self.fault == "duplicate_charge" else 1
        for _ in range(count):
            self.connection.execute("INSERT INTO ledger(operation_id,account_id,amount_cents,created_at) VALUES (?,?,?,?)", (operation_id, account_id, amount_cents, now))
        self.connection.commit()
        return self.ledger_for(operation_id)

    def ledger_for(self, operation_id: str) -> PurchaseResult:
        row = self.connection.execute("SELECT COUNT(*) AS count, COALESCE(SUM(amount_cents),0) AS total FROM ledger WHERE operation_id = ?", (operation_id,)).fetchone()
        return PurchaseResult(operation_id, int(row["count"]), int(row["total"]))

    def set_onboarding_step(self, account_id: str, step: int) -> None:
        if step < 0:
            raise ValueError("step cannot be negative")
        if self.fault == "onboarding_resets" and step > 1:
            step = 1
        self.connection.execute("UPDATE accounts SET onboarding_step = ? WHERE id = ?", (step, account_id))
        self.connection.commit()

    def transfer_owner(self, account_id: str, old_member: str, new_member: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO memberships VALUES (?,?,?)", (account_id, old_member, "owner"))
        self.connection.execute("INSERT OR REPLACE INTO memberships VALUES (?,?,?)", (account_id, new_member, "owner"))
        if self.fault != "owner_transfer_leak":
            self.connection.execute("UPDATE memberships SET role = 'member' WHERE account_id = ? AND member_id = ?", (account_id, old_member))
        self.connection.commit()

    def role(self, account_id: str, member_id: str) -> str | None:
        row = self.connection.execute("SELECT role FROM memberships WHERE account_id = ? AND member_id = ?", (account_id, member_id)).fetchone()
        return None if row is None else str(row["role"])
