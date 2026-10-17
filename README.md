# Charging Session Service

Interview take-home for KWMS. The original assignment and supplied replay tooling
remain unchanged in [take-home/](take-home/). Submission deadline: Sunday,
2026-10-18, 21:00 Europe/Berlin; our safety target is 18:00.

## Current milestone: WP-3a reliability verification

Manage station/tag permissions, check real OCPP authorization, run all eight supplied
scenarios, and inspect their actual API session data through Flask. Protocol and
session-data outcomes are checked separately. Sessions keep lifecycle,
authorization-at-start and meter quality separate; three backend replicas share
PostgreSQL state and enforce connection ownership there.

WP-3a adds deterministic contested-write and actual process-death tests for these
existing rules; it does not change production behavior or the browser experience.

**Not yet a complete submission:** bounded load, database dependency recovery and
safe replay maintenance remain WP-3b; independent final acceptance remains WP-4. The
[work board](docs/WORK.md) and [requirement matrix](docs/REQUIREMENTS.md) distinguish
implemented behavior, observed evidence and remaining work.

## Deploy on a single-node Swarm

Prerequisites: Git, Docker Desktop/Engine with Compose and Swarm support, POSIX shell,
and OpenSSL (available on macOS). Start Docker Desktop first. No host Python, pip,
PostgreSQL or Node installation is required. Initial builds download pinned images
and hash-locked Python dependencies.

```sh
docker swarm init                 # once, if this Engine is not already in a Swarm
./deploy.sh                       # builds images, creates secrets, waits for health
```

