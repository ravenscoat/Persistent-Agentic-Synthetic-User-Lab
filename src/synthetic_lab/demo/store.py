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
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', assignee TEXT);
CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', started_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, message TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
"""


@dataclass(frozen=True)
class PurchaseResult:
    operation_id: str
    charges: int
    total_cents: int


class DemoStore:
    """Small resettable business state store used by the demo and verifiers."""

    def __init__(self, path: str | Path = ":memory:", *, fault: str | None = None) -> None:
        # FastAPI/Playwright requests may execute on a worker thread. The demo
        # store is local and resettable; allow that access pattern explicitly.
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.fault = fault
        self.business_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def close(self) -> None:
        self.connection.close()

    def workflow_snapshot(self) -> dict[str, Any]:
        accounts = self.connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        projects = self.connection.execute("SELECT COUNT(*) FROM projects WHERE account_id='account-1'").fetchone()[0]
        task = self.connection.execute("SELECT status FROM tasks WHERE id='task-1' AND project_id='project-1'").fetchone()
        subscription = self.connection.execute("SELECT status FROM subscriptions WHERE id='subscription-1' AND account_id='account-1'").fetchone()
        charges, total = self.connection.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM ledger WHERE account_id='account-1' AND operation_id='purchase-1'").fetchone()
        return dict(accounts=accounts, projects=projects, task=task[0] if task else None, subscription=subscription[0] if subscription else None, charges=charges, total=total)

    def advance_days(self, days: int) -> datetime:
        if days < 0:
            raise ValueError("business time cannot move backwards")
        self.business_time += timedelta(days=days)
        return self.business_time

    def create_account(self, account_id: str, email: str, password: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO accounts(id,email,password,trial_start) VALUES (?,?,?,?)", (account_id, email, password, self.business_time.isoformat()))
        self.connection.commit()

    def account(self, account_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError(account_id)
        return dict(row)

    def account_exists(self, account_id: str | None = None) -> bool:
        if account_id is None:
            row = self.connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
        else:
            row = self.connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row is not None

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

    def create_project(self, account_id: str, project_id: str, name: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO projects(id,account_id,name,created_at) VALUES (?,?,?,?)", (project_id, account_id, name, self.business_time.isoformat()))
        self.connection.commit()

    def create_task(self, project_id: str, task_id: str, title: str, assignee: str | None = None) -> None:
        self.connection.execute("INSERT OR IGNORE INTO tasks(id,project_id,title,status,assignee) VALUES (?,?,?,?,?)", (task_id, project_id, title, "pending", assignee))
        self.connection.commit()

    def complete_task(self, task_id: str) -> None:
        status = "pending" if self.fault == "task_completion_stale" else "completed"
        self.connection.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        self.connection.commit()

    def task_status(self, task_id: str) -> str:
        row = self.connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return str(row["status"])

    def invite(self, account_id: str, invitation_id: str, email: str) -> None:
        self.connection.execute("INSERT INTO invitations(id,account_id,email,status,created_at) VALUES (?,?,?,?,?)", (invitation_id, account_id, email, "pending", self.business_time.isoformat()))
        self.connection.commit()

    def subscribe(self, account_id: str, subscription_id: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO subscriptions(id,account_id,status,started_at) VALUES (?,?,?,?)", (subscription_id, account_id, "active", self.business_time.isoformat()))
        self.connection.commit()

    def cancel_subscription(self, subscription_id: str) -> None:
        self.connection.execute("UPDATE subscriptions SET status = 'active' WHERE id = ?" if self.fault == "cancel_ignored" else "UPDATE subscriptions SET status = 'cancelled' WHERE id = ?", (subscription_id,))
        self.connection.commit()

    def notifications(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,message,read,created_at FROM notifications WHERE account_id = ? ORDER BY created_at DESC", (account_id,)).fetchall()
        return [dict(row) for row in rows]

    def invoices(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,operation_id,amount_cents,created_at FROM ledger WHERE account_id = ? ORDER BY created_at DESC, id DESC", (account_id,)).fetchall()
        return [dict(row) for row in rows]

    def memberships(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT member_id,role FROM memberships WHERE account_id = ? ORDER BY member_id", (account_id,)).fetchall()
        return [dict(row) for row in rows]

    def invitations(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,email,status,created_at FROM invitations WHERE account_id = ? ORDER BY created_at DESC", (account_id,)).fetchall()
        return [dict(row) for row in rows]
