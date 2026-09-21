from fastapi.testclient import TestClient
import pytest

from synthetic_lab.api import create_app
from synthetic_lab.storage import InMemoryStateRepository
from synthetic_lab.contracts import Event, RunRecord, RunStatus
from datetime import datetime, timezone
import asyncio
import time


@pytest.fixture(autouse=True)
def use_in_memory_storage_for_api_unit_tests(monkeypatch):
    """Keep unit tests independent of a developer's local .env PostgreSQL DSN."""
    monkeypatch.setenv("SUL_POSTGRES_DSN", "")


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


def test_optional_operator_login_protects_dashboard_and_api() -> None:
    from synthetic_lab.config import Settings
    settings = Settings(dashboard_password="operator-test-password", dashboard_session_secret="session-secret-for-tests")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/dashboard", follow_redirects=False).status_code == 303
        assert client.get("/api/runs").status_code == 401
        assert client.get("/reports/guessed-run-id", follow_redirects=False).status_code == 303
        assert client.post("/login", data={"password": "wrong"}, follow_redirects=False).status_code == 303
        assert client.post("/login", data={"password": "operator-test-password"}, follow_redirects=False).headers["location"] == "/dashboard"
        assert client.get("/dashboard").status_code == 200
        assert client.get("/api/runs").status_code == 200
        assert client.post("/logout", follow_redirects=False).headers["location"] == "/login"
        assert client.get("/api/runs").status_code == 401


def test_health_and_metrics_expose_safe_operational_state() -> None:
    client = TestClient(create_app())
    run_id = client.post("/api/runs", json={"scenario_id": "trial_return"}).json()["id"]
    assert client.get("/health").json()["status"] == "ok"
    metrics = client.get("/metrics").text
    assert 'sul_runs_total{status="CREATED"} 1' in metrics
    assert 'sul_storage_backend{backend="in_memory"} 1' in metrics
    run_metrics = client.get(f"/api/runs/{run_id}/metrics").json()
    assert run_metrics["input_tokens"] == run_metrics["output_tokens"] == 0
    assert run_metrics["cost_usd"] is None
    assert run_metrics["queue_time_ms"] is None
    assert run_metrics["execution_state"] == "not_started"


def test_metrics_truthfully_identify_zero_provider_cost_for_local_models() -> None:
    state = InMemoryStateRepository()
    client = TestClient(create_app(state))
    run = client.post("/api/runs", json={"scenario_id": "trial_return"}).json()
    now = datetime.now(timezone.utc)
    asyncio.run(state.append_event(Event(
        id="local-model-action", run_id=run["id"], sequence=0, kind="tool_result",
        wall_time=now, business_time=now,
        payload={"tool_name": "navigate", "status": "success", "model_id": "qwen3:8b", "model_latency_ms": 12, "input_tokens": 10, "output_tokens": 2},
    )))
    metrics = client.get(f"/api/runs/{run['id']}/metrics").json()
    assert metrics["cost_usd"] == 0.0
    assert metrics["cost_source"] == "local_provider_cost_excludes_hardware"
    assert metrics["models"] == ["qwen3:8b"]


def test_dashboard_lists_created_runs() -> None:
    client = TestClient(create_app())
    assert "Know what changed before customers do." in client.get("/dashboard").text
    created = client.post("/api/runs", json={"scenario_id": "payment_retry"})
    run_id = created.json()["id"]
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert "/dashboard/static/lab.js" in dashboard.text
    assert client.get("/dashboard/static/lab.js").status_code == 200
    assert client.get("/dashboard/static/lab.css").status_code == 200


def test_run_summary_aggregates_persona_actions_and_memory_ids() -> None:
    state = InMemoryStateRepository()
    client = TestClient(create_app(state))
    run = client.post("/api/runs", json={"scenario_id": "shared"}).json()
    run_id = run["id"]
    response = client.get(f"/api/runs/{run_id}/summary")
    assert response.status_code == 200
    assert response.json()["event_count"] == 0
    assert response.json()["event_sequences_unique"] is True


def test_dashboard_exposes_scenario_studio_and_validates_authored_runs() -> None:
    client = TestClient(create_app())
    dashboard = client.get("/dashboard")
    assert "Describe the customer journey" in dashboard.text
    assert "REVIEW BEFORE RUNNING" in dashboard.text
    assert "Technical details and raw trace" in dashboard.text


