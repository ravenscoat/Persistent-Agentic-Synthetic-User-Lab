# Shared persona run

Run the four healthy personas in one PostgreSQL run with:

```powershell
.venv\Scripts\python.exe -u scripts\run_shared_persona_run.py
```

The run creates one account and four independent sessions: new customer,
power user, administrator, and interrupted user. Each gets its own browser
context and private memory record. The scheduler leases all four sessions,
while the shared Qwen client serializes generations for the single GPU. Events
are allocated from the shared run sequence allocator.

The test harness prepares the business state, then Qwen inspects each persona's
return page. PostgreSQL-backed verifiers independently check trial access,
charge idempotency, ownership transfer, and onboarding persistence. The report
is written to `artifacts/shared-persona-run.json`.
