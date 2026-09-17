from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .store import DemoStore


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body><main><h1>{title}</h1>{body}</main></body></html>"


def create_demo_app(store: DemoStore | None = None) -> FastAPI:
    business = store or DemoStore()
    app = FastAPI(title="Synthetic Lab Demo Application")
    app.state.store = business

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

    @app.get("/api/owner-only")
    async def owner_only(request: Request) -> dict[str, str]:
        current_id = account_id(request)
        member_id = request.cookies.get("member_id", "")
        if business.role(current_id, member_id) != "owner":
            return {"error": "forbidden"}
        return {"status": "owner access granted"}

    return app
