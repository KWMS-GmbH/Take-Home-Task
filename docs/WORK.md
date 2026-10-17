# Work packages, status and risks

This is a lightweight work board, not evidence that application requirements are met.
Behavior lives in [SPEC.md](SPEC.md); source/test coverage in
[REQUIREMENTS.md](REQUIREMENTS.md). Package boundaries are proposed and can be
split when a diff is too large for useful candidate review.

## WP-0 — Project foundation

Status: checks/reviews complete; PR #1 open for candidate handoff.
Candidate approved the local foundation and authorized publication. Manual merge pending.
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
- [ ] Candidate manually reviews/merges PR #1; confirm merge before marking WP-0 done.

Acceptance: original files unchanged; drafts distinguish requirements, approved choices,
open points and evidence; candidate can find how a requirement will be verified.

## WP-1 — Deployable authorization and charging vertical slice

Status: proposed; discuss/resolve Q-02 before implementation. No dependency pins yet.

- [ ] Create the persisted package plan after targeted design discussion.
- [ ] Prove OCPP library + raw-input adapter or justify the narrow fallback.
- [ ] Establish pinned container tooling, database schema/migrations, backend API/OCPP,
  and minimal Flask station/tag/permission/session screens.
- [ ] Complete a permitted happy-path charge from simulator/transport through PostgreSQL
  to browser-visible session data; show rejected authorization clearly.
- [ ] Deploy the three services with secret, overlay network, volume, health and startup retries.
- [ ] Add static checks, real-PostgreSQL authorization/happy-path tests and CI baseline.
- [ ] Code/architecture review, evidence update, candidate walkthrough and manual PR review.

Acceptance: working stack, correct station-specific permissions and happy-path session;
management/API/browser layers agree. This is a milestone, not the final submission.

## WP-2 — Session correctness, audit and scenario experience

Status: proposed; depends on candidate merge of WP-1. Resolve Q-01/Q-03 and audit details.

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

Status: proposed; depends on WP-2. Resolve connection promotion, limits and retention.

- [ ] Discuss fencing/takeover and database budget; update spec and persist plan.
- [ ] Enforce ownership, connector uniqueness and consistent database lock order across replicas.
- [ ] Bound pools, waits, message/connection/simulation work and diagnostic log volume.
- [ ] Serialize migrations, implement dependency failure behavior and log collection/rotation.
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
| Submission access | KWMS repository last reported READ; no pending invitation found. Candidate contacts KWMS about final submission. Public-fork project PRs are separately authorized and may proceed. |
| Tooling readiness | Docker CLI installed but daemon was unavailable at first inspection. Start/check Docker during WP-1; do not claim deployment verification yet. |
| Scope/time | User added actual three-replica correctness, audit and browser QA. Keep packages reviewable and record honest omissions/time; six-hour intent still applies. |
| ID reuse ambiguity | No eternal station/uniqueId key. Start retry identity/retention remains Q-01; repeated Stop is independently safe by transaction ID. |
| Input compatibility | Library schemas reject arbitrary extras; raw-name collisions require adapter proof before choosing dependencies. |
| Fixture timing | Scenario 02 re-renders timestamps. Preserve original files and test data invariants rather than byte identity. |
| Single-node limits | Three backend replicas do not remove the single database/host failure domain. Persistent volume is not a backup. Explain production recovery needs. |
| Observation gaps | Local diagnostic logs can rotate/disappear; no authenticated operator actor or external durable log collector. Durable business audit is separate. |
| Candidate comprehension | Explain material choices and demonstrate failure behavior at each gate; no opaque submission. |

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
  the local foundation with `lgtm` on 2026-10-17. PR review/manual merge remains pending.
- WP-0 handoff correction: the candidate required proactive commit/push/PR ownership
  after the first package was left local. Publication authority is now explicit;
  package readiness and completion/merge conditions are defined in the workflow.
- WP-0 publication verification: assignment integrity, all 10 guard regression tests,
  shell syntax and whitespace checks pass after the workflow correction. Original
  independent reviews cover the foundation/guard; the handoff-only amendments are
  self-reviewed and remain included in the candidate's PR review.
