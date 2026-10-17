# Charging Session Service — living specification

Status: foundation/WP-1a imported with full history into canonical KWMS main;
WP-1b policies, concrete defaults and implementation plan approved, 2026-10-17.
Implementation/verification status is separate; this spec alone is not evidence of a pass.
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
| D-18 | Split WP-1 into independently reviewed PRs: WP-1a station/tag/permission management plus Boot/Authorize, then WP-1b charging/session happy path. WP-1b begins after WP-1a's manual merge. |
| D-19 | A fresh deployment creates an empty database; redeployment preserves existing data. Demo data is an explicit idempotent API operation; normal teardown preserves the volume. Destructive reset is separate and never implicit. |
| D-20 | One asynchronous backend process hosts internal HTTP on port 8000 and OCPP-only WebSockets on port 9000, sharing the application service/database pool. |
| D-21 | Declare three backend replicas from WP-1a. Build fencing, bounded pools, atomic audit, database constraints and serialized migrations now; WP-3 extends failure proof and tuning. U-SCALE remains pending until its acceptance tests pass. |
| D-22 | Raw accepted connect registers the station and allocates a pending generation. Its first valid supported OCPP call promotes ownership; no Boot-first requirement is added and garbage alone cannot evict an owner. Every mutation checks database ownership. |
| D-23 | Candidate approved an Authorize-only adapter for the UI authorization check on 2026-10-17. Replace only the UI's use of the supplied Boot+Authorize convenience helper; keep the original helper/scenarios byte-for-byte and keep deliberate scenario Boots meaningful. This avoids an incidental reboot/session interruption, but a valid Authorize still promotes its connection under D-22. Full rationale, caveat and required verification follow below. |
| D-24 | Candidate approved cross-connection Start retry recognition using station, connector, tag, equivalent UTC start time and starting meter within an explicit retry window. Recognized retries return the original transaction ID and original authorization snapshot, not a new session or a fresh permission decision. A fresh Start still uses current permission. Never use an eternal station/message-ID key. |
| D-25 | Candidate approved treating a different, non-retry Start on an occupied connector as a new physical attempt: mark the previous session interrupted without inventing its stop time, create the new active attempt and return its new transaction ID/current authorization. Flag the conflict for review and allow a later Stop to correct the previous session. This preserves one active session per connector and the brief's mandatory transaction-ID response. |
| D-26 | Candidate approved rejecting unknown/wrong-station transaction references without session mutation, while retaining otherwise valid but suspicious time/meter evidence with review indications. Do not fabricate values, destructively replace trustworthy summaries or overwrite an already established completion. Preserve atomic audit and first-established Stop semantics. |
| D-27 | Candidate approved the concrete WP-1b contract/plan: exact active-session Start retries never expire; interrupted/completed matches have a configurable default 24-hour grace from server receipt of the lifecycle transition. Stop-before-Start is acknowledged/evidenced and changes active to interrupted, without a trusted stop/duration/final energy; later admissible Stop corrects that same session. |

These are derived choices, not claims that KWMS specified every mechanism.

## Operator experience

The browser is the operator interface, not the OCPP client. Simulation executes
server-side in the Flask frontend container; the Python replay helper opens real
WebSockets to the backend. Stations and permissions are durable database records,
not browser-resident simulated devices. No physical charging hardware is included.

List connected/manually created stations and tags; create stations and tags and
toggle station/tag permissions. Choose a station, candidate tag, and separate
not-permitted tag for simulation. Show initial authorization and run scenarios
only if Accepted. For each scenario show protocol pass/fail, collapsible exchanged
messages, the supplied check lines, and its sessions with status, start, stop,
duration, and energy. Provide a station-filtered sessions list. Unknown/provisional
values must be visibly distinct from zero or confirmed facts.

### D-23 — Authorization-only probe: approved rationale and caveat

Status: implemented and locally verified in WP-1b; candidate PR review/merge remains
the gate. This changes the WP-1a UI probe mechanic, not the original assignment or
the backend's Boot behavior. Actual evidence is tracked in WORK.md/REQUIREMENTS.md.

**Why change it:** `take-home/replay.py` lines 296–321 define `authorize()` as a
real station connection that sends Boot and then Authorize. Under D-07, a fresh Boot
with open sessions marks those sessions interrupted, preserving evidence without
inventing a stop. A button intended to check permission must not accidentally declare
a simulated reboot. This is a record/lifecycle effect, not a remote-stop command.

