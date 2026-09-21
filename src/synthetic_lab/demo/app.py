from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .postgres_store import PostgresDemoStore
from .store import DemoStore


DEMO_CSS = """
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;color:#1d2939;background:#f6f8fb;font-size:14px}*{box-sizing:border-box}body{margin:0}a{color:#5261d8;text-decoration:none}button,input{font:inherit}button{cursor:pointer}aside{position:fixed;width:228px;height:100vh;background:#fff;border-right:1px solid #e6eaf0;padding:28px 18px}.brand{display:flex;align-items:center;gap:10px;color:#19243a;font-size:17px;font-weight:760}.logo{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:#5b60d8;color:#fff}.nav-label{margin:42px 12px 13px;color:#8490a3;font-size:10px;font-weight:750;letter-spacing:1.5px}.nav{display:block;padding:11px 13px;margin:3px 0;border-radius:8px;color:#68748a;font-weight:620}.nav:hover,.nav.active{background:#eef0ff;color:#515acb}.aside-foot{position:absolute;bottom:27px;padding:0 12px;color:#7e899d;font-size:11px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#48a686;margin-right:7px}main{margin-left:228px;min-height:100vh}header{height:70px;padding:0 38px;background:#ffffffc9;border-bottom:1px solid #e6eaf0;display:flex;justify-content:space-between;align-items:center;color:#7b8699;font-size:12px}.user{display:flex;align-items:center;gap:9px;color:#344056;font-weight:650}.avatar{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#eef0ff;color:#565dcc}.content{max-width:1210px;margin:auto;padding:39px}.eyebrow{color:#7c879b;font-size:10px;font-weight:750;letter-spacing:1.5px;margin:0 0 10px}h1{margin:0 0 8px;font-size:32px;letter-spacing:-1px}h2{margin:0;font-size:16px;letter-spacing:-.3px}h3{margin:0;font-size:13px}.subtitle,.muted{color:#7e899d;line-height:1.6}.card{background:#fff;border:1px solid #e5e9f1;border-radius:14px;box-shadow:0 3px 10px #20305003}.dashboard-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:24px;margin-top:28px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin:20px 0}.stat{padding:17px;border:1px solid #e9edf4;border-radius:10px}.stat small{display:block;color:#8590a3;margin-bottom:7px}.stat strong{font-size:20px;letter-spacing:-.4px}.good{color:#357c5e}.info{color:#5660ca}.action-card{padding:24px}.action-card>p{font-size:12px}.quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:12px}.quick{display:flex;align-items:center;gap:11px;padding:14px;border:1px solid #e4e8f1;border-radius:9px;color:#38445b;font-weight:650}.quick:hover{background:#f7f8ff;border-color:#d7daf7}.symbol{display:grid;place-items:center;width:27px;height:27px;border-radius:8px;background:#eef0ff;color:#5961cd}.section{margin-top:24px}.section-head{display:flex;align-items:center;justify-content:space-between;padding:20px 23px;border-bottom:1px solid #edf0f5}.list{padding:4px 23px 18px}.list-row{display:flex;justify-content:space-between;align-items:center;padding:15px 0;border-bottom:1px solid #f0f2f6}.list-row:last-child{border:0}.badge{font-size:10px;padding:5px 8px;border-radius:5px;font-weight:750;letter-spacing:.4px;background:#edf8f2;color:#37795c}.badge.neutral{background:#eff1f6;color:#718097}.page-body{margin-top:28px;padding:26px}.page-body form{display:grid;gap:13px;max-width:540px;margin:18px 0}.page-body label{font-size:12px;font-weight:650}.page-body input{width:100%;padding:12px;border:1px solid #dce1ec;border-radius:8px}.page-body button,.primary{border:0;background:#5b60d8;color:#fff;border-radius:8px;padding:12px 16px;font-weight:670;box-shadow:0 3px 7px #5b60d821}.page-body ul{padding-left:19px;color:#647087;line-height:2}.back-link{display:inline-block;margin-top:10px;font-size:12px}.form-actions{display:flex;gap:12px;flex-wrap:wrap}.form-actions form{display:inline-block;margin:0}.form-actions input{width:auto;margin-right:7px}.notice{padding:15px 17px;border-radius:9px;background:#edf1ff;color:#505aa8;font-size:12px;line-height:1.55}@media(max-width:850px){aside{position:static;width:100%;height:auto;display:flex;align-items:center;gap:15px}.nav-label,.aside-foot{display:none}.nav{display:inline-block;font-size:12px}main{margin:0}header{padding:0 22px}.content{padding:28px 20px}.dashboard-grid{grid-template-columns:1fr}.stats{grid-template-columns:1fr}}
"""


