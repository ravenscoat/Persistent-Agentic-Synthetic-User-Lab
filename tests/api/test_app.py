from fastapi.testclient import TestClient

from synthetic_lab.api import create_app


def test_run_lifecycle_and_event_cursor() -> None:
    client = TestClient(create_app())
    created = client.post("/api/runs", json={"scenario_id": "trial_return"})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert client.post(f"/api/runs/{run_id}/start").json()["status"] == "RUNNING"
    assert client.post(f"/api/runs/{run_id}/pause").json()["status"] == "PAUSED"
    assert client.post(f"/api/runs/{run_id}/resume").json()["status"] == "RUNNING"
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    assert client.get(f"/api/runs/{run_id}/events").json() == []


def test_api_rejects_unknown_fields_and_missing_run() -> None:
    client = TestClient(create_app())
    assert client.post("/api/runs", json={"scenario_id": "x", "unexpected": True}).status_code == 422
    assert client.get("/api/runs/missing").status_code == 404


def test_dashboard_lists_created_runs() -> None:
    client = TestClient(create_app())
    assert "No runs yet" in client.get("/dashboard").text
    created = client.post("/api/runs", json={"scenario_id": "payment_retry"})
    run_id = created.json()["id"]
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert run_id in dashboard.text
    assert "payment_retry" in dashboard.text


def test_run_summary_aggregates_persona_actions_and_memory_ids() -> None:
    state = InMemoryStateRepository()
    client = TestClient(create_app(state))
    run = client.post("/api/runs", json={"scenario_id": "shared"}).json()
    run_id = run["id"]
    response = client.get(f"/api/runs/{run_id}/summary")
    assert response.status_code == 200
    assert response.json()["event_count"] == 0
    assert response.json()["event_sequences_unique"] is True


def test_run_listing_and_terminal_transition_guard() -> None:
    client = TestClient(create_app())
    created = client.post("/api/runs", json={"scenario_id": "trial_return"}).json()
    run_id = created["id"]
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 409
