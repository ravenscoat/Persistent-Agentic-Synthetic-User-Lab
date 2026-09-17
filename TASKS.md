# Implementation handoff plan

Give each agent ARCHITECTURE.md, CONTRACTS.md, and its task file. These files describe intended work; none of the implementation is already complete.

## Execution waves

| Wave | Tasks | Gate |
|---|---|---|
| 1 | 01 Foundation | Shared models and protocols import and pass tests |
| 2 | 02 Demo app, 03 Storage, 04 Local model adapter | Can run independently against agreed contracts |
| 3 | 05 Memory, 06 Browser tools, 07 Verification | Memory depends on 03; browser and verification depend on 02 |
| 4 | 08 Runtime | Integrates 03–07 into one-session execution |
| 5 | 09 Scheduler, 10 Reports/replay | 09 depends on 08; 10 depends on 06–08 |
| 6 | 11 API/dashboard | Depends on 09–10 |
| 7 | 12 Evaluation and release | Integrates and validates the complete system |

Tasks in the same wave can run in separate worktrees once dependencies are merged. Assign one task per agent. Do not have multiple agents edit shared contracts simultaneously. The user remains the coordinator; no agents have been launched by this planning work.

## Standard instruction to prepend

Read ARCHITECTURE.md, CONTRACTS.md, your assigned task file, and applicable AGENTS.md instructions. Implement only your assigned task. Inspect existing implementations before coding. Respect file ownership. Do not edit another component to conceal an integration mismatch. Use dependency protocols and deterministic fakes when prerequisites are unavailable, and clearly label unverified integration. Do not install or download models automatically. Never commit credentials or generated browser session data. Run relevant tests. Finish with changed files, checks and results, assumptions, remaining blockers, and any required contract changes. A mock passing is not evidence that the real model or database works.

## Definition of completion

Every task needs working code, relevant behavioral tests, operational notes, and an explicit statement of what was actually exercised. Tests should catch real failures: scope leakage, stale observations, duplicate execution, unsupported model actions, uncertain writes, incorrect verdicts, and failed recovery.

Infrastructure unavailable locally is a documented validation blocker. It is acceptable to deliver tested adapters plus gated integration tests, but not to claim end-to-end completion without the required services.

## Coordination

Each task owns its listed directory and its own tests. Task 01 owns project configuration and shared contracts. Later tasks request dependency changes in their completion notes; coordinator applies them consistently to the lockfile. Agent branches should use codex/task-XX-description when git branches are used.

Review and merge each wave before starting dependent work. The first demo milestone is tasks 01–10 with two scenarios and one persona at a time; the dashboard and remaining persona scenarios come afterward. Four personas sharing one model still constitute independent agent sessions; do not advertise four independent GPU model instances.
