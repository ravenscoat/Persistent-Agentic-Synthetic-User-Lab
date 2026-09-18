# Evaluation harness

Run the local deterministic evaluation with:

```powershell
python scripts/evaluate.py --output outputs/evaluation.json
```

The harness runs each of the four demo scenarios twice: once with healthy
state and once with its seeded fault. `DemoVerifier` reads the business state
directly, so the result is independent of what an LLM claims. A healthy case
must be `satisfied`; a fault case must be `confirmed`. The JSON report records
the expected and actual values, detection status, and healthy false positives.

This is a small regression harness, not a production benchmark. It gives the
project a repeatable proof that the seeded examples are observable and that
the verification layer does not report a bug on healthy state.

## Matched memory ablation

Run the two-visit trial-return comparison with:

```powershell
python scripts/evaluate_memory.py --trials 3
```

Each arm receives the same account, business clock, healthy/fault cases, and
independent verifier. The memory-on arm receives a verified expectation written
on the first visit; the memory-off arm does not. A deterministic policy isolates
context retrieval from local-model randomness. The report measures whether the
return visit received the expected memory, whether it raised a correct suspicion
for the early-expiry fault, healthy false positives, context size, and latency.

This proves that memory changes the information available on a later visit. It
is not a Qwen quality benchmark. Verifier confirmation remains separate because
the verifier is independent of agent memory in both arms.

## Local Qwen performance note

On the development machine (RTX 5060 Laptop GPU, 8 GB VRAM), a short Qwen3-8B
Ollama request completed in about 2.7 seconds with thinking disabled and a
16-token output cap. Browser action prompts are larger and can still fail on
malformed or stale identifiers; those failures are action-format reliability
issues, not evidence that the GPU is too slow. Pass `think=True` only for
deliberation-heavy tasks, not routine browser actions.

The first five-run real-model benchmark completed 0/5 sessions. After compact
target aliases were added, Qwen selected the correct controls but repeated
field fills because observations did not expose which inputs already contained
values. Safe input state alone was insufficient: the model also treated filled
fields as completed signup without submitting the form.

The follow-up fix aligns tool schemas with target aliases, puts current page
state after compact action history, explicitly describes form progress, and
bounds repeated-fill and premature-finish corrections. The signup smoke uses
an independent database and dashboard check before accepting completion.

The final five-run local Qwen3-8B benchmark completed **5/5 signups**, with four
browser actions per run and elapsed times of **11.81–12.86 seconds**. Runs used
6–7 model decisions; correction requests remain necessary. The scripted smoke
also passed, and the regression suite passed 49 tests.

Reproduce sequentially (both scripts use local port 8011):

```powershell
python scripts/benchmark_real.py --runs 5 --timeout 90
python scripts/run_e2e.py
```

The benchmark saves sanitized action traces to
`artifacts/real-model-benchmark.json`. Success requires verified signup, not
just the model claiming it finished. This is a small signup-only sample using
in-memory repositories, not a claim of general browser reliability, Oracle
restart recovery, or autonomous bug discovery. The smoke script itself
advances the business clock and invokes the seeded trial-expiry verifier;
the agent does not independently discover that fault. PostgreSQL is the intended
durable backend for the next SaaS simulation milestone; tests continue to use
in-memory repositories for speed.