**Accepted behavior:** the UI opens a bounded real OCPP connection and sends only
Authorize, displays its actual result/exchanges, and runs scenarios only if Accepted.
It does not substitute a database/API permission lookup for the required protocol
check. Original scenario execution and its explicit Boots remain unchanged. Those
Boots can still intentionally interrupt open sessions, and later valid Stops must
correct the same durable sessions. The supplied command-line helper is unchanged,
so its preflight continues to send Boot; document that distinction in the delivered
README. Replacing only our UI helper use does not remove any required message handler.

**Compatibility with the original task:** BRIEF lines 89–97 require an Authorize
check before scenario execution, not a preceding Boot. Line 179 permits wrapping,
changing or replacing the provided tooling with an explanation. Lines 133–137
require a sensible reboot/missing-stop and late-stop policy; D-07 remains in force.
The Authorize-only adapter therefore satisfies the stated UI workflow without
changing any original requirement or weakening the real scenario tests.

**Connection caveat:** a new socket alone is pending, but its first valid supported
call, including Authorize whether Accepted, Blocked or Invalid, promotes its generation.
Thus the probe can fence an existing socket using the same station ID, including one
from another simulation run. Its next valid supported request is rejected and the
old socket closes; closing the probe does not restore the old generation, so that
client must reconnect. This ownership change must not, by itself, mark durable
charging sessions interrupted or completed. A subsequent connection from the same
station can continue them; session records belong to the station, not a particular
socket or backend process. This is separate from generation ownership of the live socket.

This is not a passive monitoring facility, station authentication or safe parallel
simulation under one shared station identity. Explain the caveat in the UI/help and
README; use distinct station IDs for independent runs. Do not silently add a
Boot-first requirement, reject all second connections, or exempt required edge cases.
The brief explicitly requires same-station overlapping connections (lines 142–143).
Unchanged scenario 07 starts a transaction on socket a, Boots on socket b and Stops
the same transaction there: the final result must be one completed 3.0 kWh session.
Boot may first mark it interrupted; that later Stop must correct it, not create a
second session. Passing this remains mandatory even if ordinary UI runs are serial.

**Required verification, not evidence of a pass:**

- The actual UI probe sends no Boot and returns real Accepted/Blocked/Invalid results;
  denied or failed authorization cannot start scenario execution.
- With an active durable session, Authorize-only takeover changes connection ownership
  but not session lifecycle, authorization-at-start, meter evidence or transaction ID;
  stale-socket mutations remain fenced and another valid connection can continue it.
- A deliberate fresh Boot still interrupts open sessions, duplicate handling remains
  governed by D-12, and a valid late Stop completes/corrects the same session.
- Unchanged scenario 07 proves its API/DB postconditions, not merely protocol success.
- Assignment integrity remains clean; warnings explain the remaining shared-ID side effect.

## Implementation boundaries and mechanics

### WP-1b approved implementation contract

D-23–D-27 and the persisted implementation plan are candidate-approved. Approval
authorizes implementation, not a claim that the behavior is implemented or verified.

**Session records and boundaries**

- Use the database integer primary key as the OCPP/API transaction ID. Store station,
  connector, supplied tag text (including unknown tags), authorization-at-start,
  lifecycle, reported event times, start/stop meters and explicit review reasons.
  Unknown denied tags cannot require an existing tag-row foreign key.
- Keep reported/received times distinct. Require timezone-aware, parseable timestamps
  and normalize equivalent offsets to UTC; do not substitute server receipt time for
  a station's event time. Never expose negative duration or energy as a valid result.
- Keep relevant meter/status evidence and session audit in PostgreSQL. StatusNotification
  is connector telemetry, not authority to start/complete a charging transaction.
  Start opens a physical attempt, a new Boot interrupts it, and an admissible Stop
  completes/corrects it. Denied attempts remain open until Stop, Boot or superseding
  Start, but authorization remains non-Accepted regardless of observed energy.
- Expose list/detail HTTP resources and station-filtered Flask session views, including
  lifecycle, authorization, quality/review reasons, start/stop, duration and energy.
  Completed energy uses trustworthy start/stop endpoints; active/interrupted energy
  is either explicitly provisional from trustworthy readings or unknown, never a
  fabricated final total. Billing eligibility is only a derived indication with its
  prerequisites visible, not invoicing or authorization to charge a customer.

