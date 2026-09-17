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
