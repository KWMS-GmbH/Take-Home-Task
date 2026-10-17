# AI usage

I used Codex, including parallel review agents, to inspect the assignment, discuss
architecture and edge cases, research protocol/tooling behavior, and implement the
assignment guard plus the first authorization vertical slice (backend, Flask UI,
containers and tests). Codex ran real PostgreSQL/three-replica checks and live browser
QA. Review agents inspected code and architecture; their account limit interrupted
the final pass, so subsequent integration/browser fixes were self-reviewed and
regression-tested. I review and merge the PRs myself. This is an interim account:
the current charging/session slice adds durable state transitions, API-backed session
views and an Authorize-only probe; final candidate review remains pending.

I corrected Codex's recommendation of one final PR: I will review and manually merge
several reasonably sized vertical-slice PRs. I also required actual three-backend
replica correctness rather than only a written scaling explanation. Protocol source
review corrected an early proposal for permanent station-wide message-ID uniqueness;
OCPP's uniqueness guarantee is scoped to a sender and WebSocket connection.

Verification also corrected concrete AI mistakes: the async SQLAlchemy dependency
was incomplete, Docker secret creation needed explicit stdin, and browser testing
exposed a form reset that obscured which station/tag produced an authorization result.
The original replay tool and fixtures were not changed to make tests pass.

For the session slice, independent review corrected unsafe draft defaults (a clock
cutoff could split an active long session; an impossible Stop time could leave it
active forever). Code review/tests then caught an empty protocol tag rejected by
the identity helper, Decimal context rounding, late-meter contradictions and completed
summaries changing after late evidence. These were repaired with regression tests.
