# Two-persona ownership transfer

Run the scenario with:

```powershell
.venv\Scripts\python.exe scripts\evaluate_ownership_transfer.py
```

The scenario creates separate owner and new-owner application sessions. The
original owner transfers ownership, both personas return, and both call the
protected owner-only endpoint. Their memory contexts are built independently:
the original owner sees only their loss-of-access memory, while the new owner
sees only their new-access memory.

Healthy behavior returns HTTP 403 for the former owner and HTTP 200 for the
new owner. With `owner_transfer_leak`, both return HTTP 200 and the independent
verifier confirms the leak from authoritative membership state.

The same browser-session proof can be run with the seeded leak:

```powershell
.venv\Scripts\python.exe scripts\check_two_persona_browser.py --fault owner_transfer_leak
```

The Playwright proof saves one browser state per persona and verifies both
protected-endpoint responses.

## Concurrent scheduler and Qwen proof

The durable end-to-end proof provisions the transfer as a controlled test
precondition, then runs the two returned personas concurrently through two
scheduler leases, two isolated Playwright contexts, and the local Qwen action
loop. Each persona retrieves only its own verified transfer memory before it
navigates to the protected endpoint. The independent verifier then reads the
authoritative PostgreSQL membership state; it does not trust the model's claim.

Start Ollama with Qwen3-8B and configure `SUL_POSTGRES_DSN`, then run the
seeded permission-leak proof:

```powershell
.venv\Scripts\python.exe scripts\run_concurrent_persona_qwen.py --fault owner_transfer_leak --model-concurrency 1
```

For a healthy comparison, use `--fault none`. The script exits non-zero if
either agent does not complete, event sequences collide, memory crosses persona
boundaries, endpoint behavior is wrong, or the verifier verdict is unexpected.
`--scripted` exists only to validate the harness when Ollama is unavailable and
must not be presented as a Qwen result.

The scheduler leases and browser contexts start concurrently. On one consumer
GPU, keep `--model-concurrency 1`: the shared model gate serializes only the
short generations, avoiding Ollama overload while the durable workers and
browsers remain concurrent. Increase it to `2` only after a real benchmark
shows the hardware can sustain two Qwen generations.