def test_goal_planning_api_returns_reviewable_draft() -> None:
    client = TestClient(create_app())
    response = client.post("/api/test-plans", json={
        "base_url": "https://staging.example.test/signup",
        "goal": "Create an account, create a project, and confirm 'Welcome back'.",
    })
    assert response.status_code == 200
    plan = response.json()
    assert plan["product"]["base_url"] == "https://staging.example.test"
    assert plan["required_clicks"] == ["Create account", "Create project"]
    assert plan["expected_text"] == "Welcome back"
    schema = client.get("/api/product-adapters/schema")
    assert schema.status_code == 200
    payload = {
        "scenario_id": "custom:example",
        "config_snapshot": {"authored_scenario": {
            "product": {"name": "Example", "base_url": "https://example.test", "start_path": "/"},
            "persona": {"kind": "customer", "goal": "Inspect the example product and finish safely."},
            "invariant": {"id": "page_loaded", "kind": "text_contains", "expected": "Example"},
        }},
    }
    created = client.post("/api/runs", json=payload)
    assert created.status_code == 201
    assert created.json()["config_snapshot"]["authored_scenario"]["product"]["base_url"] == "https://example.test"
    payload["config_snapshot"]["authored_scenario"]["product"]["base_url"] = "javascript:alert(1)"
    assert client.post("/api/runs", json=payload).status_code == 422


def test_test_profile_can_be_saved_and_reused() -> None:
    client = TestClient(create_app())
    scenario = {
        "product": {"name": "Example", "base_url": "https://example.test", "start_path": "/"},
        "persona": {"kind": "customer", "goal": "Open the example product and inspect its dashboard."},
        "invariant": {"id": "dashboard", "kind": "text_contains", "expected": "Dashboard"},
        "required_clicks": ["Sign in"],
    }
    profile = client.post("/api/test-profiles", json={"name": "Example smoke test", "scenario": scenario})
    assert profile.status_code == 201
    profile_id = profile.json()["id"]
    assert client.get("/api/test-profiles").json()[0]["name"] == "Example smoke test"
    run = client.post(f"/api/test-profiles/{profile_id}/runs")
    assert run.status_code == 201
    saved = run.json()["config_snapshot"]["authored_scenario"]
    assert saved["invariant"] == scenario["invariant"]
    assert saved["required_clicks"] == ["Sign in"]
    assert saved["product"]["allowed_hosts"] == ["example.test"]


def test_test_profile_preserves_additional_isolated_personas() -> None:
    client = TestClient(create_app())
    scenario = {
        "product": {"name": "Example", "base_url": "https://example.test", "start_path": "/login"},
        "persona": {"kind": "owner", "goal": "Sign in as owner and open the shared project."},
        "invariant": {"id": "owner_home", "kind": "text_contains", "expected": "Owner home"},
        "additional_personas": [{
            "id": "teammate",
            "persona": {"kind": "teammate", "goal": "Sign in as teammate and open the shared project."},
            "invariant": {"id": "team_home", "kind": "text_contains", "expected": "Team home"},
            "required_clicks": ["Projects"],
        }],
    }
    profile = client.post("/api/test-profiles", json={"name": "Owner and teammate", "scenario": scenario})
    assert profile.status_code == 201
    run = client.post(f"/api/test-profiles/{profile.json()['id']}/runs")
    assert run.status_code == 201
    saved = run.json()["config_snapshot"]["authored_scenario"]
    assert saved["additional_personas"][0]["id"] == "teammate"


def test_client_report_exposes_goal_actions_and_replay_without_raw_page_text() -> None:
    client = TestClient(create_app())
    scenario = {
        "product": {"name": "Client app", "base_url": "https://example.test", "start_path": "/"},
        "persona": {"kind": "customer", "goal": "Open the dashboard and verify the welcome message."},
        "invariant": {"id": "welcome", "kind": "text_contains", "expected": "Welcome"},
    }
    run = client.post("/api/runs", json={"scenario_id": "custom:client", "config_snapshot": {"authored_scenario": scenario}}).json()
    report = client.get(f"/api/runs/{run['id']}/client-report")
    assert report.status_code == 200
    assert report.json()["product"]["name"] == "Client app"
    assert report.json()["expected"] == {"text_contains": "Welcome"}
    assert "fresh browser session" in report.json()["replay"]
    page = client.get(f"/reports/{run['id']}")
    assert page.status_code == 200
    assert "SYNTHETIC USER LAB · EVIDENCE REPORT" in page.text


