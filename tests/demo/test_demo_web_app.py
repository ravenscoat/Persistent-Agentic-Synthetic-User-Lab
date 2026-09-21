from fastapi.testclient import TestClient

from synthetic_lab.demo import DemoStore, create_demo_app


def test_billing_matches_verifier_for_dynamic_account_and_excludes_other_accounts():
    store = DemoStore(fault="duplicate_charge")
    try:
        store.create_account("dynamic-account", "dynamic@test.invalid", "test")
        store.create_account("other-account", "other@test.invalid", "test")
        store.purchase("dynamic-account", "dynamic-operation", 2500)
        store.purchase("other-account", "other-operation", 9000)
        with TestClient(create_demo_app(store)) as client:
            client.cookies.set("account_id", "dynamic-account")
            page = client.get("/billing").text
        result = store.ledger_for("dynamic-operation")
        assert result.charges == 2
        assert "Charges: 2; total cents: 5000" in page
        assert page.count("Operation dynamic-operation:") == 2
        assert "other-operation" not in page
        assert "Charge account" not in page
    finally:
        store.close()


def test_demo_app_creates_account_and_preserves_workflow_state() -> None:
    store = DemoStore()
    client = TestClient(create_demo_app(store))
    response = client.post("/signup", data={"email": "user@example.test", "password": "pass"})
    assert response.status_code == 200
    assert "Trial active: true" in response.text
    client.post("/onboarding", data={"step": 2})
    assert "Onboarding step: 2" in client.get("/dashboard").text
    store.close()


def test_demo_app_purchase_endpoint_uses_operation_id() -> None:
    store = DemoStore(fault="duplicate_charge")
    client = TestClient(create_demo_app(store))
    client.post("/signup", data={"email": "buyer@example.test", "password": "pass"})
    client.post("/purchase", data={"operation_id": "op-1", "amount_cents": 100})
    assert store.ledger_for("op-1").charges == 2
    store.close()


def test_workflow_pages_show_real_state_and_available_actions():
    store = DemoStore()
    try:
        client = TestClient(create_demo_app(store))
        client.post('/signup', data={'email': 'workflow@example.test', 'password': 'test'})
        client.post('/projects', data={'name': 'Payments migration'})
        assert 'Complete task-1' not in client.get('/tasks').text
        client.post('/tasks', data={'project_id': 'project-1', 'title': 'Verify payment retry'})
        page = client.get('/tasks').text
        assert 'Complete task-1' in page and 'Create task' not in page
        client.post('/tasks/task-1/complete')
        assert 'Task status: completed' in client.get('/tasks').text
        client.post('/billing/subscribe')
        assert 'Start subscription' not in client.get('/billing').text
        client.post('/purchase', data={'operation_id': 'purchase-1', 'amount_cents': 2500})
        assert 'Charge account' not in client.get('/billing').text
        client.post('/billing/cancel', data={'subscription_id': 'subscription-1'})
        assert 'Subscription: cancelled' in client.get('/billing').text
    finally:
        store.close()
