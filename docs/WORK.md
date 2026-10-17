# Work packages, status and risks

This is a lightweight work board, not evidence that application requirements are met.
Behavior lives in [SPEC.md](SPEC.md); source/test coverage in
[REQUIREMENTS.md](REQUIREMENTS.md). Package boundaries are proposed and can be
split when a diff is too large for useful candidate review.

## WP-0 — Project foundation

Status: done. Candidate manually merged PR #1 on 2026-10-17 at 12:08:30 Europe/Berlin.
Merge commit: `90dced97cf7d92fb991122146af0fdb572134429`; local `main` was synchronized
with the fork at that milestone. KWMS is now canonical; see repository integration below.
Branch: `codex/project-foundation`.
PR: [#1 — Establish project foundation and protect the assignment](https://github.com/krebsni/Take-Home-Task/pull/1).

- [x] Inspect original assignment, scenarios, repository and available tooling.
- [x] Discuss workflow, roles, traceability, architecture and failure model.
- [x] Create the authorized fork and preserve upstream/history.
- [x] Draft living spec, requirement matrix, workflow, work board and effort ledger.
- [x] Verify immutable-source guard with positive and negative cases.
- [x] Independent review of documentation against original requirements/conversation.
- [x] Candidate reviews initial spec and work-package boundaries (`lgtm`, 2026-10-17).
- [x] Candidate authorizes public-fork publication (2026-10-17).
- [x] Push the foundation branch and open PR #1 in the authorized fork.
- [x] Complete final workflow-update verification and prepare the PR review handoff.
- [x] Candidate manually reviews/merges PR #1; merge confirmed through GitHub and local synchronization.

Acceptance: original files unchanged; drafts distinguish requirements, approved choices,
open points and evidence; candidate can find how a requirement will be verified.

## WP-1a — Deployable authorization vertical slice

Status: done. Candidate manually merged
[PR #2](https://github.com/krebsni/Take-Home-Task/pull/2) on 2026-10-17 at
13:20:52 Europe/Berlin. Merge commit: `f81c3cf85d677a8255b767770c31d1330b7b3bdc`.
Its tree matches the reviewed head `ea255f1`; local `main` is synchronized.
[Current-head CI passed](https://github.com/krebsni/Take-Home-Task/actions/runs/39027280630).
[Persisted plan](plans/WP-1a.md).
Branch: `codex/authorization-slice`.

- [x] Approve split, empty fresh database, one process/two listeners, replica safety now.
- [x] Prove library public message/schema APIs plus raw-input adapter; pin dependencies.
- [x] Add management API, migrations, atomic audit/replay, fencing and bounded pools.
- [x] Add Flask station/tag/permission UI and real Boot/Authorize check, with CSRF.
- [x] Deploy three service definitions, three backend replicas, secrets/network/volume/health.
- [x] Static checks, real PostgreSQL/transport tests, actual Swarm smoke and live browser QA.
- [x] Independent cross-component code/architecture reviews and findings addressed;
  final integration/browser fixes self-reviewed after review-agent account limit.
- [x] Document evidence, limitations and review focus; no complete-submission claim.
- [x] Publish PR #2 with local verification and review focus; remote CI is visible in Checks.
- [x] Candidate manually reviews and merges; merge confirmed before starting WP-1b.

Acceptance: station-specific current authorization agrees across UI/API/OCPP/DB and
three backend instances. Charging/session behavior is deliberately not claimed here.

## Repository integration — reviewed fork history into KWMS

Status: done. Candidate manually merged
[KWMS PR #1](https://github.com/KWMS-GmbH/Take-Home-Task/pull/1) on 2026-10-17 at
13:38:28 Europe/Berlin, merge `2abf0ccc6f0927dfdd0ed65fa99d521fc492f14b`.
Contributor WRITE access is verified. The dedicated fork branch,
`codex/kwms-integration`, stays at reviewed merge `f81c3cf`; no local WP-1b drafts
or new application changes are included. All nine existing commits and both fork
merge commits are present; original PR records stay linked in the fork.

Verified that `f81c3cf` and `90dced9` remain ancestors of KWMS main, whose tree matches
the imported head exactly: no squash/rebase or application change. Local main and
the existing WP-1b preparation branch fast-forwarded without losing local notes.
`origin` is now KWMS; `fork` retains the public fork. Publication conventions point
future PRs to KWMS with the same candidate-only manual merge gate. No repository
settings or original fork PRs were changed; the merge itself was the candidate's action.

At PR handoff the identical-tree fork CI passed, but no KWMS-side run was visible
and reading its Actions policy returned HTTP 403; that limitation was disclosed,
without inventing a cause. After merge, [KWMS main CI passed](https://github.com/KWMS-GmbH/Take-Home-Task/actions/runs/39029477525)
at `2abf0cc`. Independent description/history review passed
after tightening pending-connection promotion, per-replica process and OCPP-response
persistence wording. Assignment integrity/tree identity were rechecked for the import.

## WP-1b — Charging/session happy-path vertical slice

Status: done. Candidate manually merged
[KWMS PR #2](https://github.com/KWMS-GmbH/Take-Home-Task/pull/2) on 2026-10-17 at
14:41:06 Europe/Berlin, merge `4b2db68627d6acc4c6b31d964a967a3d519978dd`.
Its tree matches reviewed head `461ac24`; local main is synchronized.
[Reviewed-head CI passed](https://github.com/KWMS-GmbH/Take-Home-Task/actions/runs/39032952561).
The [implementation plan](plans/WP-1b.md) and PR retain the approved contract and
layered review guide; the next package still needs its own discussion/spec approval.
Branch: `codex/session-slice`, fast-forwarded onto canonical KWMS main `2abf0cc`.

- [x] Discuss and approve retry identity, occupied connectors, rejected references,
  suspicious evidence and the authorization helper's Boot side effect.
- [x] Review/approve concrete spec/plan: retry window, invalid-time handling, UI boundary.
- [x] Candidate approves Authorize-only UI probe (D-23); exact Boot rationale,
  remaining connection-takeover caveat and original-task compatibility are recorded
  in SPEC.md. D-24–D-27 subsequently close the other policy/default/plan decisions;
  runtime verification is recorded below.
- [x] Status/Start/Meter/Stop handlers, session migrations and API/UI session views.
- [x] One permitted complete 10.0 kWh session through unchanged scenario 01 and API.
- [x] Denied-start outcome and session authorization snapshot, without false billing claims.
- [x] Meaningful session regression tests and independent code/architecture reviews;
  findings repaired and regression-tested. Publication/final evidence follows below.
- [x] Publish locally verified WP-1b PR in KWMS with layered review/presentation guide;
  confirm current-head checks/ready status before the candidate handoff.
- [x] Candidate-requested opt-in varied demo sessions: real HTTP/OCPP, repeat/refusal
  tests and actual visible browser examples; no new lifecycle/quality or billing policy.
- [x] Candidate manually reviews/merges WP-1b; merge/tree/source identity confirmed
  before preparing dependent WP-2 work.

## WP-2 — Session correctness, audit and scenario experience

Status: done. Candidate manually merged
[KWMS PR #3](https://github.com/KWMS-GmbH/Take-Home-Task/pull/3) on 2026-10-17 at
16:11:04 Europe/Berlin, merge `e0b51fc65e65f7304266a03e3090ab2c682e1a3a`.
Its tree matches reviewed head `8b45a72`; local main is synchronized.
[Reviewed-head CI passed](https://github.com/KWMS-GmbH/Take-Home-Task/actions/runs/39035541045).
Candidate approved D-28–D-30: bounded sequential execution,
one UI run per station/start spacing and transient reports with durable sessions/
audit. Concrete contract is in SPEC.md; [plan](plans/WP-2.md) persisted.
Branch: `codex/scenario-slice`. Whole-second rendering/cross-scenario aliasing and
lease expiry without fencing were found during preparation and resolved through the
adapter and backend token checks, preserving originals and established retry rules.

- [x] Review delivered state/retry/meter rules against the remaining scenarios;
  discuss remaining gaps, update spec and persist implementation plan.
- [x] Preserve session rules; add remaining scenario and all-action extra-field wire tests.
- [x] Correlate actual Start response IDs and HTTP API snapshots, never latest-session guesses.
- [x] Execute all eight unchanged fixture files through the bounded public-helper adapter;
  distinguish this from the original CLI entrypoint's clock/coordination behavior.
- [x] Show separate outcomes/checks/exchanges/API session fields and existing history links.
- [x] Test authorization gating, repeats and independent API/DB/audit postconditions.
- [x] Independent code/architecture reviews; findings repaired with regression tests.
- [x] Static checks, 255-test suite, deployed all-eight smoke and live browser QA.
- [x] Publish layered KWMS PR with local verification and review focus; current-head
  remote checks determine ready status before handoff, not a fixed green checkbox.
- [x] Candidate manually reviews and merges; merge/tree/source identity confirmed
  before WP-3 preparation.

Acceptance: scenarios 01–06/08 have correct visible data; required UI fields are present;
case 9 is tested/explained. Connection/concurrency hardening completes in WP-3.

## WP-3 — Three-replica reliability and bounded operation

Status: candidate approved two PRs, isolated fault tests and conservative retention
on 2026-10-17 (D-31/D-32). WP-3a manually merged; WP-3b is in discussion/preparation
and still needs agreement on its concrete safeguards. Extend existing foundations
to charging races, crashes, load, retention and operational recovery.

- [ ] Discuss fencing/takeover and database budget; update spec and persist plan.
- [x] Stress the delivered connector-uniqueness and charging-lock invariants with
  explicit concurrent writers across replicas.
- [ ] Tune existing pool/transport/log and WP-2 simulation bounds; add retention.
- [ ] Extend serialized-migration/readiness/log checks with dependency-failure recovery proof.
- [x] Test takeover across explicit replicas, stale writers, concurrent starts/permissions,
  crash before/after commit, reconnect/replay and three-instance startup.
- [ ] Extend WP-1b's unchanged scenario 07/actual backend=3 continuation proof with
  injected failures/recovery; extend WP-2's delivered all-eight browser proof.
- [ ] Code/architecture review, evidence update and candidate manual PR review.

Acceptance: all original cases plus U-SCALE work with three replicas. If scope/time requires
an omission, obtain a visible scope decision and retain it as incomplete in the matrix.

Approved split: **WP-3a** deterministic race/process-crash correctness, then **WP-3b**
bounded operation/dependency recovery/maintenance. Failures run only on isolated test
resources. Preserve sessions, meter evidence/business audit and current-generation
replay rows; WP-3b may clean only superseded replay rows in bounded batches, with its
concrete safeguards still to be specified. No new deployed service/autoscaling/HA.

### WP-3a — charging races and process-crash correctness

Status: done. Candidate manually merged
[KWMS PR #4](https://github.com/KWMS-GmbH/Take-Home-Task/pull/4) on 2026-10-17 at
16:36:34 Europe/Berlin, merge `3699be98379bd527b29d4d6ed9b876d0871831a3`.
Its tree matches reviewed head `1b9c4bf`; local main is synchronized.
[Reviewed-head CI passed](https://github.com/KWMS-GmbH/Take-Home-Task/actions/runs/39040735481).
Branch `codex/reliability-slice`; production application/schema/deployment unchanged.

- [x] Candidate approves scope/boundaries; record concrete spec and implementation plan.
- [x] Deterministic three-instance Start/connector, permission, Stop and stale-writer races.
- [x] Actual test-child Start/Stop death before commit and after commit/before response.
- [x] Exact API/PG/inbox/audit/retry assertions, bounded cleanup and repeated focused runs.
- [x] Full regression/static/source gates, actual deployed smoke and independent reviews.
- [x] Publish layered KWMS PR with actual local gates and review focus; current-head
  remote checks gate ready status before handoff rather than a fixed green checkbox.
- [x] Candidate manually reviews/merges; merge/tree/source identity confirmed
  before WP-3b preparation.

### WP-3b — bounded operation, recovery and safe maintenance

Discussion/preparation only. No implementation or fault injection yet. Concrete
workload/maintenance/health safeguards need agreement, spec and persisted plan.

Proposals, **not yet approved**:

- Separate restart health (application/listener liveness) from dependency readiness;
  retain generic 503/error behavior while DB is unavailable, without treating a shared
  DB outage as process death. Swarm has one health signal, so document its routing trade-off.
- Test graceful database stop/restart on an exact owned disposable volume-backed DB,
  not the existing tmpfs test DB or live stack. Keep app instances running and prove
  state persistence, failure responses and recovery. Arbitrary network blackholes
  are not covered by server statement timeouts; do not claim a universal wall-time bound.
- Add pool/lock/socket bound checks and a 12-station correctness workload, reporting
  observed timings rather than a throughput SLA. Keep defaults absent contrary evidence.
- Station-specific maintenance command, dry-run by default, explicit apply, seven-day
  minimum age by default and 100 replay rows per batch. Lock station first and compare
  strictly against active generation, preserving current and pending higher generations.
  Preserve business records/existing audit; add one summary audit per applied batch.
  No global sweep or scheduled purge; concrete caps/CLI safeguards still need agreement.

## WP-4 — Independent acceptance and submission

Status: proposed; depends on candidate merge of prior packages.

- [ ] Independently reconcile every original requirement and scenario with actual evidence.
- [ ] Run clean deployment, growing regression suite, restart/persistence checks and browser QA.
- [ ] Finalize README: commands/URL/teardown, nine decisions, scaling, production gaps and omissions.
- [ ] Finalize AI_USAGE and honest effort records; keep original assignment/history intact.
- [ ] Final code/architecture/security review and candidate deployment/code-change walkthrough.
- [ ] Candidate reviews/merges final PR; verify accepted-repository submission before safety target.

## Risks and open actions

At every gate, reassess effort against the brief's timebox. Its fallback priority is
working stack + authorization/happy path, then simulation, then messy cases. Defer
extra visual polish, advanced CI and production observability/upgrade machinery first.
Do not silently drop approved three-replica or audit goals: surface a scope/time
conflict and record the candidate's decision, with remaining requirements incomplete.

| Risk | Current treatment / next action |
| --- | --- |
| Submission access | KWMS WRITE access and candidate manual integration merge are verified. KWMS is canonical; both historical fork merges remain ancestors and KWMS main CI passes. Final assignment completion/submission remains pending. |
| Tooling readiness | Docker Desktop/Engine verified; deployment checks used Engine 29.8.2, 8 CPUs and 8,319,770,624 bytes memory. Single-node Swarm initialized; healthy 3/3 backend, 1/1 frontend, 1/1 db observed. Host dependencies beyond Docker/Git/shell/OpenSSL are unnecessary. |
| Scope/time | User added actual three-replica correctness, audit and browser QA. Keep packages reviewable and record honest omissions/time; six-hour intent still applies. |
| ID reuse ambiguity | D-24/D-27 are approved and implemented: exact active matches stay protected; closed/interrupted matches have a 24-hour post-transition grace. No eternal station/uniqueId key. Repeated Stop protection is independent of that Start window. |
| Input compatibility | Pinned public-schema/message API plus raw-name filter; wire regressions now cover Boot/Authorize and all four charging actions, including collision/nested-meter attempts. Broader fuzzing is not claimed. |
| Fixture timing | Original files remain identical. The UI adapter uses one clock per case and spaces distinct cases by at least 1.1s; the unchanged CLI has its original dynamic clock and no run coordination. |
| Single-node limits | Three backend replicas do not remove the single database/host failure domain. Persistent volume is not a backup. Explain production recovery needs. |
| Observation gaps | Local diagnostic logs can rotate/disappear; no authenticated operator actor or external durable log collector. Durable business audit is separate. |
| Candidate comprehension | Explain material choices and demonstrate failure behavior at each gate; no opaque submission. |
| Authorization probe side effect | D-23 implemented: Authorize-only UI adapter avoids incidental Boot/session interruption; original helper/scenarios stay unchanged. Actual three-replica smoke proves active-session view remains unchanged while old socket is fenced, then completion succeeds on another replica. UI/README show the shared-ID caveat. Broader crash/failure proof remains WP-3. |

## Review evidence

Only actual completed checks belong here. Record the command/scope, result, relevant
commit, findings addressed, and remaining limitations at each gate.

- WP-0: original checkout inspected clean at the baseline commit; no application code existed.
- WP-0: `make verify-assignment` and `sh -n scripts/verify-assignment.sh` pass.
  `make test-assignment-guard` passes 10 isolated regression tests, including staged
  edits hidden by an unchanged working copy, deletions, committed drift and ignored additions.
- WP-0: independent source/spec and workflow/code reviews completed. Fixed missing
  explicit S01 coverage, scenario acceptance details, ambiguous replay-response wording,
  and the staged-drift guard bypass. No remaining review blocker; candidate approved
  the local foundation with `lgtm` on 2026-10-17 and subsequently manually merged PR #1.
- WP-0 handoff correction: the candidate required proactive commit/push/PR ownership
  after the first package was left local. Publication authority is now explicit;
  package readiness and completion/merge conditions are defined in the workflow.
- WP-0 publication verification: assignment integrity, all 10 guard regression tests,
  shell syntax and whitespace checks pass after the workflow correction. Original
  independent reviews cover the foundation/guard; the handoff-only amendments are
  self-reviewed and remain included in the candidate's PR review.
- WP-0 completion: GitHub reports PR #1 `MERGED` at `2026-10-17T10:08:30Z`, merge
  `90dced97cf7d92fb991122146af0fdb572134429`. Its tree matches reviewed head `f911a49`;
  the assignment guard passes on the synchronized tree. New work branch:
  `codex/authorization-slice`.
- WP-1 preparation: Docker Desktop startup completed and `docker info` confirms a
  reachable Engine; no Swarm initialization, image build, dependency installation,
  or application behavior is claimed from this environment check.
- WP-1a: `make check` passes baseline + 10 guard regression cases, dependency consistency,
  Ruff format/lint, strict mypy for application modules and Compose/stack configuration.
  `make test` passes 61 tests against isolated PostgreSQL, including three migration
  processes, empty initial data, permission changes, atomic rollback/audit/replay,
  explicit three-listener fencing, malformed/oversize/binary frames and pool exhaustion.
  Application commit: `fc38541`; deployment/evidence commit: `a85a479`. Final CI state
  belongs to the PR's Checks rather than a self-asserted documentation checkbox.
- WP-1a: `./deploy.sh` brought a fresh single-node Swarm to 3/3 + 1/1 + 1/1 healthy.
  Repeated deployment retained CHECK-CP/CHECK-TAG and CP-REVIEW/TAG-REVIEW. Inspection
  confirms only 8080/9000 published, DB image digest pinned, Docker secrets mounted,
  application images use `app` (UID 10001), and port 9000 `/api/stations` returns 404.
- WP-1a: `make smoke` passes against all three actual Swarm task addresses; a permission
  written to one replica affects another, a valid newer socket fences the old one,
  and the Flask UI executes the unchanged supplied Boot/Authorize helper. `make demo-data`
  ran twice successfully (existing records reused). Test-only Compose resources cleaned up.
- WP-1a browser: created CP-REVIEW/TAG-REVIEW through forms; observed Blocked, allowed→Accepted,
  unknown→Invalid and revoked→Blocked without restart; expanded real exchanged frames.
  Fixed lost result context; selected station/tag now remain visible. Desktop visual QA
  completed; this is live observation, not a checked-in browser regression suite.
- WP-1a security check: pinned tooling `pip-audit --disable-pip --no-deps -r requirements.txt`
  reported no known vulnerabilities on 2026-10-17. This is not a container OS audit or
  a guarantee of security. Operational JSON events inspected without raw tags/frames.
- WP-1a review repairs: corrected async SQLAlchemy extra and secret stdin; replaced
  unchanged local image tags with content-ID-derived tags for rollout; fixed unused
  timeout variables, pool connect/finite bounds, migration
  logging reset, and misleading connectivity/result context. Remaining: sessions/scenarios,
  full crash/load proof, retention/pagination, production auth/TLS/backup/least-privilege
  roles, candidate comprehension review and final upstream submission access.
- WP-1a review-guide amendment: the candidate found the original concise PR description
  insufficient for understanding/review/presentation. PR #2 now uses a layered overview,
  actual business/technical flows, decisions, risks/deferred work, evidence and a
  review/demo/interview route. The default is persisted in AGENTS.md, WORKFLOW.md and
  the GitHub PR template. Spec review: application behavior and original requirements
  are unchanged; this is a documentation/workflow amendment, not dependent WP-1b work.
  Independent review corrected image-tag/logging wording and stale workflow command
  status; 17 repository links, expandable-section balance, whitespace/source guard
  and unchanged-runtime diff checks pass. Current-head CI remains in PR Checks.
- WP-1a completion: GitHub confirms candidate manual merge at `2026-10-17T11:20:52Z`,
  merge `f81c3cf85d677a8255b767770c31d1330b7b3bdc`. Its tree matches reviewed head
  `ea255f1`; CI run `39027280630` passed. Local main fast-forwarded, new preparation
  branch `codex/session-slice` created, and `make verify-assignment` passes.
  This is merge/source evidence, not a new runtime test run.
- WP-1b D-23 source review: candidate approved the Authorize-only proposal and
  requested exact rationale/caveat documentation. BRIEF 89–97 requires Authorize
  before scenarios; 179 allows a tooling adapter; 133–143 retains reboot/two-socket
  requirements. Scenario 07 still requires one transaction started on a and completed
  on b for 3.0 kWh. This compatibility review is not a session test pass; the current
  application is unchanged and source-integrity/whitespace checks are the scoped gate.
- WP-1b plan review: candidate approved the three session policies (D-24–D-26).
  Independent review found that a strict clock cutoff could split an active long
  session, and an acknowledged invalid-time Stop could leave false active state.
  Revised concrete defaults are proposed for candidate review: protect exact active
  retries; apply bounded grace after interruption/completion; retain invalid-time
  Stop evidence and mark active sessions interrupted without a trusted completion.
  No application code changed; these are design findings, not runtime-test evidence.
- WP-1b acceptance: `make check` passes source/10 guard cases, dependency consistency,
  formatting/lint, strict mypy and deployment configs. `make test` passes **131 tests**
  against isolated PostgreSQL, including concurrent migration startup, unchanged
  scenarios 01 and 07 with actual response-ID/API/DB postconditions, denied starts,
  revocation/retry snapshots, grace/long-active protection, connector replacement,
  chronological meter evidence, first Stop completion, rollback/audit and three
  explicit replica listeners. Temporary test resources were cleaned automatically.
- WP-1b deployment: `./deploy.sh` upgraded the existing local Swarm without deleting
  data/secrets and reached healthy 3/3 backend + 1/1 frontend + 1/1 db. Extended
  `make smoke` proves a session starts on task 1, the UI's real Authorize-only probe
  preserves its whole API view while fencing the old socket, then task 3 completes
  the same transaction at 10.0 kWh/60 seconds. API-backed list/detail return HTTP 200.
- WP-1b live browser: observed the displayed shared-ID warning, Accepted result with
  exactly one Authorize send/receive pair (no Boot), station-filtered history and
  completed transaction detail showing 10.0 kWh/60 seconds, UTC times, separate state/
  authorization/quality and explicit eligibility prerequisites. Screenshot saved
  outside the submission tree. This is observed QA, not a checked-in browser suite.
- WP-1b independent reviews: backend/application and frontend/security/schema reviews
  completed with no remaining blocker. Repaired empty-tag identity, Decimal precision/
  exponent overflow, historical readings below baseline/neighbours, Stop earlier
  than observed meter evidence, completed-summary mutation, and invalid-ID 503 mapping.
  Regression tests cover these findings. Full scenario dashboard, other supplied
  scenario acceptance, crash/load/retention and production security remain later work.
- WP-1b publication: four coherent process/domain/UI/evidence commits through
  `1261dad` were pushed and [KWMS PR #2](https://github.com/KWMS-GmbH/Take-Home-Task/pull/2)
  opened in draft while CI runs. A final independent documentation review removed
  stale proposed/unimplemented claims and narrowed later packages to remaining work.
  This handoff-record amendment changes no application behavior; assignment guard
  and whitespace checks pass. The candidate alone reviews/merges; current-head Checks
  determine readiness rather than a permanently green documentation checkbox.
- WP-1b candidate-requested demo amendment: added `make demo-sessions`, not startup
  seeding. It creates seven dedicated station examples with fixed illustrative UTC
  event times using management HTTP plus real OCPP. Two deployed invocations return
  the same seven IDs; existing CHECK/CP records remain intact. Live browser verified
  every detail view, plus blocked station history and its Interrupted/Blocked/
  Suspicious/Not eligible result; screenshot saved outside the repository. No app
  image or domain policy changed. `make check`, **134-test** suite and actual
  three-replica `make smoke` pass. Initial new tests had a mismatched test-listener
  instance ID; corrected the fixture wiring before the passing run. Three focused
  tests cover outcomes/repeatability, unrelated-data preservation, unknown-tag
  collision and refusal to reset modified examples. Full original-scenario UI remains
  WP-2 and these examples do not replace its acceptance proof. Independent
  code/architecture and documentation review found no remaining actionable issue.
- WP-1b completion: GitHub confirms candidate manual merge at `2026-10-17T12:41:06Z`,
  merge `4b2db68`. Its tree matches reviewed head `461ac24`, whose CI run
  `39032952561` passed. Local main fast-forwarded; `make verify-assignment` passes.
  New preparation branch `codex/scenario-slice`; no WP-2 application changes or new
  runtime-test pass are claimed by this merge/source check.
- WP-2 acceptance: `make check` passes assignment/10 guard cases, dependency checks,
  formatting/lint, strict mypy (24 application modules) and deployment configuration.
  `make test` passes **255 tests** against isolated PostgreSQL. All eight original
  fixture files execute through the adapter with distinct actual transaction IDs,
  exact API time/meter/state outcomes and independent database assertions: seven
  completion events, one repeated-Stop audit and no completion for reboot-without-Stop.
  Tests also cover cross-instance two-slot/scope admission, expiry/token fencing,
  denied/permitted-secondary setup, unavailable API data, bounded partial/timeout
  evidence and all-action extra-field collisions. Disposable test resources cleaned up.
- WP-2 initial deployed acceptance: existing Swarm upgraded without data/secret reset;
  three actual backend tasks remain healthy. Extended `make smoke` passes all-eight
  UI protocol/data outcomes with exact API links and denied-primary zero-scenario
  gating, alongside the existing cross-replica takeover/completion checks. The seven
  reserved demo examples remain unchanged. Final reviewed-image redeploy and the
  extended smoke pass again after the reporting repairs.
- WP-2 live browser: observed Accepted/Blocked preflight, eight protocol/data passes,
  all exact session snapshots (including interrupted/provisional reboot, late Stop,
  offsets, suspicious meter and denied charging), and denied-primary 0/8 execution.
  This is observed QA; Flask route tests and stack smoke provide repeatable automation,
  not a claimed checked-in browser-driver suite.
- WP-2 final browser checkpoint: reviewed images show eight protocol/data passes,
  exact distinct IDs and expanded original checks/real exchanges. Execution badges
  explicitly say "Execution completed", separate from charging lifecycle; verbose
  automated checks are collapsible. Overview/detail screenshots saved outside the
  submission tree; the result tab remains available for candidate review.
- WP-2 independent code/architecture reviews found lease-expiry-without-fencing,
  shared-clock/derived-station overlap risks before implementation. Integrated review
  caught incomplete execution marked completed and lost preflight frames on timeout;
  both are repaired with regressions. Final independent review reports no blocker.
  No production authentication, crash/load/retention or complete-submission claim.
- WP-2 publication: process/spec commits followed by coherent admission (`98192b6`),
  adapter/data (`8dbc41b`), browser (`60e6024`) and integrated evidence (`492cd98`)
  commits pushed to canonical KWMS. PR #3 contains the layered review/demo guide.
  Final documentation review narrowed failure wording and removed stale WP-3 browser
  scope; source/whitespace checks pass. Current-head remote CI is a separate gate,
  and only the candidate may merge. No dependent WP-3 implementation started.
- WP-2 completion: GitHub confirms manual merge at `2026-10-17T14:11:04Z`, merge
  `e0b51fc`. Its tree matches reviewed `8b45a72`, whose CI run `39035541045` passed.
  Local main fast-forwarded and assignment integrity passes. New preparation branch:
  `codex/reliability-slice`. This is merge/source evidence, not a fresh full runtime
  suite or approval of unresolved WP-3 mechanics.
- WP-3a preparation: candidate approved D-31/D-32. Independent design review found
  that a response-validator pause precedes inbox insertion, so it cannot prove
  rollback of every atomic effect. Test-local pre-commit instrumentation instead
  flushes and queries target domain/audit/inbox SQL state; post-commit instrumentation
  pauses after service return, before protocol send. No production hook or behavior
  change was needed. The optional pytest-argument test entrypoint preserves the same
  isolated database/container lifecycle and full-suite default.
- WP-3a local gates: focused charging races **12 passed**; process death/database
  guards **10 passed**. After removing a new deprecated Result.tuples conversion,
  two combined repeats each pass **22 tests**, warning-free (4.95s / 5.10s).
  `make check` passes all static/config/source/10 guard checks; `make test` passes
  **277 tests** against isolated PostgreSQL (21.26s). All disposable test resources
  are cleaned automatically. These durations are observations, not performance goals.
- WP-3a deployed gate: normal `make smoke` passes against three actual Swarm task
  addresses, including shared authorization, fencing/continuation, all-eight UI/API
  results and denied-run gating. No fault was injected into the live stack and no
  local redeploy was needed because backend/frontend/schema/config/dependency trees
  are unchanged from merged `e0b51fc`; source/whitespace guards pass. Existing WP-2
  visual QA remains the UI evidence; no new browser behavior or visual pass claimed.
- WP-3a code/architecture review: no remaining blocker. Controlled PG lock waits
  prove real contention, not just unfinished tasks; instances retain legitimate
  generations. Child barriers and independent API/PG reads distinguish rollback
  from committed-but-unreplied work; live receive/socket closure and new-connection
  retries prove one business completion without dead-inbox replay. Race tests use
  three pools/services in one process; crash tests kill one actual child with two
  survivors in the parent, not three killed/raced Swarm processes. Dependency/load/
  retention and host/database HA are not claimed. Candidate manual review remains.
- WP-3a publication: approved process/spec records, deterministic races (`13aed6a`),
  owned-child crash proof (`dd14b07`) and review/evidence (`f0e3958`) pushed to KWMS.
  PR #4 provides outcome, business/technical flows, decisions/risks, exact evidence,
  file route and interview-preparation guide. Independent documentation/description
  review found no actionable inconsistency; original requirement definitions and
  runtime/source trees remain unchanged. Current-head CI and candidate manual merge
  remain separate gates; no WP-3b implementation started.
- WP-3a completion: GitHub confirms manual merge at `2026-10-17T14:36:34Z`, merge
  `3699be9`. Its tree matches reviewed `1b9c4bf`, whose CI run `39040735481` passed.
  Local main fast-forwarded and assignment guard passes. New preparation branch:
  `codex/operations-slice`. Read-only review identified tmpfs restart-test limits and
  active-versus-pending-generation cleanup safety. This is not new runtime-test proof
  or approval of WP-3b's concrete workload, maintenance or health-policy details.