Open [http://localhost:8080](http://localhost:8080). A fresh volume is empty: add a
station and tag, allow their permission, then use **Check authorization**. Known tags
without permission are Blocked; unknown tags are Invalid. Changes apply to the next
fresh request, without a restart. Open **Sessions** for station-filtered history
and transaction details, or open **Simulate** to run the supplied cases. A fresh
database has no sessions until a simulated station creates them. Optional setup and
sample records:

```sh
make demo-data                    # explicit, idempotent CP-001 / TAG-42 / TAG-BLOCKED
make demo-sessions                # seven real examples, visible immediately in Sessions
make logs                         # retained, timestamped service logs
make teardown                     # removes this stack, PRESERVES database and secrets
```

`make demo-sessions` uses real HTTP/OCPP and prints direct UI links. It creates
dedicated `DEMO-*` stations and `DEMO-ALLOWED`/`DEMO-BLOCKED` tags; it leaves
`DEMO-UNKNOWN-TAG` unregistered. Fixed illustrative event times are on October 17,
2026, UTC. It never runs automatically, deletes data or repairs changed/partial
session outcomes. Re-running verifies/reuses existing examples without duplicates;
empty reserved stations may be populated. Use those station IDs only for these
examples, not independent simulations. Active describes durable lifecycle, not a
currently connected socket; an incomplete session has no invented final duration.

| Station | Visible result |
| --- | --- |
| DEMO-HOME | Completed, Accepted, valid meters: 7.2 kWh / 2 hours, eligible prerequisites |
| DEMO-OFFICE | Completed, Accepted, valid meters: 22.5 kWh / 90 minutes, eligible prerequisites |
| DEMO-CHARGING | Active, Accepted: 2.4 kWh provisional, final duration unknown, not eligible |
| DEMO-REBOOT | Interrupted, Accepted: 6.0 kWh provisional, stop/duration unknown, not eligible |
| DEMO-BAD-METER | Completed, Accepted: 45 minutes, suspicious backwards meter, energy unknown, not eligible |
| DEMO-BLOCKED | Interrupted, Blocked: suspicious non-numeric reading, energy/duration unknown, not eligible |
| DEMO-UNKNOWN | Active, Invalid tag: meter quality/energy/duration unknown, not eligible |

These are custom review examples of existing rules, not new lifecycle/quality labels
or substitutes for supplied-scenario acceptance. Keep their reserved station IDs
separate from scenario runs, whose deliberate Boots can change open sessions.

Redeploy with `./deploy.sh`; existing data and secrets are reused. Do not delete the
database volume or rotate its password secret independently: the initialized database
keeps its existing password. `STACK_NAME=other ./deploy.sh` changes resource names,
not published ports; two copies cannot share ports 8080/9000. Teardown never leaves
Swarm or removes unrelated Docker resources.

## Run scenarios in the browser

Open [Simulate](http://localhost:8080/simulation). Select an existing station, an
allowed tag and a different blocked or unknown tag. `make demo-data` provides
CP-001, TAG-42 and TAG-BLOCKED for this purpose. Acknowledge the Boot/connection
side effects, then submit. The simulator runs **server-side in the Flask container**:
Python opens real OCPP WebSockets; the browser submits the form and displays results.

Both tags are checked afresh over Authorize-only connections. The primary must be
Accepted, and the second explicitly Blocked or Invalid. Otherwise no scenario runs;
the application never grants/revokes permissions to make a test pass. Permission
changes during execution remain effective and may cause a visible failure.

Each scenario shows protocol results/warnings, its original check lines, collapsible
automated data checks and exchanges, and exact transaction-linked API snapshots.
Snapshots are fetched immediately after each case, before later Boots; the current
session-detail link can therefore show a later state. Execution completed means the
scenario finished, not that its charging session completed: reboot-without-Stop
correctly shows an interrupted session with provisional/unknown energy and duration.
API checks do not prove internal audit-row counts; real-PostgreSQL tests check those
separately. Missing API evidence is unverified, never a data pass.

The adapter uses the eight fixed original JSON files in filename order and public
replay call/raw helpers. Source files remain byte-for-byte unchanged. Relative times
are rendered from one stable UTC instant per case, preserving offsets; successive
case starts are spaced by at least 1.1 seconds and a run waits past recent Start
receipt when necessary. This prevents whole-second timestamps from accidentally
making distinct cases look like exact Start retries. Unlike the unchanged CLI's
per-message clock rendering, intended duplicate payloads stay identical within a
browser case. This is an explicit orchestration adaptation, not weakened retry rules
or a modification to the supplied fixtures.

PostgreSQL admission allows two runs globally and one run over each scope containing
the selected station **and its exact `<station>-OTHER` identity** used by scenario 07.
Overlapping scopes are rejected. Simulation base IDs are limited to 58 characters so
the unchanged suffix fits the normal 64-character station limit. A 30-second lease
and station-locked token checks fence expired/reassigned simulated sockets. The
`X-Simulation-Token` header coordinates our runner; it is **not authentication** and
does not prevent ordinary OCPP clients from interfering. Boots can interrupt existing
sessions on either identity; new connections can fence older sockets there.

The async run has a 20-second execution deadline, including admission, preflight,
spacing, scenarios and normal API snapshots. It is not a 20-second end-to-end HTTP
promise: initial form metadata requests and bounded cleanup/partial-data collection
also take time. Interrupted execution/deadlines retain bounded partial evidence and
mark later cases not run; completed cases can show failed/unverified checks while
execution continues. Reports exist only in that response, not cookies or a durable report archive;
reloading/reposting can start another run. Committed sessions/audit remain even when
the report fails or times out. There is no background queue, resume engine or undo.

## Automated verification

```sh
make verify-assignment            # byte-for-byte baseline guard, Git + shell only
make check                        # container: guard tests, format, lint, types, config
make test                         # container: unit + isolated real-PostgreSQL integration
make smoke                        # deployed stack: three replicas + full scenario UI flow
```

`make test` uses an isolated Compose project and a disposable PostgreSQL tmpfs. It
never targets the deployed database; destructive test fixtures require an explicit
database name ending `_test`. It races three migration processes and tests database
postconditions, not merely protocol responses. Test resources are removed afterward.

Focused checks use the same isolated lifecycle; omit arguments for the full suite:

```sh
./scripts/test.sh tests/test_charging_races.py tests/test_process_crashes.py
```

The race tests use three separate Service instances/pools and observe actual
PostgreSQL lock contention before releasing controlled barriers. They cover both
Start/promotion and permission orders, connector independence, unrelated-station
progress, duplicate/conflicting Stops and stale mutation/replay rejection. Test-only
lock/statement timeouts are longer to permit observation; runtime defaults stay unchanged.

Crash tests kill one owned subprocess running the real OCPP listener, with two
independent surviving service/listener instances in the parent test process. For
both Start and Stop, they pause after SQL domain/audit/inbox writes are flushed but
before commit, and separately after commit but before the protocol sends its reply.
Independent API/PG readers verify rollback versus durability; the client sees no
successful reply, reconnects through survivors and converges to one completion.
Generated-ID gaps after rollback are valid. All hooks are test-local, waits/cleanup
are bounded, and unsafe database URLs are rejected before engine creation. These
tests do not kill the live demo stack, PostgreSQL or three separate Swarm tasks.

`make smoke` addresses all three actual backend tasks explicitly, checks UI forms and
CSRF, shared permission updates, stale fencing, the UI's real Authorize-only probe,
an active session surviving that takeover, and completion via another actual replica.
It submits the real simulation form, verifies all eight protocol/data results and
eight distinct API-linked sessions, and checks that a denied primary tag creates no
scenario sessions. It creates/reuses CHECK-CP, CHECK-CP-OTHER, CHECK-TAG and
CHECK-BLOCKED, leaves the primary allowed and secondary blocked, and retains its
verification sessions. It does not touch the reserved DEMO-* examples. Live browser
visual QA is a separate recorded review, not a claimed checked-in browser automation
suite. CI runs static/PostgreSQL checks, deploys an ephemeral Swarm and runs this smoke
test.

## Architecture and reliability

Exactly three Swarm services: Flask `frontend`, asynchronous `backend` (three replicas),
and PostgreSQL `db`. Frontend calls the internal HTTP API, never the database. Each
backend process runs two separate listeners: internal HTTP 8000 and OCPP-only 9000.
Only UI 8080 and OCPP 9000 are published. Both application images run as UID 10001.
PostgreSQL and Flask signing credentials are Docker secrets, not production plaintext
environment values. An overlay network and persistent database volume are declared.

The OCPP library's public message/schema APIs validate inputs and outputs. A narrow
raw-name filter ignores assignment-unlisted fields *before* interpretation, including
name-collision attempts. No private dispatcher override or disabled schema validation
is needed. Frames are text-only, at most 64 KiB, with bounded queues and work.

A connection registers its station immediately, then its first valid supported call
promotes a database generation. Every request locks/checks that station, so older
sockets cannot mutate or replay stale results after takeover, including across
replicas. Old sockets close when they next send a supported valid call; immediate
cross-process physical close is not claimed. A dead process cannot transfer its live
socket: the station reconnects to any surviving replica. Charging sessions are shared
durable records, not socket/process-owned objects. A database partial unique index
enforces one active session per station/connector; station-row locks serialize
permissions, ownership and charging changes across replicas.

Same-connection exact retries replay a stored validated response; conflicting reuse
is audited and rejected. Reusing an ID on another connection is legal and does not
cache an old authorization decision forever. For OCPP, mutations, audit and intended
validated response commit together; network replies happen after releasing the
transaction. Management HTTP mutations also commit with audit. Initial
per-replica pool bounds are 5 connections, zero overflow, 2s acquisition, 3s connect,
1s lock and 3s statement timeout. Migrations use a PostgreSQL advisory lock. Startup
retries transient database failures; health/restart policies do not rely on Swarm
`depends_on` ordering.

The default three pools allow up to 15 steady application connections. The declared
one-at-a-time start-first rollout can temporarily run a fourth task (up to 20),
plus migration/admin connections. This is a configuration budget, not measured fleet
capacity; dependency recovery/load measurements remain WP-3b.

Running `docker service scale kwms_backend=3` is already the default. Shared
permissions, database fencing and serialized migrations are built in. WP-3a adds
controlled charging races and real process-death/retry proof, complementing the
actual three-task smoke. This is finite correctness evidence, not exhaustive crash
coverage, production capacity or HA. The single-node
host and single PostgreSQL instance remain failure domains; a volume is not a backup.
Multi-node deployment additionally needs shared image distribution, database/storage
placement and recovery planning; locally built images target the requested one node.

## Session decisions and simulation caveat

Energy is exact Decimal arithmetic on Wh before JSON presentation, divided by 1000
for kWh. Completed totals use admissible Start/Stop endpoints. Active/interrupted
totals are explicitly provisional from trustworthy meter evidence, or unknown. A
missing/bad meter never becomes a fabricated zero or negative energy. The derived
`billingEligible` indicator requires Accepted-at-start, completed, trustworthy final
energy/duration and no review reasons; it is not invoicing or a payment decision.

| Original messy case | Implemented policy and rationale |
| --- | --- |
| 1. Duplicates | Connection-scoped response replay; cross-connection exact Start identity uses station/connector/tag/UTC start/meter. Exact active retries never expire. Interrupted/completed matches default to 24 hours after server receipt of the transition. Repeated Stops preserve the first admissible completion regardless of that Start window; discrepancies are audited, not counted twice. |
| 2. Reboot without Stop | A fresh Boot marks open sessions interrupted, retains meter evidence and does not invent a stop time. A mere disconnect/backend crash is not proof charging stopped. |
| 3. Late Stop | Match the durable station-owned transaction and correct its interrupted record using the reported real stop time; do not create another session. |
| 4. Time offsets | Require timezone-aware times and normalize to UTC; receipt time is stored separately, never substituted for event time. |
| 5. Backwards meters | Retain evidence, flag suspicious quality and return unknown energy rather than negative/zero. Indexed chronological neighbours detect late contradictions; a Stop earlier than an observed meter sample is also flagged. |
| 6. Two connections | The new socket's first valid supported call promotes its generation; old sockets are fenced and close on their next valid supported call. Session ownership stays with the station. A Stop on the new connection can complete the original transaction. |
| 7. Garbage | Strict bounded framing/input validation, generic errors, no raw attacker data in operational logs; a bad call cannot poison a subsequent valid call or another station. |
| 8. Denied but charging | Always allocate a valid Start's integer transaction ID, preserve Blocked/Invalid authorization and the physical attempt, and never mark it normally billing-eligible. |
| 9. Revocation | Fresh Authorize/Start sees current permission; existing sessions/retries retain authorization-at-start. No remote stop is issued. Production needs an explicit enforcement/remote-stop policy rather than rewriting history. |

A different Start on an occupied connector interrupts the prior attempt without an
invented stop, creates a new transaction and flags the conflict for review. A Stop
before Start is retained/audited and changes active to interrupted, with no trusted
stop time/duration/final energy; a later admissible Stop can correct it. Established
completions do not change when duplicate/conflicting Stops or late meter evidence
arrive; late evidence remains in PostgreSQL/audit, not a repeated completion effect.

The UI permission check and simulator preflight send **Authorize only**. The supplied
`replay.authorize()` convenience helper sends Boot first, which would incidentally
interrupt an existing
session under our reboot policy. Our UI uses an adapter; supplied files and deliberate
scenario Boots remain unchanged. The original CLI helper therefore still sends Boot.
This adaptation is allowed by BRIEF line 179 and preserves its required
Authorize-before-scenarios workflow.

**Shared station ID caveat:** even Authorize-only promotes the probe's connection,
including Blocked/Invalid outcomes. The previous socket remains fenced after the
probe closes and must reconnect. This must not itself interrupt a charging session.
Use different station IDs for independent simulations; this is not passive monitoring
or authenticated station identity. Scenario 07's overlapping-connection, one completed
3.0 kWh requirement is not exempted: unchanged scenario 07 has passing automated
API/DB acceptance and a visible browser result. Coordinated browser runs exclude
overlapping scopes, but that does not exclude external devices/probes using those IDs.

`START_RETRY_SECONDS=86400 ./deploy.sh` configures the post-transition Start grace
(1–604800 seconds); active exact-match protection and Stop idempotency are separate.
Expired identical-looking Starts create a new attempt with a review flag, not a claim
of permanent exactly-once processing. API session lists are bounded to 100, with an
optional `before` transaction-ID cursor; the current UI shows the latest 100. Counters
use Numeric(30,6), magnitude below 1e24 Wh with at most six fractional digits; invalid
string samples are retained as suspicious evidence, never silently rounded. JSON
numbers are presentation values, not a high-precision settlement interface.

## Security and limits

The UI has CSRF-protected POSTs, escaped templates, security headers, a 16 KiB body
limit and bounded upstream calls. It intentionally has no login, as permitted by
the brief. Use only a trusted local demo environment: published Swarm ports are not
guaranteed localhost-only. Public deployment needs TLS,
operator authentication/roles, station credentials, secure cookies, perimeter rate
limits, least-privilege database roles and backups. Publishing OCPP does not expose
management HTTP routes. Simulation admission is resource coordination, not operator
or station identity verification; an unauthenticated trusted-network user can still
operate the demo and trigger its documented Boot effects.

Initial transport limits per replica: 128 sockets; 20 messages/s with a 40-message
burst per socket; 10,000 requests per connection; 300s idle timeout. These are bounded
demo defaults, not measured production capacity or DDoS protection. Invalid JSON does
not acquire ownership or poison later valid requests. Simulation work has separate
lease/deadline/frame/text bounds, without a background worker service. Broader
crash/load proof, audit/inbox retention and fleet pagination remain tracked work.
Session history itself persists; the Start retry window is not a database
retention/deletion policy.

Business audit is durable PostgreSQL history; unauthenticated HTTP changes identify
only an `anonymous_operator` role, not a person. Application events are JSON-structured;
framework startup messages retain their text format. Application events exclude raw
frames, tags and credentials; HTTP access logging is disabled. Error types omit raw exception
payloads; this deliberately limits diagnosis. Docker logs rotate (10 MiB × 3 files
per task, bounded nonblocking buffer) and can be lost with the host or rotation;
central durable log collection is not included.

## Decisions and process

The [living spec](docs/SPEC.md) records decisions for all nine messy cases and open
questions. Implementation/acceptance status is tracked separately; direct regression
tests cover the required supplied-scenario outcomes but do not claim every failure
mode is proven. [Workflow](docs/WORKFLOW.md) defines spec-first packages, growing tests,
independent code/architecture review and candidate
manual PR merges. [AI_USAGE](AI_USAGE.md) and the [effort ledger](docs/TIME_LOG.md)
disclose assistance, corrections and observed time without inventing human effort.