**Retry and concurrency defaults**

- Start retry boundary: always recognize an exact match to the currently
  active session, regardless of its age. A strict clock cutoff must not make a long
  charge interrupt itself and create a second transaction on retry. For interrupted
  or completed sessions, use a configurable default 24-hour grace period from server
  receipt of that lifecycle transition, not the historical station event time.
  Completion of an interrupted session starts its completion grace; repeated messages
  alone never extend it. Normalize timestamps before matching the relevant fields
  from D-24, and check for a recognized retry before occupied-connector logic.
  Keep history after the window; do not silently call this permanent exactly-once
  processing. An expired identical-looking Start needs a review indication rather
  than an unqualified normal-billing claim if processed as a new attempt.
  This window applies only to cross-connection Start recognition: it does not expire
  transaction-ID references, repeated-Stop protection or the existing same-connection
  response-replay boundary.
- Reuse the station-row lock and generation checks before every session mutation;
  order locks station then session. A partial unique database index enforces one
  active session per station/connector. A superseding Start transitions the previous
  session and inserts the next in the same transaction, with audit and replay response.
- Distinct requests serialized behind permission changes observe the committed current
  permission. A recognized retry preserves its original decision even after revocation;
  it is a replay of an earlier attempt, not permission for a new attempt.
- Unknown/wrong-station transaction IDs, or MeterValues naming the wrong connector,
  receive the same generic CALLERROR with a bounded audit event and no session mutation.
  Do not disclose another station's transaction details. Malformed frames/required
  field types still follow the existing input-rejection boundary.

**Suspicious evidence and completion**

- Keep out-of-order/duplicate meter samples as evidence without moving a trustworthy
  latest-reading summary backward in event time or meter value. Flag inconsistent
  evidence rather than using arrival order to rewrite the physical history.
  Compare against the start baseline and indexed chronological neighbours, not just
  the latest reading. A conflicting value at the same instant preserves the first
  trustworthy summary and records a review reason. Completed summaries stay fixed;
  subsequent meter evidence/review observations are retained separately in audit.
- A Stop with a backwards end meter can complete the attempt when its time is
  admissible, but energy is unknown and marked suspicious, never negative/zero by
  correction. A repeated Stop cannot overwrite an established completion; audit
  meaningful disagreement and preserve the original result/energy effect.
  A Stop earlier than an already observed meter sample, or lower than a preceding
  trustworthy sample, is also suspicious rather than normal billing-eligible data.
  There is one completion/domain-stop event; duplicate/conflict audit entries are
  diagnostics, not additional completion events or repeated energy effects.
- Time policy: a structurally valid Stop before the session's start is
  acknowledged and retained as suspicious evidence; move an active session to
  interrupted so it does not appear to be charging forever or block its connector.
  Do not establish a trusted stop time, duration or final energy, or invent substitutes.
  A later admissible Stop can complete the same session. An already-completed session
  is never downgraded by a subsequent bad Stop. This distinguishes protocol receipt
  from a trustworthy business outcome. First-established completion means the first
  admissible Stop, not the first syntactically valid Stop carrying impossible chronology.
- A fresh Authorize-only connection must leave existing session state unchanged,
  while preserving D-22's socket fencing. A deliberate Boot still interrupts; a
  subsequent valid Stop corrects that same interrupted record, including scenario 07.

**This slice versus later work**

- WP-1b delivers the four session actions, migration, API/list/detail UI, Authorize-only
  probe and automated unchanged scenario-01 acceptance with independent API/DB checks.
  Full multi-scenario UI/result correlation remains WP-2; do not silently move the
  entire simulator experience or production security into this PR.
- Test these minimum integrity rules now, including cross-replica session access and
  takeover without lifecycle change. Broader crash/load/retention and all-scenario
  proof still belong to WP-2/WP-3 and stay visibly incomplete in the matrix.
- Implementation resource details: session list API returns at most 100, newest first,
  with a positive bounded `before` cursor; UI currently displays the latest 100.
  Meter storage is Numeric(30,6), finite magnitude below 1e24 Wh and no silent rounding;
  invalid string evidence is retained, while unsupported Start/Stop counter precision
  is rejected before database work. JSON numbers are presentation, not settlement
  precision. Retry configuration is 1–604800 seconds, default 86400, independently
  of active exact-match protection, transaction references and Stop idempotency.

