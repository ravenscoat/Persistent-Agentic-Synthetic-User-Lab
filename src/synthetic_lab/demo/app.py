from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .postgres_store import PostgresDemoStore
from .store import DemoStore


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body><main><h1>{title}</h1>{body}</main></body></html>"


def create_demo_app(store: DemoStore | PostgresDemoStore | None = None) -> FastAPI:
    # Tests pass an explicit SQLite DemoStore. A configured deployment uses
    # PostgreSQL without changing any route or verifier code.
    dsn = os.getenv("SUL_POSTGRES_DSN")
    business = store or (PostgresDemoStore(dsn, fault=os.getenv("SUL_BUSINESS_FAULT")) if dsn else DemoStore(fault=os.getenv("SUL_BUSINESS_FAULT")))
    app = FastAPI(title="Synthetic Lab Demo Application")
    app.state.store = business

    if isinstance(business, PostgresDemoStore):
        @app.on_event("startup")
        async def migrate_postgres() -> None:
            migration = Path(__file__).resolve().parents[3] / "migrations" / "003_demo_business_postgres.sql"
            await business.apply_migration(str(migration))

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return _page("Team Subscription", '<p>Controlled application for synthetic-user testing.</p><a href="/signup">Create account</a>')

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form() -> str:
        return _page("Create account", '<form method="post"><label>Email <input name="email" type="email" required></label><label>Password <input name="password" type="password" required></label><button type="submit">Create account</button></form>')

    @app.post("/signup")
    async def signup(email: str = Form(...), password: str = Form(...)) -> RedirectResponse:
        account_id = "account-1"
        try:
            business.create_account(account_id, email, password)
        except Exception:
            account_id = f"account-{abs(hash(email)) % 100000}"
            business.create_account(account_id, email, password)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("account_id", account_id, httponly=True)
        response.set_cookie("member_id", "new-owner", httponly=True)
        return response

    def account_id(request: Request) -> str:
        value = request.cookies.get("account_id")
        if not value:
            raise ValueError("not signed in")
        business.account(value)
        return value

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        try:
            current_id = account_id(request)
        except ValueError:
            return HTMLResponse(_page("Sign in required", '<a href="/signup">Create account</a>'), status_code=401)
        account = business.account(current_id)
        active = business.trial_active(current_id)
        body = f'''<p id="trial-status">Trial active: {str(active).lower()}</p>
        <p id="plan">Plan: {account["plan"]}</p>
        <p id="onboarding-step">Onboarding step: {account["onboarding_step"]}</p>
        <form method="post" action="/purchase"><input name="operation_id" value="purchase-1"><input name="amount_cents" type="number" value="2500"><button type="submit">Purchase</button></form>
        <form method="post" action="/onboarding"><input name="step" type="number" value="2"><button type="submit">Save onboarding</button></form>
        <form method="post" action="/transfer"><input name="new_member" value="new-owner"><button type="submit">Transfer ownership</button></form>'''
        body += '<p><a href="/projects">Projects</a> | <a href="/tasks">Tasks</a> | <a href="/billing">Billing</a> | <a href="/notifications">Notifications</a></p>'
        return HTMLResponse(_page("Account dashboard", body))

    @app.post("/purchase")
    async def purchase(request: Request, operation_id: str = Form(...), amount_cents: int = Form(...)) -> RedirectResponse:
        business.purchase(account_id(request), operation_id, amount_cents)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/onboarding")
    async def onboarding(request: Request, step: int = Form(...)) -> RedirectResponse:
        business.set_onboarding_step(account_id(request), step)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/transfer")
    async def transfer(request: Request, new_member: str = Form(...)) -> RedirectResponse:
        business.transfer_owner(account_id(request), request.cookies.get("member_id", "old-owner"), new_member)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/projects", response_class=HTMLResponse)
    async def projects(request: Request) -> HTMLResponse:
        current_id = account_id(request)
        return HTMLResponse(_page("Projects", '<form method="post"><input name="name" value="Payments migration" required><button>Create project</button></form><p>After creating a project, continue to Tasks.</p><a href="/tasks">Tasks</a> | <a href="/dashboard">Back</a>'))

    @app.post("/projects")
    async def create_project(request: Request, name: str = Form(...)) -> RedirectResponse:
        business.create_project(account_id(request), "project-1", name)
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/invitations")
    async def invite(request: Request, email: str = Form(...)) -> RedirectResponse:
        business.invite(account_id(request), f"invite-{uuid4().hex[:8]}", email)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/notifications", response_class=HTMLResponse)
    async def notifications(request: Request) -> HTMLResponse:
        values = business.notifications(account_id(request))
        body = "".join(f"<li>{item['message']}</li>" for item in values) or "<li>No notifications</li>"
        return HTMLResponse(_page("Notifications", f"<ul>{body}</ul><a href='/dashboard'>Back</a>"))

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks(request: Request) -> HTMLResponse:
        account_id(request)
        body = '''<form method="post" action="/tasks"><input name="project_id" value="project-1"><input name="title" placeholder="Task title" required><button>Create task</button></form>
        <form method="post" action="/tasks/task-1/complete"><button>Complete task-1</button></form><a href="/billing">Billing</a> | <a href="/dashboard">Back</a>'''
        return HTMLResponse(_page("Tasks", body))

    @app.post("/tasks")
    async def create_task(request: Request, project_id: str = Form(...), title: str = Form(...)) -> RedirectResponse:
        account_id(request)
        business.create_task(project_id, "task-1", title)
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/tasks/{task_id}/complete")
    async def complete_task(request: Request, task_id: str) -> RedirectResponse:
        account_id(request)
        business.complete_task(task_id)
        return RedirectResponse("/tasks", status_code=303)

    @app.get("/billing", response_class=HTMLResponse)
    async def billing(request: Request) -> HTMLResponse:
        current_id = account_id(request)
        return HTMLResponse(_page("Billing", f'''<form method="post" action="/billing/subscribe"><button>Start subscription</button></form>
        <form method="post" action="/billing/cancel"><input name="subscription_id" value="subscription-1"><button>Cancel subscription</button></form>
        <form method="post" action="/purchase"><input name="operation_id" value="purchase-1"><input name="amount_cents" type="number" value="2500"><button>Charge account</button></form>
        <p>Account: {current_id}</p><a href="/dashboard">Back</a>'''))

    @app.post("/billing/subscribe")
    async def subscribe(request: Request) -> RedirectResponse:
        business.subscribe(account_id(request), "subscription-1")
        return RedirectResponse("/billing", status_code=303)

    @app.post("/billing/cancel")
    async def cancel_billing(request: Request, subscription_id: str = Form(...)) -> RedirectResponse:
        account_id(request)
        business.cancel_subscription(subscription_id)
        return RedirectResponse("/billing", status_code=303)

    @app.get("/api/owner-only")
    async def owner_only(request: Request) -> dict[str, str]:
        current_id = account_id(request)
        member_id = request.cookies.get("member_id", "")
        if business.role(current_id, member_id) != "owner":
            return {"error": "forbidden"}
        return {"status": "owner access granted"}

    return app
