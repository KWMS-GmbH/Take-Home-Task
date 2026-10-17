# WP-2 — scenario experience and full session acceptance

Status: candidate approved D-28–D-30 scope on 2026-10-17. Concrete implementation
contract is in SPEC.md; these tasks are disposable and verification remains separate.
Branch: `codex/scenario-slice`, based on manually merged WP-1b `4b2db68`.

## Outcome and boundaries

An operator can choose station/tags, explicitly accept Boot side effects, authorize,
run all eight original scenarios and inspect separate protocol/data results plus
the exact API sessions in the browser. No original-file edits, permission shortcuts,
background-job framework or durable report archive. Existing session rules stay intact.

## Implementation

1. Review concrete contract for source compatibility, lease fencing, timestamp
   isolation, bounded work and honest failure/result semantics before code.
   Review corrected secondary-tag gating to explicit Blocked/Invalid and included
   scenario 07's exact derived `-OTHER` station in token scope/overlap exclusion;
   validate a 58-character simulation base ID before any network mutation.
2. Add one migration/model for two coordination slots, short acquire/release HTTP
   APIs and optional simulation-token validation in station-locked registration/call
   handling. Ordinary OCPP stays unchanged. Test cross-instance admission, expiry,
   wrong/old-token rejection and short transaction/resource behavior.
3. Add fixed-fixture adapter using supplied public helpers, isolated rendering
   instant per scenario, spacing, bounded transport/collector and total deadline.
   Preserve partial results and mark remaining cases not run. No shared monkeypatch.
4. Correlate exact Start responses; fetch HTTP detail and snapshot after each case.
   Add a small expectation manifest; distinguish protocol/data failures and unknown
   evidence. Test all original scenarios against real PG with independent audit/
   count/time/meter assertions and extra-field collision wire regressions.
5. Add CSRF-protected Flask simulation form/results route, input/response validation,
   explicit setup/Boot warnings, secondary-tag checks, escaped collapsible frames,
   original check lines and linked API session snapshots. Keep reports request-local.
6. Run static/container tests, deploy/smoke, live browser authorization/complete/
   partial/denied flows and independent cross-component code/architecture reviews.
   Repair findings with tests. Update matrix, spec, work/AI/effort evidence honestly.
7. Proactively commit/push/open a layered KWMS PR, keep draft for pending checks,
   then hand over after current-head CI. Candidate manually reviews/merges; no WP-3
   implementation before that merge.

## Intended evidence, not results

- Source guard unchanged; all existing 134 tests remain meaningful and pass.
- Eight cases produce their expected API data and durable audit effects, not merely
  a green replay. Distinct scenario IDs are not accidentally deduplicated.
- Denied/unavailable primary authorization and permitted secondary tag run no cases;
  no permission mutation occurs. Missing API data cannot be reported as a data pass.
- Same-station/global admission works across independent service instances; stale
  lease token cannot regain mutation rights on a newer socket; expiry frees capacity.
- Deadline/protocol/API failures retain bounded partial evidence, close connections,
  release/recover leases and honestly retain committed charging records.
- Browser shows all required fields/checks/exchanges and exact session links through
  HTTP only; results snapshots clearly differ from current durable history.

Planned coherent commits: approved spec/plan; admission/fencing with tests;
scenario adapter/data acceptance; browser slice; integrated verification/review docs.

## Execution checkpoint

Implemented without changing original files or existing session policy. `make check`
and the 255-test suite pass; all eight helper-adapter scenarios have independent
API/DB/audit evidence. Actual three-replica UI smoke and positive/denied live browser
flows pass. Final review repaired incomplete-case classification and preflight timeout
evidence; no remaining review blocker. Final image/browser checkpoint, publication,
current-head CI and candidate manual review are recorded in WORK.md as they occur.
