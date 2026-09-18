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
protected-endpoint responses. It uses deterministic form actions; the next
extension is running these two browser sessions through the Qwen action loop.
