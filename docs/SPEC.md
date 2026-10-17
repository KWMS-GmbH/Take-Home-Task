# Charging Session Service — living specification

Status: foundation approved by the candidate (`lgtm`), 2026-10-17. No application behavior is implemented yet.
The high-level decisions below were agreed in discussion; proposed mechanics and
open questions remain visibly marked. Original-source coverage lives in
[REQUIREMENTS.md](REQUIREMENTS.md), execution status in [WORK.md](WORK.md), and
review conventions in [WORKFLOW.md](WORKFLOW.md).

## Purpose and boundaries

Allow operators to manage station-specific RFID permissions and inspect trustworthy
charging sessions reconstructed from unreliable OCPP 1.6 messages. Provide a browser
simulation that exposes protocol results and the actual session records from the API.

Deploy exactly three service definitions: Flask `frontend`, asynchronous Python
`backend` (HTTP API and OCPP WebSockets), and PostgreSQL `db`. The frontend never
accesses PostgreSQL. Support three concurrent backend replicas (`U-SCALE`). Keep
protocol, application rules, and persistence as backend modules.

Scope is the six actions and simplified fields in the assignment. There is no
tariff/invoicing engine, frontend login, extra OCPP coverage, autoscaling system,
additional deployed service, or central observability stack in the agreed scope.
Production gaps must be documented rather than represented as solved.

## Protocol contract

Accept `/ocpp/<charge_point_id>` on port 9000 with `ocpp1.6`. Validate framing and
the relevant fields; ignore unlisted fields, including nested extras. A bad message
must not compromise subsequent valid messages or other stations.

| Action | Relevant input | Result payload |
| --- | --- | --- |
| `BootNotification` | `chargePointVendor`, `chargePointModel` | `status: Accepted`, `currentTime: ISO-8601 UTC`, `interval: 300` |
| `Authorize` | `idTag` | `idTagInfo.status` from current station permission |
| `StatusNotification` | `connectorId`, `status`, `errorCode`, `timestamp` | `{}` |
| `StartTransaction` | `connectorId`, `idTag`, `meterStart` Wh, `timestamp` | integer `transactionId` and `idTagInfo.status`, including denied attempts |
| `MeterValues` | `connectorId`, `transactionId`, `meterValue[].timestamp`, `meterValue[].sampledValue[].value` string Wh | `{}` |
| `StopTransaction` | `transactionId`, `meterStop` Wh, `timestamp`, optional `reason` | `idTagInfo.status: Accepted` |

The result payload is enclosed in the correct OCPP response frame, echoing the
message's `uniqueId`. Error framing and codes must be tested, not inferred from
the supplied runner's warning/failure count alone.

## Accepted decisions and rationale

| ID | Agreed behavior and rationale |
| --- | --- |
| D-01 | Use asynchronous FastAPI/Starlette for the backend, Flask for the UI, and SQLAlchemy with PostgreSQL migrations. Keep presentation, domain rules, and transport separate without extra services. Exact dependency versions await compatibility checks. |
| D-02 | Evaluate the Python OCPP library first, with a small input adapter. Filter unlisted raw fields before name conversion; validate required fields and outputs. Prefer a narrow implementation if a maintainable adapter cannot satisfy the assignment. |
| D-03 | Use the server-generated integer OCPP `transactionId` as the session's database/API identifier. No additional public session ID. Validate that transaction references belong to the connected station. |
| D-04 | Keep lifecycle (`active`, `completed`, `interrupted`), authorization-at-start (`Accepted`, `Blocked`, `Invalid`), and meter quality (`valid`, `unknown`, `suspicious`) separate. Any derived billing-eligibility indication is not a billing system and must expose its prerequisites. |
| D-05 | Unknown tag is `Invalid`; known tag without enabled station permission is `Blocked`; otherwise `Accepted`. Fresh Authorize and Start requests read current permission. Preserve the Start decision on the session. |
| D-06 | A denied Start still allocates a transaction ID and retains the physical charging attempt. Display its authorization outcome clearly and exclude it from normal billing eligibility. |
| D-07 | A new station Boot interrupts its open sessions. Preserve the last trustworthy meter evidence and uncertainty; do not invent the actual stop time. A later valid Stop corrects that same interrupted session. A backend crash or mere disconnect does not itself prove charging ended. |
| D-08 | Use timezone-aware timestamps normalized to UTC. Compute completed energy as `(meterStop - meterStart) / 1000`. Backwards meters yield unknown energy (`null`) plus a review indication, never a negative amount or a fabricated zero. |
| D-09 | Permit concurrent sessions on different connectors; enforce at most one active session per station/connector in PostgreSQL. A newer accepted station connection supersedes the old connection. Database ownership checks protect mutations across replicas. |
| D-10 | A charging session has no backend-process owner. Only its live socket does. After a process dies, the device reconnects to any replica and processing resumes from durable data; live sockets cannot migrate between processes. |
| D-11 | Make domain changes and their business audit events atomic in one short transaction. For request types designated replayable by Q-01, persist the intended response in that transaction too; validate it before commit and send it afterward. Database commit and network delivery cannot be atomic. Release database resources before network waits. Bound pools and timeouts across all replicas. |
| D-12 | Message `uniqueId` uniqueness is per sender/connection, not eternal station-wide uniqueness. Treat replay and ID reuse separately. Session completion and its energy effect cannot be applied twice. A repeated Stop preserves its first established result and records suspicious mismatches. Detailed retry rules are Q-01. |
| D-13 | Permission revocation affects future fresh Authorize/Start calls. It does not retroactively change a session's original authorization or issue a remote stop. Explain the production policy/remote-control work that would be needed. |
| D-14 | Store business audit history in PostgreSQL and emit bounded structured operational logs to stdout/stderr. Correlate station, message, transaction, connection, and instance where applicable. Do not claim an authenticated operator identity when login is absent. |
| D-15 | Use versioned database migrations, serialized across replica startup. Fix the external protocol at `ocpp1.6`. Multiple internal HTTP API versions and full zero-downtime migration machinery are deferred; document compatibility during rolling upgrades. |
| D-16 | Publish frontend HTTP and required OCPP ingress; keep database and management API internal. Use separate ingress/listener boundaries so publishing the WebSocket port cannot expose management routes accidentally. |
| D-17 | Execute each supplied scenario and obtain its actual transaction IDs from the recorded responses, then fetch those sessions through the API. An application adapter may interpret results; the original replay tool and scenarios remain unchanged. |

