# Task 08 — Bounded persona execution loop

Depends on: 03,04,05,06,07. Own: src/synthetic_lab/runtime/agent.py and related execution modules, tests/runtime/agent/.

Implement one-session execution: observe, assemble context, request decision, validate, dispatch, record, update memory, repeat. Connect search_memory and report_suspicion to bounded platform services. Model finish does not automatically imply task success; record verifier results separately.

Enforce all run/session budgets including retries and elapsed time. Preserve action IDs and checkpoints. Unknown write outcomes require reconciliation before retry. Cancellation finishes recording the current known outcome and releases resources.

Acceptance: scripted model completes a normal scenario; invalid output gets at most one repair; repeated bad actions stop at budget; tool timeout and unavailable model cause clear paused/failed outcomes; memory queries respect scope; restart metadata contains enough information to reconcile uncertain actions. Provide an optional single-persona real-Qwen browser smoke run with actual results, not assumed success.
