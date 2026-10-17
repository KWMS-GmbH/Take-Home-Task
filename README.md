# Charging Session Service

Interview take-home for KWMS. The original assignment and supplied replay tooling
remain unchanged in [take-home/](take-home/). Submission deadline: Sunday,
2026-10-18, 21:00 Europe/Berlin; our safety target is 18:00.

## Current milestone: WP-1a

This independently reviewed slice provides station/tag management, station-specific
permission toggles, and real OCPP Boot/Authorize checks in the Flask UI. Three backend
replicas share PostgreSQL state and enforce connection ownership there.

**Not yet a complete submission:** charging sessions, the other four actions, full
scenario execution and session screens follow in WP-1b/WP-2. Unsupported actions
return CALLERROR rather than pretending to succeed. The [work board](docs/WORK.md)
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
fresh request, without a restart. Optional sample records:

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
CSRF, shared permission updates, connection takeover and the UI's unchanged supplied
OCPP helper. It leaves its named verification records, permitted, in the deployed
database. Live browser visual QA is a separate recorded review, not a claimed
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
socket: the station reconnects to any surviving replica. Domain sessions will be
shared durable records, not process-owned objects.

Same-connection exact retries replay a stored validated response; conflicting reuse
is audited and rejected. Reusing an ID on another connection is legal and does not
cache an old authorization decision forever. Mutations, audit and intended response
commit together; network replies happen after releasing the transaction. Initial
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

## Security and limits

The UI has CSRF-protected POSTs, escaped templates, security headers, a 16 KiB body
limit and bounded upstream calls. It intentionally has no login, as permitted by
the brief. Use only a trusted local demo environment: public deployment needs TLS,
operator authentication/roles, station credentials, secure cookies, perimeter rate
limits, least-privilege database roles and backups. Publishing OCPP does not expose
management HTTP routes.

Initial transport limits per replica: 128 sockets; 20 messages/s with a 40-message
burst per socket; 10,000 requests per connection; 300s idle timeout. These are bounded
demo defaults, not measured production capacity or DDoS protection. Invalid JSON does
not acquire ownership or poison later valid requests. Socket/audit/inbox retention,
fleet pagination and broader load proof remain tracked work.

Business audit is durable PostgreSQL history; unauthenticated HTTP changes identify
only an `anonymous_operator` role, not a person. Application events are JSON-structured;
framework startup messages retain their text format. Application events exclude raw
frames, tags and credentials; HTTP access logging is disabled. Error types omit raw exception
payloads; this deliberately limits diagnosis. Docker logs rotate (10 MiB × 3 files
per task, bounded nonblocking buffer) and can be lost with the host or rotation;
central durable log collection is not included.

## Decisions and process

The [living spec](docs/SPEC.md) records decisions for all nine messy cases and open
questions. Their implementation/acceptance status is tracked separately; session-case
decisions are not yet delivered behavior. [Workflow](docs/WORKFLOW.md) defines
spec-first packages, growing tests, independent code/architecture review and candidate
manual PR merges. [AI_USAGE](AI_USAGE.md) and the [effort ledger](docs/TIME_LOG.md)
disclose assistance, corrections and observed time without inventing human effort.
