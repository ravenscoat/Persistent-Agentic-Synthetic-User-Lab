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