### WP-1a accepted boundary

- Implement Boot and Authorize for this PR. Other actions return protocol errors
  until their owning slice; charging/session completion is not claimed.
- Create/list stations and tags, toggle permissions, and check real OCPP authorization
  from the Flask UI through the unchanged provided helper. Empty data is explicit.
  This describes delivered WP-1a; D-23 supersedes only the probe helper choice in WP-1b.
- Filter raw extras before library name conversion; retain request/output validation.
  Correlatable errors get CALLERROR; malformed JSON must not poison later valid calls.
- Same-connection replay uses `(connection UUID, uniqueId)` and canonical relevant
  payload. Exact retries return the recorded response; conflicts are audited/rejected.
  New-connection Authorize always reads current permission. Charging retries remain Q-01.
- Domain mutation, audit and replayable response share a short transaction; validate
  the response before commit and send after releasing the database connection.
- Initial configurable database bounds: pool 5 per replica, overflow 0, acquisition
  timeout 2 seconds, lock timeout 1 second, statement timeout 3 seconds. Validate
  these defaults rather than claiming production capacity. Bound transport/work too.
- Default frontend URL `http://localhost:8080`; publish only frontend and OCPP ingress.
- Flask POSTs use CSRF tokens, escaped templates, bounded body sizes and backend
  timeouts. Frontend login remains out of scope as allowed by the assignment.
- Compatibility established with `ocpp==2.1.0`: use public `Call` and schema APIs
  directly, with the narrow raw-name filter, rather than inherit its full dispatcher.
  Both request and pre-commit response validation remain enabled. Exact runtime/test
  versions and hashes are in the lockfiles; Python/PostgreSQL image digests are pinned.
- Initial transport bounds per replica: 128 connections, 64 KiB text frames, queue 8,
  20 messages/s with burst 40 per socket, 10,000 calls per connection, 300s idle timeout.
  These are demo bounds, not production sizing or fleet-wide DDoS protection.
- Operator-created IDs use URL-safe letters/digits plus `. _ : -`, starting with a
  letter/digit, max 64 station/20 tag characters. OCPP Authorize still accepts the
  library's broader string contract; schema-valid unknown tags return Invalid.
- The UI preserves and labels the checked station/tag beside its result. A stored
  ownership generation is not presented as proof that a socket is currently online.
- Existing ownership is fenced immediately on valid takeover; a stale physical socket
  closes on its next valid supported request, not by a cross-process close broadcast.
  Audit/inbox retention, larger fleets and comprehensive failure/load proof remain later work.

### Implementation boundaries and remaining mechanics

- D-23's Authorize-only UI probe and connection caveat are implemented and verified
  by transport, actual three-replica smoke and live browser observation. Original
  tooling and deliberate scenario Boots remain unchanged. D-27 approved the full
  WP-1b plan; candidate PR review/merge remains separate from runtime evidence.
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
| Q-01 | D-24/D-27 and connection-scoped replay are implemented/tested: active exact matches stay protected; interrupted/completed use 24-hour post-transition grace plus expired-match indication. Remaining scenario, retention/failure extensions stay WP-2/WP-3. | WP-2, WP-3 |
| Q-02 | Library choice/public API/raw filter resolved and pinned in WP-1a; collision, malformed-input and output-validation tests pass. Extend exact extra-field wire coverage to all four charging actions; do not imply existing Boot/Authorize proof covers every action. | WP-2 |
| Q-03 | D-25–D-27/D-12 are implemented/tested: supersede occupied attempts, reject bad references, retain suspicious evidence, interrupt invalid-time Stops, preserve first admissible completion. Full remaining scenario acceptance/UI stays WP-2. | WP-2 |
| Q-04 | D-21/D-22, initial bounds and explicit/deployed cross-replica continuation are implemented/tested. Extend concurrent-writer/crash/load proof and tune limits in WP-3. | WP-3 |
| Q-05 | Atomic session audit fields and bounded/redacted operational logging with rotation are implemented. Retention and reliable full-scenario result extraction remain; do not promise durable external diagnostic collection. | WP-2, WP-3 |
| Q-06 | Access/integration resolved: candidate manually merged KWMS PR #1 (`2abf0cc`), both fork merge ancestors are verified, and KWMS is now canonical. Final complete submission/verification remains pending in WP-4; the fork retains original review records. | Candidate, Codex |
| Q-07 | D-23 implemented/verified: Authorize-only UI adapter, unchanged originals, documented ownership caveat, exact active-view preservation and stale-socket rejection. Full scenario browser experience remains WP-2; candidate WP-1b review/merge is still required. | Candidate, WP-2 |

