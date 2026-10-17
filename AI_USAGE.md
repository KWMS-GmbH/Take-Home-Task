# AI usage

I used Codex, including parallel review agents, to inspect the assignment, discuss
architecture and edge cases, research protocol/tooling behavior, and implement the
assignment guard plus the first authorization vertical slice (backend, Flask UI,
containers and tests). Codex ran real PostgreSQL/three-replica checks and live browser
QA. Review agents inspected code and architecture; their account limit interrupted
the final pass, so subsequent integration/browser fixes were self-reviewed and
regression-tested. I review and merge the PRs myself. This is an interim account:
the manually merged charging/session slice adds durable state transitions, API-backed
session views and an Authorize-only probe. I also manually merged the scenario slice
as KWMS PR #3; final submission acceptance remains pending.

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

During my UI review I requested varied visible examples. Codex added an explicit
HTTP/OCPP demo command, verified repeatability/non-destructive refusal in integration
tests and inspected all seven actual session pages in the live browser. These are
illustrative data for the existing rules, not a replacement for supplied scenarios.

For the scenario slice, Codex implemented a server-side adapter around the supplied
public replay helpers, bounded database-coordinated runs and separate protocol/API
result checks. All eight original fixture files remain byte-identical. Review caught
whole-second retry-identity collisions, lease expiry without fencing and overlap with
the derived second station before implementation. Integrated review/tests corrected
partial execution marked completed and lost authorization frames during timeout.
The unchanged CLI retains its original clock/coordination behavior; this adaptation
is documented rather than presented as the unmodified entrypoint. Codex ran isolated
PostgreSQL/audit tests, actual three-replica UI smoke and live positive/denied browser QA.

For WP-3a, Codex added deterministic charging/permission/Stop races and a test-only
process-crash harness, without changing runtime application code. Independent review
caught a test-design trap: pausing during response validation would precede insertion
of the replay record, so it would not prove rollback of all three atomic effects.
The implemented pre-commit hook flushes and reads back domain/audit/inbox SQL state;
the post-commit hook pauses before protocol send. Only an owned child against an
explicit test database is killed. Three independent pools/services are not presented
as three separately killed OS processes; the actual deployed three-replica smoke is
separate evidence. I manually reviewed/merged WP-3a as KWMS PR #4; WP-3b and final
acceptance remain pending.
