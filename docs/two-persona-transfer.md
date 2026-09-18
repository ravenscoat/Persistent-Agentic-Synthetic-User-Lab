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

This is a two-client application-session proof. The next extension is to drive
the two sessions through Playwright and the Qwen action loop.
