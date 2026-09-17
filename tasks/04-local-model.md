# Task 04 — Shared local model adapter

Depends on: 01. Own: src/synthetic_lab/llm/, tests/llm/, docs/local-model.md.

Implement ModelClient for a configurable Ollama endpoint and installed Qwen3-8B model. Use a single shared client and configurable concurrency semaphore defaulting to one. Validate structured decisions against the contract and implement one bounded repair attempt. Do not assume thinking-mode or structured-output options are identical across runtimes: verify support and report configured capabilities.

Include deadlines, cancellation, bounded output, response usage metadata, latency, unavailable-model errors, and a deterministic scripted fake client. Prefer non-thinking for routine actions; expose optional budgeted thinking only when supported. Never persist private reasoning traces.

Acceptance: fake HTTP responses cover valid decisions, malformed JSON, unknown fields, timeout, missing model, cancellation, and exhaustion of repair allowance; concurrency never exceeds configured capacity. An optional real-model smoke test reports its actual model tag, configuration, output validity, and latency. Do not claim speed or hardware suitability without measurement. Provide manual model setup guidance without downloading automatically.
