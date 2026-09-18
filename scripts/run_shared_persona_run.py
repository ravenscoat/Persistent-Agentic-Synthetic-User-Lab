"""Run four isolated healthy personas in one PostgreSQL run."""
from __future__ import annotations
import asyncio, json, os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import uvicorn
from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import BudgetConfig, MemoryRecord, MemoryType, PersonaRecord, RunRecord, SessionRecord, Trust
from synthetic_lab.demo import create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.evaluation.campaign import PERSONA_SCENARIOS
from synthetic_lab.llm import build_local_model
from synthetic_lab.memory import with_optional_qdrant
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.scheduler import DurableScheduler
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.verification import DemoVerificationContext, DemoVerifier

class SharedRunContext(MemoryContextAssembler):
    def __init__(self, repository, route, tool_registry):
        super().__init__(repository, memory_limit=1, tool_registry=tool_registry); self.route = route
    async def build(self, persona, session, observation, budgets):
        arrived = observation.url.rstrip('/').endswith(self.route)
        guidance = f'The current URL is already {self.route}; finish with a summary.' if arrived else f'Navigate exactly once to {self.route}, then finish. Only navigate is allowed.'
        return await super().build(persona.model_copy(update={'goal': persona.goal + ' ' + guidance}), session, observation, budgets)

async def main() -> int:
    dsn = os.getenv('SUL_POSTGRES_DSN')
    if not dsn: raise RuntimeError('SUL_POSTGRES_DSN is required')
    root, schema, run_id = Path(__file__).resolve().parents[1], f'sul_eval_{uuid4().hex}', str(uuid4())
    now, account_id, origin = datetime.now(timezone.utc), 'account-1', 'http://127.0.0.1:8016'
    store = PostgresDemoStore(dsn, schema=schema); state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    settings = Settings().model_copy(update={'model_concurrency': 1}); memory = with_optional_qdrant(PostgresMemoryRepository.from_dsn(dsn, schema=schema), settings)
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host='127.0.0.1', port=8016, log_level='error')); task = model = None; browsers = {}
    try:
        await store.apply_migration(root / 'migrations' / '003_demo_business_postgres.sql'); await state.apply_migration(root / 'migrations' / '002_initial_postgres.sql')
        await state.create_run(RunRecord(id=run_id, scenario_id='shared_persona_healthy', created_at=now, business_time=now, model_metadata={'mode':'qwen','personas':4}))
        store.create_account(account_id, 'shared@example.test', 'not-a-real-password'); store.advance_days(6); store.purchase(account_id, 'purchase-1', 2500); store.transfer_owner(account_id, 'old-owner', 'new-owner'); store.set_onboarding_step(account_id, 2)
        task = asyncio.create_task(server.serve()); await asyncio.sleep(.25)
        personas, sessions, routes = {}, {}, {}
        for spec in PERSONA_SCENARIOS:
            pid = 'old-owner' if spec.scenario_id == 'ownership_transfer' else spec.persona_kind
            personas[pid] = PersonaRecord(id=pid, run_id=run_id, kind=spec.persona_kind, application_account_id=account_id, goal=spec.goal, allowed_tool_names=['navigate']); sessions[pid] = SessionRecord(id=f'{pid}-shared', run_id=run_id, persona_id=pid, phase='shared_return', due_business_time=now); routes[pid] = spec.return_route
            await state.enqueue_session(sessions[pid]); await memory.append(MemoryRecord(id=f'{pid}-private', run_id=run_id, persona_id=pid, type=MemoryType.ENTITY, text=f'Private verified memory for {pid}.', trust=Trust.VERIFIED, valid_from=now))
        for pid in personas:
            browser = PlaywrightBrowserSession(run_id, sessions[pid].id, origin, artifact_root=root / 'artifacts'); await browser.start(); await browser.page.context.add_cookies([{'name':'account_id','value':account_id,'url':origin,'httpOnly':True},{'name':'member_id','value':pid,'url':origin,'httpOnly':True}]); await browser.page.goto(origin + routes[pid]); browsers[pid] = browser
        tools = BrowserToolRegistry(browsers); observations = {pid: await browser.observe() for pid, browser in browsers.items()}; model = build_local_model(settings); results = {}
        def factory(leased):
            pid = leased.persona_id; browser = browsers[pid]
            async def execute(current):
                agent = PersonaAgent(model=model, context=SharedRunContext(memory, routes[pid], tools), tools=tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=3, max_model_requests=4, output_tokens=256), completion_check=lambda: asyncio.sleep(0, result=browser.page.url.rstrip('/').endswith(routes[pid])))
                results[pid] = await agent.run(personas[pid], current, observations[pid])
            return execute
        await asyncio.gather(*(DurableScheduler(state, factory).run_once(f'shared-worker-{pid}', now=now) for pid in personas)); events = await state.list_events(run_id, limit=500)
        verification = {}
        for invariant, operation in [('trial_access_seven_days',None),('purchase_idempotency','purchase-1'),('ownership_transfer',None),('onboarding_persistence',None)]: verification[invariant] = (await DemoVerifier().check(invariant, DemoVerificationContext(store, account_id, operation))).model_dump(mode='json')
        retrieved = {pid: sorted({mid for event in events if event.persona_id == pid for mid in event.payload.get('retrieved_memory_ids',[])}) for pid in personas}; private_ok = all(f'{pid}-private' in retrieved[pid] and all(f'{other}-private' not in retrieved[pid] for other in personas if other != pid) for pid in personas)
        payload = {'run_id':run_id,'schema':schema,'model':'qwen','personas':list(personas),'results':{pid:r.__dict__ for pid,r in results.items()},'event_count':len(events),'event_sequences_unique':len({e.sequence for e in events})==len(events),'retrieved_memory_ids':retrieved,'memory_isolated':private_ok,'verification':verification}; path=root/'artifacts'/'shared-persona-run.json'; path.write_text(json.dumps(payload,indent=2,default=str),encoding='utf-8'); print(json.dumps(payload,indent=2,default=str)); passed=len(results)==4 and all(r.status=='completed' for r in results.values()) and payload['event_sequences_unique'] and private_ok and all(v['verdict']=='satisfied' for v in verification.values()); return 0 if passed else 1
    finally:
        for browser in browsers.values(): await browser.close()
        if model and hasattr(model,'aclose'): await model.aclose()
        server.should_exit=True
        if task: await task
        store.close()

if __name__ == '__main__': raise SystemExit(asyncio.run(main()))
