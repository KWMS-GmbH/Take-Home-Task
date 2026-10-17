# WP-1b — charging/session vertical slice

Status: candidate approved D-23–D-27, concrete defaults and this implementation
plan on 2026-10-17. Implementation is authorized; verification/PR merge is separate.
Execution: implemented and locally verified; current publication/merge evidence is
in WORK.md. The candidate still reviews/merges manually before WP-2.
Branch: `codex/session-slice`, fast-forwarded onto manually merged KWMS integration
`2abf0cc` (preserving WP-1a `f81c3cf` and all earlier history).
This plan is disposable; [SPEC.md](../SPEC.md) owns agreed behavior and
[REQUIREMENTS.md](../REQUIREMENTS.md) owns acceptance traceability.

## Goal and boundary

Implement Status/Start/Meter/Stop, durable session data and API-backed session views.
Prove the unchanged happy-path scenario produces one completed 10.0 kWh session.
Retain denied physical starts with their actual authorization snapshot. This is not
a complete-submission claim: the full scenario experience and comprehensive messy
case/failure proof remain WP-2/WP-3.

## Approved policies and defaults

- Candidate approved D-24: recognize Start retries by station/connector/tag/UTC start
  time/starting meter within a retry window and return original ID/auth snapshot.
- Candidate approved D-25: a different Start interrupts the occupied connector's
  previous session without inventing a stop and creates a new attempt/transaction ID.
- Candidate approved D-26: reject bad references without changing sessions; retain
  suspicious evidence with review flags, preserve trustworthy summaries/completion.
- Q-07 design approved as D-23: implement a bounded Authorize-only UI adapter;
  preserve supplied files and deliberate scenario Boots. Document that a valid
  probe still fences an older socket under the same station ID, even after the
  probe closes, but must not itself change durable session lifecycle. Verify the
  exact rationale and checks in SPEC.md.
- Candidate approved the concrete contract in SPEC.md: exact active-session Start
  retries never expire; interrupted/completed matches have a 24-hour grace from
  server receipt of the lifecycle transition. Stop-before-Start retains evidence
  and marks an active session interrupted without establishing trusted completion;
  session list/detail UI now, full scenario UI/correlation still WP-2. The unchanged
  happy-path scenario runs as automated acceptance in this slice.

## Execution

1. Confirm concrete spec/plan review, run the assignment guard and preserve the
   canonical KWMS merge/traceability records in a coherent process/spec commit.
2. Add versioned session/meter/status schema and domain helpers: UTC normalization,
   exact meter arithmetic, quality/review reasons and bounded Start identity. Use a
   station/session index and partial unique active-connector constraint, not an
   in-memory session owner. Keep unknown denied tag text without a tag FK.
3. Extend validated Status/Start/Meter/Stop through the existing station lock,
   generation fencing and atomic audit/replay boundary. Wire Boot interruption and
   late Stop correction immediately; keep network I/O outside database transactions.
4. Add session list/detail HTTP contracts and Flask views; preserve separate
   lifecycle, authorization-at-start and meter quality, UTC event times, and honest
   unknown/provisional versus final energy. Do not claim actual invoicing.
5. Replace only the UI authorization helper use with bounded Authorize-only OCPP;
   retain exchanged frames and the shared-station-ID takeover warning. Do not change
   the supplied helper, scenario files or their intentional Boots.
6. Extend real PostgreSQL, transport, HTTP/UI and supplied-scenario tests: permitted
   and denied physical starts, current permission versus retry snapshot, occupied
   connectors, suspicious meters/times, rejected references, repeated Stop,
   rollback/audit and cross-replica takeover without session lifecycle changes.
7. Run source/static/regression/deployed smoke checks and live browser QA. Perform
   independent code/architecture review, repair findings, update actual evidence,
   then proactively commit/push/open a layered PR for candidate review/manual merge.

## Intended evidence, not results

- Scenario 01 passes protocol and independent API/DB session postconditions:
  correct station/tag, one completion, message-derived UTC start/stop, 10.0 kWh.
- Unknown/blocked Start returns an integer transaction ID and visible non-Accepted
  authorization without being represented as normally billing-eligible.
- Existing authorization/replay/fencing/CSRF/migration regressions still pass.
- Session changes and audit are atomic; no acknowledgement of uncommitted state.
- Session references cannot mutate another station's records.
- UI reads real session data through HTTP, never directly from PostgreSQL.
- D-23 probe emits no Boot; active-session state survives Authorize-only takeover.
  Unchanged scenario 07's single completed 3.0 kWh transaction across sockets is
  mandatory acceptance, not an exemption for the shared-ID caveat. Its automated
  API/DB acceptance was achieved in this slice; full browser experience stays WP-2.
- Exact Start retries, including equivalent timestamp offsets and fresh connection
  IDs, return one original transaction/auth snapshot within the agreed window.
  Test a still-active session older than 24 hours so a retry cannot split it.
  Fresh Start after revocation uses current permission; an occupied connector has
  at most one active record after a distinct Start.
- Backwards meters do not produce negative/fabricated energy; invalid time ordering
  remains visibly untrusted and does not leave a stopped attempt falsely active;
  wrong-station/connector references cannot mutate data.
- Repeat Stop has one completion effect; conflicting evidence is retained/audited
  without replacing its established result.

Planned coherent commits: session/domain migration with tests; HTTP/UI vertical
slice with scenario acceptance; review/deployment repairs and evidence as needed.
Adjust to actual changes and reviewability, not arbitrary line counts.

## Candidate-requested review examples amendment

Scope: explicit opt-in illustrative data using the implemented contract, not WP-2
scenario UI or new domain behavior. Candidate requested this while reviewing PR #2.

1. Add a container-run command using management HTTP plus real OCPP to create seven
   reserved demo stations with distinct energy/duration/state combinations.
2. Verify API postconditions and reuse exact existing examples on rerun. Never delete
   records or silently rewrite modified/partial examples; preserve unrelated data.
3. Add real PostgreSQL/WebSocket integration coverage for all example outcomes,
   repeat invocation and preservation/failure boundaries.
4. Run growing checks/review and live browser QA; amend PR #2 with actual evidence.
   Candidate manual review/merge remains the gate before dependent WP-2 work.

Execution: command ran twice against the deployed stack with seven stable IDs;
all seven detail views were observed in the browser. Static checks, 134-test suite
and deployed three-replica smoke pass. Current review/publication evidence lives
in WORK.md/PR #2; full scenario UI still belongs to WP-2.
