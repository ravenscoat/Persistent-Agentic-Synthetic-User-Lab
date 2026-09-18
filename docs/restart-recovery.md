# Durable restart recovery

When SUL_POSTGRES_DSN is set, a workflow persists its run, session,
append-only events, and tool-log memories in the same isolated PostgreSQL
evaluation schema as its business state.

An agent checkpoints the event and the next session state in one database
transaction. A session that was LEASED or RUNNING when its worker stops can
be leased by a new worker after its lease expires. Its persisted step count,
events, and workflow memories are then available to the new process.

Run the proof locally:

\`\`\`powershell
.\.venv\Scripts\python.exe scripts\check_restart_recovery.py
\`\`\`

It creates a fresh schema, writes a checkpoint at step 3 and one workflow
memory, discards the first repository objects, creates new ones, then proves
the new worker reclaims the expired session and reads the original event and
memory.

Validated locally on 2026-09-18:

\`\`\`text
reclaimed session: yes
resumed step count: 3
persisted events: 1
persisted workflow memory: yes
\`\`\`

The real Qwen workflow also completed using PostgreSQL state and memory:
14 actions, 16 model requests, 16 persisted events, 14 persisted tool-log
memories, and all six workflow milestones verified.
