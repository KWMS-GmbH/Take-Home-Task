# WP-3a — deterministic charging races and process-crash safety

Status: candidate approved D-31/D-32 boundaries on 2026-10-17. SPEC.md owns behavior;
this plan is disposable and is not a test-result claim. Based on manually merged
WP-2 `e0b51fc`, branch `codex/reliability-slice`.

## Outcome and non-goals

Prove that three independent backend instances serialize contested charging and
permission changes, and recover correctly when a test backend process dies around
commit/reply. Existing domain/API/UI/deployment rules stay unchanged unless a
regression exposes a narrowly repairable bug; material new behavior needs agreement.
No live-stack fault injection, production fault endpoints, retention, load target,
database outage/HA, autoscaling or extra deployed service in this PR.

## Work

1. Review the concrete race/crash contract against existing lock/promotion, replay,
   permission and commit-before-send boundaries. Independently review proposed test
   synchronization, test-only isolation and claim limits before implementation.
2. Add focused real-PG tests with independent engines/instance IDs. Use deterministic
   barriers and bounded waits for exact/distinct Start races, same/different
   connectors, both permission orders, equal/conflicting Stops and stale writers.
   Assert unrelated-station progress plus actual API, domain/inbox/audit postconditions.
3. Add a test-only subprocess worker and parent harness. Observe/flush the target
   transaction before commit; separately observe committed service return before
   protocol send. Kill only that owned child; prove absent/present complete results
   through an independent reader. Cover Start/Stop at both boundaries, no received
   success, reconnect through surviving instances and one final completion.
4. Bound and clean all process/socket/engine resources even on failed assertions.
   Refuse crash workers unless the explicit database name ends `_test`. Test hooks
   remain under tests; never add runtime fault routes or kill live Docker tasks.
5. Run focused tests repeatedly, `make check`, full `make test`, source guard and
   existing deployed `make smoke`. Independent code/architecture reviews inspect
   determinism, false-pass risks, pre/post-commit distinction and production diff.
   Record actual results and limitations; UI unchanged, so no new visual QA claim.
6. Update SPEC/REQUIREMENTS/WORK, README reliability account, AI_USAGE and honest
   time evidence. Make coherent race/crash/evidence commits, push/open layered KWMS
   PR, wait for exact-head CI and hand over. Candidate manually merges before WP-3b.

## Intended evidence, not results

- Newest committed ownership fences stale mutations/replays, not requests that
  legitimately committed before takeover. At most one active record per connector.
- Permission snapshots match controlled serialization order; completion effects
  remain once-only while conflicting/retried requests have distinct diagnostics.
- Pre-commit process death leaves no target partial domain/audit/inbox state;
  post-commit lost reply preserves all target state, and new-socket retry converges.
- No Boot or automatic completion is invented by death/reconnect. Sequence gaps
  are acceptable. All child processes are owned, database-guarded and reaped.
- No claim of DB/host-loss availability, measured production capacity or exhaustive
  crash coverage. U-SCALE/U-AUDIT remain partial until WP-3b/final acceptance.

Planned commits: approved contract/plan; deterministic races; process-crash proof;
review/evidence and PR handoff. Existing container dependencies should suffice.