def _page(title: str, body: str, active: str = "overview") -> str:
    nav = [("overview", "Overview", "/dashboard"), ("projects", "Projects", "/projects"), ("tasks", "Tasks", "/tasks"), ("team", "Team", "/team"), ("billing", "Billing", "/billing"), ("notifications", "Notifications", "/notifications")]
    links = "".join(f'<a class="nav {"active" if key == active else ""}" href="{path}">{label}</a>' for key, label, path in nav)
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Flowboard</title><style>{DEMO_CSS}</style></head><body><aside><div class="brand"><span class="logo">✦</span>Flowboard</div><p class="nav-label">WORKSPACE</p>{links}<div class="aside-foot"><span class="dot"></span> Demo workspace<br><p>Project operations</p></div></aside><main><header><span>Workspace / {title}</span><span class="user"><span class="avatar">A</span>Account owner</span></header><div class="content">{body}</div></main></body></html>'''


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
        return _page("Welcome", '<p class="eyebrow">TEAM WORKSPACE</p><h1>Bring the work into one place.</h1><p class="subtitle">Create a workspace to manage projects, people, and billing.</p><section class="card page-body"><a class="primary" href="/signup">Create account</a></section>')

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form() -> str:
        return _page("Create account", '<p class="eyebrow">GET STARTED</p><h1>Create your workspace</h1><p class="subtitle">You can invite your team after setup.</p><section class="card page-body"><form method="post"><label>Email <input name="email" type="email" required></label><label>Password <input name="password" type="password" required></label><button type="submit">Create account</button></form></section>')

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
        # The person who signs up is the initial owner. A later transfer can
        # promote the separately provisioned new-owner identity.
        response.set_cookie("member_id", "old-owner", httponly=True)
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
            return HTMLResponse(_page("Sign in required", '<p class="eyebrow">ACCESS REQUIRED</p><h1>Start with an account</h1><section class="card page-body"><a class="primary" href="/signup">Create account</a></section>'), status_code=401)
        account = business.account(current_id)
        active = business.trial_active(current_id)
        trial_label = "Active" if active else "Expired"
        trial_text = f"Trial active: {str(active).lower()}"
        body = f'''<p class="eyebrow">WORKSPACE OVERVIEW</p><h1>Good afternoon, Alex.</h1><p class="subtitle">Here is what needs your attention in Flowboard.</p>
        <div class="dashboard-grid"><section class="card action-card"><div class="section-head"><div><h2>Workspace health</h2><p class="muted">Your team workspace at a glance</p></div><span class="badge">{trial_label}</span></div><div class="stats"><div class="stat"><small>TRIAL STATUS</small><strong id="trial-status" class="good">{trial_text}</strong></div><div class="stat"><small>CURRENT PLAN</small><strong id="plan" class="info">Plan: {account["plan"]}</strong></div><div class="stat"><small>SETUP PROGRESS</small><strong id="onboarding-step">Onboarding step: {account["onboarding_step"]}</strong></div></div><div class="notice">Your workspace is ready. Add a project, assign work, or review your billing status.</div></section><section class="card action-card"><h2>Quick actions</h2><p class="muted">Keep momentum with the next useful step.</p><div class="quick-actions"><a class="quick" href="/projects"><span class="symbol">＋</span>New project</a><a class="quick" href="/tasks"><span class="symbol">✓</span>View tasks</a><a class="quick" href="/team"><span class="symbol">♙</span>Invite team</a><a class="quick" href="/billing"><span class="symbol">$</span>Billing</a></div></section></div>
        <section class="card section"><div class="section-head"><div><h2>Workspace actions</h2><p class="muted">Actions used by the demo workflow</p></div><span class="badge neutral">DEMO</span></div><div class="page-body"><div class="form-actions"><form method="post" action="/purchase"><input name="operation_id" value="purchase-1"><input name="amount_cents" type="number" value="2500"><button type="submit">Purchase</button></form><form method="post" action="/onboarding"><input name="step" type="number" value="2"><button type="submit">Save onboarding</button></form><form method="post" action="/transfer"><input name="new_member" value="new-owner"><button type="submit">Transfer ownership</button></form></div></div></section>'''
        return HTMLResponse(_page("Account dashboard", body, "overview"))

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
        return HTMLResponse(_page("Projects", '<p class="eyebrow">PROJECTS</p><h1>Plan the work.</h1><p class="subtitle">Create a project, then add the tasks that move it forward.</p><section class="card page-body"><h2>Create project</h2><form method="post"><input name="name" value="Payments migration" required><button>Create project</button></form><p class="muted">After creating a project, continue to <a href="/tasks">Tasks</a>.</p></section>', "projects"))

    @app.post("/projects")
    async def create_project(request: Request, name: str = Form(...)) -> RedirectResponse:
        business.create_project(account_id(request), "project-1", name)
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/invitations")
    async def invite(request: Request, email: str = Form(...)) -> RedirectResponse:
        business.invite(account_id(request), f"invite-{uuid4().hex[:8]}", email)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/team", response_class=HTMLResponse)
    async def team(request: Request) -> HTMLResponse:
        current_id = account_id(request)
        members = business.memberships(current_id)
        invitations = business.invitations(current_id)
        member_text = "".join(f"<li>{item['member_id']}: {item['role']}</li>" for item in members) or "<li>No members</li>"
        invite_text = "".join(f"<li>{item['email']}: {item['status']}</li>" for item in invitations) or "<li>No invitations</li>"
        body = f"<h2>Members</h2><ul>{member_text}</ul><h2>Invitations</h2><ul>{invite_text}</ul><form method='post' action='/invitations'><input name='email' type='email' required><button>Invite teammate</button></form><a href='/dashboard'>Back</a>"
        return HTMLResponse(_page("Team and permissions", f'<p class="eyebrow">TEAM</p><h1>People and permissions</h1><section class="card page-body">{body}</section>', "team"))

    @app.get("/notifications", response_class=HTMLResponse)
    async def notifications(request: Request) -> HTMLResponse:
        values = business.notifications(account_id(request))
        body = "".join(f"<li>{item['message']}</li>" for item in values) or "<li>No notifications</li>"
        return HTMLResponse(_page("Notifications", f'<p class="eyebrow">NOTIFICATIONS</p><h1>Workspace updates</h1><section class="card page-body"><ul>{body}</ul><a class="back-link" href="/dashboard">Back to overview</a></section>', "notifications"))

    @app.get("/invoices", response_class=HTMLResponse)
    async def invoices(request: Request) -> HTMLResponse:
        values = business.invoices(account_id(request))
        body = "".join(f"<li>{item['operation_id']}: {item['amount_cents']} cents</li>" for item in values) or "<li>No invoices</li>"
        return HTMLResponse(_page("Invoices", f'<p class="eyebrow">BILLING</p><h1>Invoices</h1><section class="card page-body"><ul>{body}</ul><a class="back-link" href="/billing">Back to billing</a></section>', "billing"))

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks(request: Request) -> HTMLResponse:
        account_id(request)
        status = business.workflow_snapshot()['task']
        body = f'<p class="eyebrow">TASKS</p><h1>Keep work moving.</h1><section class="card page-body"><p>Task status: {status or "No task created"}</p>'
        if status is None:
            body += '<form method="post" action="/tasks"><input name="project_id" value="project-1"><label>Task title <input name="title" required></label><button>Create task</button></form>'
        elif status == 'pending':
            body += '<form method="post" action="/tasks/task-1/complete"><button>Complete task-1</button></form>'
        body += '<p><a href="/billing">Billing</a> · <a href="/dashboard">Back to overview</a></p></section>'
        return HTMLResponse(_page("Tasks", body, "tasks"))

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
        state = business.workflow_snapshot()
        # Read the same account-scoped records used by purchase verification.
        invoices = business.invoices(current_id)
        state["charges"] = len(invoices)
        state["total"] = sum(item["amount_cents"] for item in invoices)
        body = f'<p>Subscription: {state["subscription"] or "None"}</p><p>Charges: {state["charges"]}; total cents: {state["total"]}</p>'
        from html import escape
        body += "<ul>" + "".join(
            f"<li>Operation {escape(str(item['operation_id']))}: {item['amount_cents']} cents</li>"
            for item in invoices
        ) + "</ul>"
        if state['subscription'] is None:
            body += '<form method="post" action="/billing/subscribe"><button>Start subscription</button></form>'
        if state['subscription'] == 'active':
            body += '<form method="post" action="/billing/cancel"><input name="subscription_id" value="subscription-1"><button>Cancel subscription</button></form>'
        if state['charges'] == 0:
            body += '<form method="post" action="/purchase"><input name="operation_id" value="purchase-1"><input name="amount_cents" type="number" value="2500"><button>Charge account</button></form>'
        return HTMLResponse(_page('Billing', f'<p class="eyebrow">BILLING</p><h1>Plan and payments</h1><p class="subtitle">Review subscription status and recent charges.</p><section class="card page-body">{body}<a class="back-link" href="/dashboard">Back to overview</a></section>', "billing"))

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
            raise HTTPException(status_code=403, detail="owner access required")
        return {"status": "owner access granted"}

    return app