## Source findings and spec change history

- 2026-10-17: Initial discussion accepted the decisions above and added actual
  three-replica support beyond the original scaling-explanation requirement.
- 2026-10-17: Candidate approved the foundation spec and authorized fork publication.
  Codex owns proactive verified commit/push/PR handoff; candidate manual merge remains
  the gate. This changes workflow ownership, not application behavior.
- Source review corrected an initial proposal for permanent `(station, uniqueId)`
  deduplication. OCPP-J 1.6 §4.1.4 only guarantees sender/connection uniqueness.
- 2026-10-17: Candidate approved the WP-1a/WP-1b split, empty default database,
  one process/two listeners and three-replica foundations immediately (D-18–D-22).
- WP-1a verification corrected missing SQLAlchemy async dependencies, secret stdin
  creation, unchanged local image tags on redeploy, migration logging reset, and
  misleading UI connectivity/result context. These repairs preserve the approved behavior.
- Supplied scenario 02 recomputes `$ts(-5m)` for each Stop. Its two payloads can
  differ by a second despite intending a duplicate; preserve the original fixture
  and assert transaction-level idempotency instead of assuming identical bytes.
- 2026-10-17: Candidate manually merged WP-1a; begin WP-1b discussion, not automatic
  approval of unresolved mechanics. Clarified the existing server-side simulator
  boundary. Brought dependent parts of Q-01/Q-03 forward and recorded Q-07 after
  inspecting the supplied helper. No session behavior implemented by this update.
- 2026-10-17: Candidate approved D-23 and requested an exact explanation and source
  compatibility check, including the connection caveat. Reviewed BRIEF 89–97,
  133–143, 179 and unchanged scenario 07; recorded the rationale and pending tests
  above. UI probe behavior will change in WP-1b; no application code changed here.
- 2026-10-17: Candidate manually merged the history-preserving KWMS integration
  and authorized continuing there. Canonical remotes/publication change to KWMS;
  existing behavior, source baseline and candidate-only merge gates are unchanged.
- 2026-10-17: Candidate explicitly approved all three proposed session rules and
  asked to continue (D-24–D-26). Recorded the policies and finalized the detailed
  WP-1b contract/plan for the promised review. Concrete retry/time defaults remain
  visibly proposed; no session implementation or new test pass is claimed here.
- Independent plan review caught two unsafe draft details before code: a global
  24-hour cutoff could split a still-active long session, and acknowledging impossible
  Stop chronology without a lifecycle change could leave a session active forever.
  The revised proposal protects active exact-match retries and marks invalid-time
  stops interrupted with evidence preserved. Candidate review remains required.
- 2026-10-17: Candidate explicitly approved the refined retry and invalid-time Stop
  defaults and the plan (D-27). WP-1b implementation may proceed within that contract;
  full scenario UI and production features remain outside this slice.
- WP-1b verification/review repaired empty-tag identity incompatibility, Decimal
  context rounding/overflow, historical meter inversions, post-completion summary
  mutation and invalid frontend-ID error classification. Added focused regressions;
  resource/presentation bounds and first-completion behavior are documented above.
- WP-1b publication review reconciled stale implementation/question status with
  actual evidence and later-package scope. This documentation-only handoff amendment
  changes no behavior or original requirement; candidate manual PR review is pending.
- Future changes must state affected requirement IDs, old/new behavior, rationale,
  candidate agreement for material choices, and changed verification evidence.

References: [OCPP-J specification, §4.1.4](https://github.com/mobilityhouse/ocpp/blob/master/docs/v16/ocpp-j-1.6-specification.pdf),
[evaluated library dispatch](https://github.com/mobilityhouse/ocpp/blob/2.1.0/ocpp/charge_point.py),
[original scenario 02](../take-home/scenarios/02_duplicate_stop.json).
