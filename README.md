# Charging Session Service

Interview take-home for KWMS. The original assignment and supplied replay tooling
remain unchanged in [take-home/](take-home/). Submission deadline: Sunday,
2026-10-18, 21:00 Europe/Berlin; our safety target is 18:00.

## Current milestone: WP-1b

Manage station/tag permissions, check real OCPP authorization, and inspect durable
charging sessions through Flask. All six required actions are handled; sessions keep
lifecycle, authorization-at-start and meter quality separate. Three backend replicas
share PostgreSQL state and enforce connection ownership there.

**Not yet a complete submission:** the full browser scenario runner, per-scenario
results/correlation and comprehensive failure/load/retention proof remain WP-2/WP-3.
The unchanged happy-path scenario is already an automated API/database acceptance
test, not yet a browser simulation button. The [work board](docs/WORK.md)
and [requirement matrix](docs/REQUIREMENTS.md) distinguish completed evidence from
remaining work.

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
and transaction details. A fresh database has no sessions until a simulated station
creates them; `make smoke` automatically creates a complete CHECK-CP/CHECK-TAG
10.0 kWh example. Optional sample records:

```sh
make demo-data                    # explicit, idempotent CP-001 / TAG-42 / TAG-BLOCKED
make logs                         # retained, timestamped service logs
make teardown                     # removes this stack, PRESERVES database and secrets
```

Redeploy with `./deploy.sh`; existing data and secrets are reused. Do not delete the
database volume or rotate its password secret independently: the initialized database
keeps its existing password. `STACK_NAME=other ./deploy.sh` changes resource names,
not published ports; two copies cannot share ports 8080/9000. Teardown never leaves
Swarm or removes unrelated Docker resources.

## Automated verification

```sh
make verify-assignment            # byte-for-byte baseline guard, Git + shell only
make check                        # container: guard tests, format, lint, types, config
make test                         # container: unit + isolated real-PostgreSQL integration
make smoke                        # against deployed stack; creates CHECK-CP / CHECK-TAG
```

`make test` uses an isolated Compose project and a disposable PostgreSQL tmpfs. It
never targets the deployed database; destructive test fixtures require an explicit
database name ending `_test`. It races three migration processes and tests database
postconditions, not merely protocol responses. Test resources are removed afterward.

`make smoke` addresses all three actual backend tasks explicitly, checks UI forms and
CSRF, shared permission updates, stale fencing, the UI's real Authorize-only probe,
an active session surviving that takeover, and completion via another actual replica.
It checks API-backed session list/detail views and leaves its named verification
records and completed session in the deployed database. Live browser visual QA is a
separate recorded review, not a claimed
checked-in browser automation suite. CI runs static/PostgreSQL checks, deploys an
ephemeral Swarm and runs the three-replica smoke test.

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

Running `docker service scale kwms_backend=3` is already the default. Shared
permissions, database fencing and serialized migrations are built in now. Full
charging races, crash/retry proofs and load tuning remain WP-2/WP-3, so this is not
yet a claim that the complete application is three-replica verified. The single-node
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

The UI permission check sends **Authorize only**. The supplied `replay.authorize()`
convenience helper sends Boot first, which would incidentally interrupt an existing
session under our reboot policy. Only our UI helper use is replaced; supplied files
and deliberate scenario Boots remain unchanged. The original CLI helper therefore
still sends Boot. This adapter is allowed by BRIEF line 179 and preserves its required
Authorize-before-scenarios workflow; full scenario UI is the next slice.

**Shared station ID caveat:** even Authorize-only promotes the probe's connection,
including Blocked/Invalid outcomes. The previous socket remains fenced after the
probe closes and must reconnect. This must not itself interrupt a charging session.
Use different station IDs for independent simulations; this is not passive monitoring
or authenticated station identity. Scenario 07's overlapping-connection, one completed
3.0 kWh requirement is not exempted: unchanged scenario 07 has passing automated
API/DB acceptance. The all-scenario browser experience remains tracked in the matrix.

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
management HTTP routes.

Initial transport limits per replica: 128 sockets; 20 messages/s with a 40-message
burst per socket; 10,000 requests per connection; 300s idle timeout. These are bounded
demo defaults, not measured production capacity or DDoS protection. Invalid JSON does
not acquire ownership or poison later valid requests. Socket/audit/inbox retention,
fleet pagination and broader load proof remain tracked work. Session history itself
persists; the Start retry window is not a database retention/deletion policy.

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
tests do not claim all supplied scenarios or failure modes are already proven. [Workflow](docs/WORKFLOW.md) defines
spec-first packages, growing tests, independent code/architecture review and candidate
manual PR merges. [AI_USAGE](AI_USAGE.md) and the [effort ledger](docs/TIME_LOG.md)
disclose assistance, corrections and observed time without inventing human effort.