def test_client_report_returns_each_persona_screenshot_link() -> None:
    state = InMemoryStateRepository()
    client = TestClient(create_app(state))
    scenario = {
        "product": {"name": "Client app", "base_url": "https://example.test", "start_path": "/"},
        "persona": {"kind": "owner", "goal": "Open the workspace dashboard as owner."},
        "invariant": {"id": "owner_dashboard", "kind": "text_contains", "expected": "Workspace"},
    }
    run = client.post("/api/runs", json={"scenario_id": "custom:client", "config_snapshot": {"authored_scenario": scenario}}).json()
    now = datetime.now(timezone.utc)
    asyncio.run(state.append_event(Event(
        id="owner-shot", run_id=run["id"], persona_id="owner", session_id="session-owner", sequence=0,
        kind="artifact_captured", wall_time=now, business_time=now,
        payload={"kind": "screenshot", "persona_id": "owner", "url": f"/api/runs/{run['id']}/artifacts/session-owner"},
    )))
    asyncio.run(state.append_event(Event(
        id="teammate-shot", run_id=run["id"], persona_id="teammate", session_id="session-teammate", sequence=1,
        kind="artifact_captured", wall_time=now, business_time=now,
        payload={"kind": "screenshot", "persona_id": "teammate", "url": f"/api/runs/{run['id']}/artifacts/session-teammate"},
    )))
    report = client.get(f"/api/runs/{run['id']}/client-report").json()
    assert [item["persona_id"] for item in report["evidence"]["screenshots"]] == ["owner", "teammate"]
    assert report["evidence"]["final_screenshot"].endswith("/session-owner")


def test_run_listing_and_terminal_transition_guard() -> None:
    client = TestClient(create_app())
    created = client.post("/api/runs", json={"scenario_id": "trial_return"}).json()
    run_id = created["id"]
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 409


def test_start_can_launch_observable_background_executor() -> None:
    async def executor(run) -> None:
        await asyncio.sleep(0.01)

    with TestClient(create_app(executor=executor)) as client:
        run_id = client.post("/api/runs", json={"scenario_id": "trial_return"}).json()["id"]
        response = client.post(f"/api/runs/{run_id}/start")
        assert response.status_code == 200
        assert response.json()["execution"]["state"] in {"queued", "running"}
        for _ in range(20):
            status = client.get(f"/api/runs/{run_id}/execution").json()
            if status["state"] == "completed":
                break
            time.sleep(0.01)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "COMPLETED"
        assert client.get(f"/api/runs/{run_id}/metrics").json()["queue_time_ms"] is not None


def test_startup_rehydrates_running_runs() -> None:
    state = InMemoryStateRepository()
    now = datetime.now(timezone.utc)
    run = RunRecord(id="recovery-run", scenario_id="recovery", created_at=now, business_time=now, status=RunStatus.RUNNING)
    asyncio.run(state.create_run(run))
    called: list[str] = []

    async def executor(recovered) -> None:
        called.append(recovered.id)

    with TestClient(create_app(state, executor=executor)) as client:
        for _ in range(20):
            if client.get(f"/api/runs/{run.id}").json()["status"] == "COMPLETED":
                break
            time.sleep(0.01)
    assert called == [run.id]


def test_evaluation_api_enforces_real_campaign_size() -> None:
    async def executor(run) -> None:
        return None

    with TestClient(create_app(executor=executor)) as client:
        assert client.post("/api/evaluations", json={"scenario_id": "trial_return", "trials": 19}).status_code == 422
        response = client.post("/api/evaluations", json={"scenario_id": "trial_return", "trials": 20, "expect_fault": True})
        assert response.status_code == 202
        evaluation_id = response.json()["id"]
        for _ in range(100):
            result = client.get(f"/api/evaluations/{evaluation_id}").json()
            if result["completed_trials"] == 20:
                break
            time.sleep(0.01)
        assert result["completed_trials"] == 20
        assert result["arms"]["memory_on"]["trials"] == 10
        assert result["arms"]["memory_off"]["trials"] == 10
