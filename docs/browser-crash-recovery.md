# Browser crash-and-resume proof

Run this with local PostgreSQL configured through `SUL_POSTGRES_DSN`:

```powershell
.venv\Scripts\python.exe scripts\check_browser_resume.py
```

Worker one performs six browser actions, checkpointing every action and writing
tool-log memory. It then stops at a deliberate crash boundary and saves its
Playwright storage state. Worker two is created from fresh PostgreSQL
repositories and a fresh browser. It reclaims the expired lease, restores the
saved login state, navigates to the last URL in durable tool-result events,
re-observes the page, and completes the remaining workflow.

The generated JSON artifact includes interrupted and resumed step counts,
restored URL, persisted event and memory totals, and database-verified
milestones. The default model is deterministic and proves plumbing only. For
the actual local-model run, start Ollama and use:

```powershell
.venv\Scripts\python.exe scripts\check_browser_resume.py --real-model
```
