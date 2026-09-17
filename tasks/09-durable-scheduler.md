# Task 09 — Scheduler, simulated time, and resume

Depends on: 08. Own: src/synthetic_lab/runtime/scheduler.py, src/synthetic_lab/scenarios/ excluding specs owned by 02, tests/runtime/scheduler/.

Implement four persona definitions, phase-based scenario scheduling, durable session leases, fair scheduling, pause/resume/cancel, bounded inference admission, and explicit business-clock advancement between completed phases. Start with one active inference request. Personas never control the clock.

Persist pending expectations and future wakeups. Recovery reclaims expired leases using wall time, re-observes browser/application state, and avoids blindly repeating writes. Prevent multiple scheduler instances from executing the same leased session.

Acceptance: signup on day zero and revisit on day six resumes the same persona's memory; process interruption preserves pending work; duplicate scheduler polling cannot duplicate a charge action; one slow persona does not permanently starve others; cancelled runs never acquire new sessions; wall-clock deadlines still work when business time jumps. Demonstrate two initial scenarios before adding the remaining two.