These are derived choices, not claims that KWMS specified every mechanism.

## Operator experience

List connected/manually created stations and tags; create stations and tags and
toggle station/tag permissions. Choose a station, candidate tag, and separate
not-permitted tag for simulation. Show initial authorization and run scenarios
only if Accepted. For each scenario show protocol pass/fail, collapsible exchanged
messages, the supplied check lines, and its sessions with status, start, stop,
duration, and energy. Provide a station-filtered sessions list. Unknown/provisional
values must be visibly distinct from zero or confirmed facts.

## Proposed mechanics — review within the owning work package

- PostgreSQL connection generations/fencing, row locks and a partial unique index
  enforce ownership and connector invariants. Define takeover at a validated
  connection boundary without adding a mandatory Boot-first sequence to the brief.
  Optional PostgreSQL notifications can prompt socket closure; fencing is authoritative.
- One application worker per backend container; small configurable pools with
  bounded acquisition/lock/statement waits. Count migration/notification connections
  when choosing the total database connection budget.
- Docker captures structured output with rotation; a repository command gathers
  retained service logs. Diagnostic logs are best-effort, whereas committed audit
  events survive application crashes. Do not promise host-loss durability.
- Validate and bound messages before expensive work; apply connection/message and
  simulation-run limits. Operational log flooding must not exhaust storage. The
  exact limits are to be measured and documented, not invented as safety guarantees.

## Open questions — close before dependent implementation

| ID | Decision needed | Owner |
| --- | --- | --- |
| Q-01 | Define same-connection replay/conflict behavior and bounded cross-connection retry recognition, particularly Start. New connections may legally reuse IDs. Do not cache fresh authorization or Boot responses on an eternal station/ID key. | WP-2, WP-3 |
| Q-02 | Prove library adapter behavior for arbitrary extra fields, raw-name collisions (`callUniqueId`/`call_unique_id`, `connectorId`/`connector_id`), malformed input, and output validation. Pin or reject the evaluated library based on evidence. | WP-1 |
| Q-03 | Decide response to a fresh Start on an already-active connector, out-of-order meter samples, invalid time ordering, and unknown transaction references. Define how conflicting stops for an already-completed session are reported/audited while preserving D-12's first established result. | WP-2 |
| Q-04 | Fix ownership-promotion and stale-socket behavior, migration execution, pool budget, timeouts, and load limits; prove the three-replica behavior. | WP-3 |
| Q-05 | Define audit-event fields/retention, diagnostic redaction and rotation, and reliable scenario result extraction, without logging unbounded attacker-controlled data. | WP-2, WP-3 |
| Q-06 | Resolve access/final submission to the KWMS repository. The candidate has separately authorized routine project PR publication to the public fork; that workflow is not blocked on KWMS access. | Candidate |

## Source findings and spec change history

- 2026-10-17: Initial discussion accepted the decisions above and added actual
  three-replica support beyond the original scaling-explanation requirement.
- 2026-10-17: Candidate approved the foundation spec and authorized fork publication.
  Codex owns proactive verified commit/push/PR handoff; candidate manual merge remains
  the gate. This changes workflow ownership, not application behavior.
- Source review corrected an initial proposal for permanent `(station, uniqueId)`
  deduplication. OCPP-J 1.6 §4.1.4 only guarantees sender/connection uniqueness.
- Supplied scenario 02 recomputes `$ts(-5m)` for each Stop. Its two payloads can
  differ by a second despite intending a duplicate; preserve the original fixture
  and assert transaction-level idempotency instead of assuming identical bytes.
- Future changes must state affected requirement IDs, old/new behavior, rationale,
  candidate agreement for material choices, and changed verification evidence.

References: [OCPP-J specification, §4.1.4](https://github.com/mobilityhouse/ocpp/blob/master/docs/v16/ocpp-j-1.6-specification.pdf),
[evaluated library dispatch](https://github.com/mobilityhouse/ocpp/blob/2.1.0/ocpp/charge_point.py),
[original scenario 02](../take-home/scenarios/02_duplicate_stop.json).
