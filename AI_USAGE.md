# AI usage

I used Codex, including parallel review agents, to inspect the assignment, discuss
architecture and edge cases, research protocol/tooling behavior, and implement the
assignment guard plus the first authorization vertical slice (backend, Flask UI,
containers and tests). Codex ran real PostgreSQL/three-replica checks and live browser
QA. Review agents inspected code and architecture; their account limit interrupted
the final pass, so subsequent integration/browser fixes were self-reviewed and
regression-tested. I review and merge the PRs myself. This is an interim account:
charging/session implementation and final candidate review remain pending.

I corrected Codex's recommendation of one final PR: I will review and manually merge
several reasonably sized vertical-slice PRs. I also required actual three-backend
replica correctness rather than only a written scaling explanation. Protocol source
review corrected an early proposal for permanent station-wide message-ID uniqueness;
OCPP's uniqueness guarantee is scoped to a sender and WebSocket connection.

Verification also corrected concrete AI mistakes: the async SQLAlchemy dependency
was incomplete, Docker secret creation needed explicit stdin, and browser testing
exposed a form reset that obscured which station/tag produced an authorization result.
The original replay tool and fixtures were not changed to make tests pass.
