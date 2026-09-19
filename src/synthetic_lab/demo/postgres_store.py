from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .store import PurchaseResult


class PostgresDemoStore:
    """PostgreSQL business store with the same public operations as DemoStore."""

    def __init__(self, dsn: str, *, fault: str | None = None, schema: str | None = None) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("install synthetic-user-lab[postgres] first") from exc
        if schema:
            import re
            if not re.fullmatch(r"sul_eval_[a-f0-9]{32}", schema):
                raise ValueError("invalid evaluation schema")
            from psycopg import sql
            with psycopg.connect(dsn) as connection:
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self._connect = lambda: psycopg.connect(dsn, **({"options": f"-c search_path={schema}"} if schema else {}))
        self.fault = fault
        self.business_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def apply_migration(self, path: str) -> None:
        from pathlib import Path
        statements = [part.strip() for part in Path(path).read_text(encoding="utf-8").split(";") if part.strip()]
        def run() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                connection.commit()
        await asyncio.to_thread(run)

    def close(self) -> None:
        return None

    def workflow_snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM sul_demo_accounts")
                accounts = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM sul_demo_projects WHERE account_id='account-1'")
                projects = cursor.fetchone()[0]
                cursor.execute("SELECT status FROM sul_demo_tasks WHERE id='task-1' AND project_id='project-1'")
                task = cursor.fetchone()
                cursor.execute("SELECT status FROM sul_demo_subscriptions WHERE id='subscription-1' AND account_id='account-1'")
                subscription = cursor.fetchone()
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM sul_demo_invoices WHERE account_id='account-1' AND operation_id='purchase-1'")
                charges, total = cursor.fetchone()
        return dict(accounts=accounts, projects=projects, task=task[0] if task else None, subscription=subscription[0] if subscription else None, charges=charges, total=int(total))

    def advance_days(self, days: int) -> datetime:
        if days < 0:
            raise ValueError("business time cannot move backwards")
        self.business_time += timedelta(days=days)
        return self.business_time

    def create_account(self, account_id: str, email: str, password: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_accounts(id,email,password,trial_start) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (account_id, email, password, self.business_time))
            connection.commit()

    def account(self, account_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,email,password,trial_start,trial_days,plan,onboarding_step FROM sul_demo_accounts WHERE id=%s", (account_id,))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(account_id)
                return dict(zip(("id", "email", "password", "trial_start", "trial_days", "plan", "onboarding_step"), row))

    def account_exists(self, account_id: str | None = None) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM sul_demo_accounts" + (" WHERE id=%s" if account_id else "") + " LIMIT 1", (account_id,) if account_id else ())
                return cursor.fetchone() is not None

    def trial_active(self, account_id: str) -> bool:
        account = self.account(account_id)
        duration = 5 if self.fault == "trial_expires_day_5" else account["trial_days"]
        return self.business_time < account["trial_start"] + timedelta(days=duration)

    def purchase(self, account_id: str, operation_id: str, amount_cents: int) -> PurchaseResult:
        if amount_cents <= 0:
            raise ValueError("amount must be positive")
        now = self.business_time
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_purchase_operations(operation_id,account_id,amount_cents,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (operation_id, account_id, amount_cents, now))
                count = 2 if self.fault == "duplicate_charge" else 1
                for _ in range(count):
                    cursor.execute("INSERT INTO sul_demo_invoices(id,operation_id,account_id,amount_cents,created_at) VALUES (%s,%s,%s,%s,%s)", (str(uuid4()), operation_id, account_id, amount_cents, now))
            connection.commit()
        return self.ledger_for(operation_id)

    def ledger_for(self, operation_id: str) -> PurchaseResult:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM sul_demo_invoices WHERE operation_id=%s", (operation_id,))
                count, total = cursor.fetchone()
        return PurchaseResult(operation_id, int(count), int(total))

    def set_onboarding_step(self, account_id: str, step: int) -> None:
        if step < 0:
            raise ValueError("step cannot be negative")
        if self.fault == "onboarding_resets" and step > 1:
            step = 1
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE sul_demo_accounts SET onboarding_step=%s WHERE id=%s", (step, account_id))
            connection.commit()

    def transfer_owner(self, account_id: str, old_member: str, new_member: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_memberships(account_id,member_id,role) VALUES (%s,%s,'owner') ON CONFLICT (account_id,member_id) DO UPDATE SET role='owner'", (account_id, old_member))
                cursor.execute("INSERT INTO sul_demo_memberships(account_id,member_id,role) VALUES (%s,%s,'owner') ON CONFLICT (account_id,member_id) DO UPDATE SET role='owner'", (account_id, new_member))
                if self.fault != "owner_transfer_leak":
                    cursor.execute("UPDATE sul_demo_memberships SET role='member' WHERE account_id=%s AND member_id=%s", (account_id, old_member))
            connection.commit()

    def role(self, account_id: str, member_id: str) -> str | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT role FROM sul_demo_memberships WHERE account_id=%s AND member_id=%s", (account_id, member_id))
                row = cursor.fetchone()
        return None if row is None else str(row[0])

    def create_project(self, account_id: str, project_id: str, name: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_projects(id,account_id,name,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (project_id, account_id, name, self.business_time))
            connection.commit()

    def create_task(self, project_id: str, task_id: str, title: str, assignee: str | None = None) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_tasks(id,project_id,title,status,assignee) VALUES (%s,%s,%s,'pending',%s) ON CONFLICT DO NOTHING", (task_id, project_id, title, assignee))
            connection.commit()

    def complete_task(self, task_id: str) -> None:
        status = "pending" if self.fault == "task_completion_stale" else "completed"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE sul_demo_tasks SET status=%s WHERE id=%s", (status, task_id))
            connection.commit()

    def task_status(self, task_id: str) -> str:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM sul_demo_tasks WHERE id=%s", (task_id,))
                row = cursor.fetchone()
        if row is None:
            raise KeyError(task_id)
        return str(row[0])

    def invite(self, account_id: str, invitation_id: str, email: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_invitations(id,account_id,email,status,created_at) VALUES (%s,%s,%s,'pending',%s)", (invitation_id, account_id, email, self.business_time))
            connection.commit()

    def subscribe(self, account_id: str, subscription_id: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO sul_demo_subscriptions(id,account_id,status,started_at) VALUES (%s,%s,'active',%s) ON CONFLICT DO NOTHING", (subscription_id, account_id, self.business_time))
            connection.commit()

    def cancel_subscription(self, subscription_id: str) -> None:
        status = "active" if self.fault == "cancel_ignored" else "cancelled"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE sul_demo_subscriptions SET status=%s WHERE id=%s", (status, subscription_id))
            connection.commit()

    def notifications(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,message,read,created_at FROM sul_demo_notifications WHERE account_id=%s ORDER BY created_at DESC", (account_id,))
                rows = cursor.fetchall()
        return [dict(zip(("id", "message", "read", "created_at"), row)) for row in rows]

    def invoices(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,operation_id,amount_cents,created_at FROM sul_demo_invoices WHERE account_id=%s ORDER BY created_at DESC,id DESC", (account_id,))
                rows = cursor.fetchall()
        return [dict(zip(("id", "operation_id", "amount_cents", "created_at"), row)) for row in rows]

    def memberships(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT member_id,role FROM sul_demo_memberships WHERE account_id=%s ORDER BY member_id", (account_id,))
                rows = cursor.fetchall()
        return [dict(zip(("member_id", "role"), row)) for row in rows]

    def invitations(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,email,status,created_at FROM sul_demo_invitations WHERE account_id=%s ORDER BY created_at DESC", (account_id,))
                rows = cursor.fetchall()
        return [dict(zip(("id", "email", "status", "created_at"), row)) for row in rows]
