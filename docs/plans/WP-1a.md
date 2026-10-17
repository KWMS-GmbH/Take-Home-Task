# WP-1a — deployable authorization slice

Status: authorized after targeted discussion on 2026-10-17.
Execution: implemented; local acceptance evidence is recorded in WORK.md. Candidate
PR review/merge remains the completion gate. No dependent session work started.
Branch: `codex/authorization-slice`, based on manually merged WP-0.
This plan is disposable; [SPEC.md](../SPEC.md) owns behavior and
[REQUIREMENTS.md](../REQUIREMENTS.md) owns acceptance traceability.

Goal: browser-to-HTTP/OCPP-to-PostgreSQL station/tag/permission management and
Boot/Authorize with three replicas. Charging/session simulation remains later work.

## Execution

1. Pin Python/PostgreSQL images and exact runtime/test dependencies. Prove the OCPP
   library's public receive/send adapter, raw filtering and retained output validation.
2. Add migrations for stations, tags, permissions, audit and connection-scoped replay.
   Serialize startup migrations through a PostgreSQL advisory lock.
3. Add management API/application service with short transactions, generation fencing,
   atomic audit/replay, current permission reads and bounded pools.
4. Run internal HTTP and public OCPP listeners in one process. Validate before
   promotion, reject stale owners and contain malformed/oversize messages.
5. Add Flask management/real OCPP authorization, CSRF, bounded HTTP calls, escaped
   templates, clear empty/error states and live browser review.
6. Deploy three service definitions with three backend replicas, secrets, volume,
   overlay boundary, startup retries, health and restart policies.
7. Verify, review code/architecture, update actual evidence/limitations, make coherent
   commits and proactively open the PR. Candidate reviews/merges manually.

## Evidence required

- Assignment guard and its existing regression tests.
- Container-based formatting/lint/type checks and image builds.
- Real PostgreSQL authorization matrix, station specificity and immediate permission changes.
- Boot/Authorize contracts, extras/name collisions, malformed frames, replay/conflicts,
  stale-generation rejection and rollback/audit assertions.
- Three-replica startup against an empty database serializes migrations successfully.
- Healthy actual Swarm, API/DB unexposed, secrets in use, persistence across redeploy.
- Browser creation/toggling plus Accepted/Blocked/Invalid through real OCPP.
- Explicit remaining charging/scenario and full failure-proof limitations.

Planned coherent commits: application/tooling foundation; OCPP and Flask slice/tests;
then deployment/review fixes/evidence. Adjust to actual changes, not arbitrary line counts.
