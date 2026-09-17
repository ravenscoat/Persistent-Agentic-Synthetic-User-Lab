from fastapi.testclient import TestClient

from synthetic_lab.demo import DemoStore, create_demo_app


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
