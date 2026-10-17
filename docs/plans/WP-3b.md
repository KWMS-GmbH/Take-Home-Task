# WP-3b — bounded operation, dependency recovery and safe replay maintenance

Status: candidate explicitly approved D-33–D-35 on 2026-10-17. SPEC.md owns behavior;
planned checks below are not passes. Branch `codex/operations-slice`, based on
manually merged WP-3a `3699be9`. No original-file edits or extra deployed service.

## Work

1. Review concrete health/routing, recovery isolation and maintenance eligibility
   before code. Guard against tmpfs persistence claims, pruning pending generations,
   unsafe Docker targets, destructive dry-run or cleanup without atomic audit.
2. Keep frontend liveness independent; add backend listener-only liveness checking.
   Move Swarm app health probes to liveness and retain explicit per-task readiness
   in deployment success. Add listener/DB health and configuration tests.
3. Implement one-station replay cleanup in a short station-locked transaction:
   fixed seven-day DB-time cutoff, only generations below active, stable batch at
   most 100, dry-run no writes, explicit apply with atomic summary audit. Add strict
   container CLI/wrapper, no HTTP purge/cron. Test filters, limits, rollback/audit,
   concurrent maintenance/promotion/replay and old/current retry protection.
4. Add resource-bound integration and 12-station real-WS correctness tests through
   independent pools/listeners/API. Do not tune production merely to create a diff.
5. Add owned disposable Compose recovery project with persistent test volume.
   Host controller starts/stops only that DB; tooling gets no Docker socket.
   Bounded control markers coordinate a long-lived three-instance Python checker.
   Prove unavailable responses, retained pre-outage state and completion/retry after
   DB returns. Wire one-command recovery into `make test`/CI; expose focused command.
6. Run focused groups/recovery repeatedly, `make check`, growing `make test`, guard,
   real local redeploy and normal three-replica `make smoke`. Exercise live CLI only
   in dry-run. Independent cross-component code/architecture review and repairs.
7. Update SPEC/REQUIREMENTS/WORK/README/AI_USAGE/time records with actual evidence,
   state exact limits and remaining final acceptance/security/network-blackhole gaps.
   Make coherent commits, push/open layered KWMS PR, wait exact-head CI, hand off.
   Candidate manually merges; WP-4 implementation waits for that merge.

## Intended evidence, not results

- Live-but-unready instances report truthful temporary errors and recover without
  application restart across a graceful owned-DB container stop/start, preserving
  its volume and exact committed session/audit/inbox/meter evidence.
- Contention/capacity failures are bounded and do not acknowledge partial writes;
  capacity is reusable. Twelve station IDs yield twelve correct once-completed
  sessions with retries and no resource leak; measured duration is not a load SLA.
- Dry-run preserves all data; explicit cleanup removes only the bounded eligible
  superseded rows and atomically records what happened. Current/pending/recent/other
  station rows plus sessions/meter/business audit survive; old sockets stay fenced.
- All controller/test resources are exact-owned, generated, bounded and cleaned;
  no live fault/purge, host DB or shared volume deletion. No arbitrary network
  blackhole, HA or production-grade security/retention guarantee.

Planned commits: approved contract/plan; health/readiness; maintenance/tests;
bounds/workload; isolated recovery tooling; integrated review/evidence/handoff.
