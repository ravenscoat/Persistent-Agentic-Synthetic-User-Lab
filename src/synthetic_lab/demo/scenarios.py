from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .store import DemoStore


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    satisfied: bool
    expected: object
    actual: object
    reason: str


def trial_return(store: DemoStore, account_id: str) -> InvariantResult:
    store.advance_days(6)
    actual = store.trial_active(account_id)
    return InvariantResult("trial_access_seven_days", actual, True, actual, "trial must remain active on day six")


def payment_retry(store: DemoStore, account_id: str, operation_id: str = "purchase-1") -> InvariantResult:
    store.purchase(account_id, operation_id, 2500)
    actual = store.ledger_for(operation_id).charges
    return InvariantResult("purchase_idempotency", actual == 1, 1, actual, "one operation must produce one ledger charge")


def ownership_transfer(store: DemoStore, account_id: str) -> InvariantResult:
    store.transfer_owner(account_id, "old-owner", "new-owner")
    actual = store.role(account_id, "old-owner")
    return InvariantResult("ownership_transfer", actual == "member", "member", actual, "old owner must lose owner role")


def interrupted_onboarding(store: DemoStore, account_id: str) -> InvariantResult:
    store.set_onboarding_step(account_id, 2)
    actual = store.account(account_id)["onboarding_step"]
    return InvariantResult("onboarding_persistence", actual == 2, 2, actual, "completed onboarding progress must persist")


def stale_task_completion(store: DemoStore, account_id: str) -> InvariantResult:
    store.create_project(account_id, "project-1", "Payments migration")
    store.create_task("project-1", "task-1", "Verify payment retry")
    store.complete_task("task-1")
    actual = store.task_status("task-1")
    return InvariantResult("task_completion", actual == "completed", "completed", actual, "completed task must remain completed")


SCENARIOS: dict[str, Callable[..., InvariantResult]] = {"trial_return": trial_return, "payment_retry": payment_retry, "ownership_transfer": ownership_transfer, "interrupted_onboarding": interrupted_onboarding, "stale_task_status": stale_task_completion}
