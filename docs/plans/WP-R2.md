# WP-R2 — Frontend/simulator readability without behavior changes

Candidate approved D-36/D-37, independent execution/review and frontend before docs.
Baseline: manually merged PR #6 `3e3c854`, identical to reviewed `689cb28`.
Branch: `codex/frontend-readability`. SPEC.md owns the unchanged behavior contract.

1. Confirm merge/tree/source; inspect the frontend, simulator and existing coverage.
   Independent baseline review identifies high-risk ordering and real test gaps.
2. Characterize HTTP outcome mapping, route/method/error security contract, truncated
   collector correlation and release-warning retention against the unchanged code.
3. Extract bounded client/errors and Authorize-only adapter, pure view/report validation
   and ordinary route registration. Keep factory/injections/imports/endpoints stable.
4. Separate fixed playback/correlation, API snapshots and run orchestration. Share the
   fixed allowlist. Split independent scenario checks into named ordered helpers,
   keeping every existing name/order/text/tri-state outcome and no rules framework.
5. Review exact preflight/lease/token/clock/pacing/snapshot/timeout/finally ordering;
   preserve correlation before truncation, data/protocol distinction and security.
6. Run focused and full pinned-container/static/source/recovery gates, normal redeploy/
   three-replica smoke and actual browser checks. No live outage, purge or demo reset.
7. Independently review code/architecture and evidence; commit coherent changes,
   push/open a layered KWMS PR, verify exact-head CI and hand off for manual merge.
   No full documentation rewrite or dependent work before that merge.

Any behavioral defect is reported explicitly and separately tested, not silently
reclassified as a refactor. A material scope/security/domain change needs agreement.

Execution checkpoint: 11 baseline characterization cases passed before extraction.
Factory/routes, clients/probe, view/report validation, playback/snapshots and ordered
independent check helpers now have explicit boundaries. Focused/static/source/338-case
full suite and actual isolated recovery pass, along with normal redeploy/three-task
smoke and live browser 8/8 plus denied 0/8 and exact session detail. Independent code/
architecture review finds no blocker or behavior defect. WORK.md records limitations.
Draft PR #7 is published; current-head CI and candidate manual review/merge remain
gates. WP-R3 has not started.

## Approved CI-harness repair

Final-head CI `39069933611` passes static checks and 337 pytest cases, but the
unchanged fake-Docker marker/exit test hits a command deadline. The harness replaces
each one-second poll sleep with 50ms, so the controller's tick-count watchdog grants
only about 250ms for a nominal five-second command. The exact CI command is unknown;
an unchanged local 11-case repeat and a 0.1-CPU diagnostic pass, so no local failure
reproduction or frontend defect is claimed.

Candidate explicitly approved a test-only timing repair. Separate the verification
controller's real watchdog clock from accelerated polling, retaining command budgets,
exact-owned process/resource guards, finite outer test timeout and marker semantics.
Stop/reap only its owned watchdog/command children. Add a mock command slower than the
old synthetic budget but below the real limit, plus an actually overdue mock command;
retain fake command context in assertion failures. Independently review and rerun the
focused/full/static/actual isolated-recovery gates and exact-head CI before readiness.
No application policy, production timeout, live outage or deployment setting change.

The 0.6s delayed-valid regression fails against the old tick clock (0.44s total,
deadline during absence preflight), then passes after repair. The focused startup/
controller group passes 18 cases (20.08s), including a genuinely overdue owned client
that aborts before any resource creation/outage. Static/source gates and normal
three-replica smoke also pass. Independent read-only review finds no blocking timer,
exit-status or ownership issue. Signal injection and every descendant-process case
are not established by these tests; full/recovery/exact-head CI gates remain.
