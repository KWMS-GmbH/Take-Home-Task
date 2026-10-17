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

Status: in progress. Candidate approved D-23–D-27 and the detailed
[implementation plan](plans/WP-1b.md), including refined retry/time defaults and
the UI boundary. Implementation/verification is underway; no acceptance pass yet.
Branch: `codex/session-slice`, fast-forwarded onto canonical KWMS main `2abf0cc`.

- [x] Discuss and approve retry identity, occupied connectors, rejected references,
  suspicious evidence and the authorization helper's Boot side effect.
- [x] Review/approve concrete spec/plan: retry window, invalid-time handling, UI boundary.
- [x] Candidate approves Authorize-only UI probe (D-23); exact Boot rationale,
  remaining connection-takeover caveat and original-task compatibility are recorded
  in SPEC.md. Other session decisions/full-plan approval and runtime tests remain open.
- [ ] Status/Start/Meter/Stop handlers, session migrations and API/UI session views.
- [ ] One permitted complete 10.0 kWh session through unchanged scenario 01 and API.
- [ ] Denied-start outcome and session authorization snapshot, without false billing claims.
- [ ] Meaningful session tests, code/architecture review and manual PR review.

## WP-2 — Session correctness, audit and scenario experience

Status: proposed; depends on candidate merge of WP-1b. Extend approved D-24–D-26
and the reviewed Q-01/Q-03 mechanics with complete scenario proof and audit details.

- [ ] Discuss state transitions, Start retry identity, meter ordering and conflicting data;
  update spec and persist implementation plan.
- [ ] Implement interrupted/late/rejected/suspicious sessions and domain idempotency.
- [ ] Add atomic business audit records and correlated replay/mismatch diagnostics.
- [ ] Run each supplied scenario unchanged; correlate actual transaction IDs through the API.
- [ ] Show all required outcomes/checks/exchanges/session fields and station history in the UI.
- [ ] Test authorization changes, all session edge cases, repeats and their API/DB postconditions.
- [ ] Code/architecture review, live browser QA, evidence update and candidate manual PR review.

Acceptance: scenarios 01–06/08 have correct visible data; required UI fields are present;
case 9 is tested/explained. Connection/concurrency hardening completes in WP-3.

## WP-3 — Three-replica reliability and bounded operation

Status: proposed; depends on WP-2. Extend WP-1a's ownership/pool/migration foundations
to charging races, crashes, load, retention and operational recovery.

- [ ] Discuss fencing/takeover and database budget; update spec and persist plan.
- [ ] Extend verified ownership to connector uniqueness and charging lock order across replicas.
- [ ] Tune existing pool/transport/log bounds; add simulation limits and retention.
- [ ] Extend serialized-migration/readiness/log checks with dependency-failure recovery proof.
- [ ] Test takeover across explicit replicas, stale writers, concurrent starts/permissions,
  crash before/after commit, reconnect/replay and three-instance startup.
- [ ] Complete scenario 07 and prove actual Swarm backend=3 behavior, not just process tests.
- [ ] Code/architecture review, evidence update and candidate manual PR review.

Acceptance: all original cases plus U-SCALE work with three replicas. If scope/time requires
an omission, obtain a visible scope decision and retain it as incomplete in the matrix.

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
| ID reuse ambiguity | D-24's Start fingerprint/window policy is approved; active exact-match protection plus a 24-hour post-transition grace is proposed for review. No eternal station/uniqueId key. Repeated Stop protection is independent of that Start window. |
| Input compatibility | Library schemas reject arbitrary extras; raw-name collisions require adapter proof before choosing dependencies. |
| Fixture timing | Scenario 02 re-renders timestamps. Preserve original files and test data invariants rather than byte identity. |
| Single-node limits | Three backend replicas do not remove the single database/host failure domain. Persistent volume is not a backup. Explain production recovery needs. |
| Observation gaps | Local diagnostic logs can rotate/disappear; no authenticated operator actor or external durable log collector. Durable business audit is separate. |
| Candidate comprehension | Explain material choices and demonstrate failure behavior at each gate; no opaque submission. |
| Authorization probe side effect | Candidate approved D-23: Authorize-only UI adapter avoids incidental Boot/session interruption; original helper/scenarios stay unchanged. A valid probe still supersedes an older same-ID socket (not its durable session); document the caveat and test it. Required scenario 07 remains mandatory; implementation is pending. |

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
