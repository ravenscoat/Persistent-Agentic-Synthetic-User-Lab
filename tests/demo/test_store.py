from synthetic_lab.demo.scenarios import interrupted_onboarding, ownership_transfer, payment_retry, trial_return
from synthetic_lab.demo.store import DemoStore


def account(store: DemoStore) -> str:
    store.create_account("a1", "demo@example.test", "pass")
    return "a1"


def test_healthy_trial_and_payment() -> None:
    store = DemoStore()
    account_id = account(store)
    assert trial_return(store, account_id).satisfied
    assert payment_retry(store, account_id).satisfied
    store.close()


def test_trial_fault() -> None:
    store = DemoStore(fault="trial_expires_day_5")
    assert not trial_return(store, account(store)).satisfied
    store.close()


def test_duplicate_charge_fault() -> None:
    store = DemoStore(fault="duplicate_charge")
    assert not payment_retry(store, account(store)).satisfied
    store.close()


def test_ownership_and_onboarding_faults() -> None:
    healthy = DemoStore()
    assert ownership_transfer(healthy, account(healthy)).satisfied
    assert interrupted_onboarding(healthy, "a1").satisfied
    healthy.close()
    owner_fault = DemoStore(fault="owner_transfer_leak")
    assert not ownership_transfer(owner_fault, account(owner_fault)).satisfied
    owner_fault.close()
    onboarding_fault = DemoStore(fault="onboarding_resets")
    assert not interrupted_onboarding(onboarding_fault, account(onboarding_fault)).satisfied
    onboarding_fault.close()
