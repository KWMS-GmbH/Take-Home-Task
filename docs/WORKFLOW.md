# Working and verification conventions

## Authority and records

The original assignment and supplied tooling remain the baseline. A KWMS clarification
is recorded with its source and reconciled visibly. Candidate-approved additional
goals and interpretations are explicit; they cannot silently replace original requirements.
The living [spec](SPEC.md) guides code; the [matrix](REQUIREMENTS.md) checks coverage;
the [work board](WORK.md) owns progress/risks. Temporary plans under
`docs/plans/<work-package>.md` may later be discarded; durable decisions stay in the spec.

The candidate approved the initial documentation/spec package with `lgtm` on
2026-10-17 and then explicitly authorized publishing it to the fork. Open design
details still require discussion in their owning work package before implementation.

## Work-package loop

1. Discuss the package's problem, original requirement IDs, options, recommendation,
   failure cases, and acceptance evidence. Use targeted grilling and a short teach-back.
2. Review/update the spec. Label assumptions and proposed details; obtain candidate
   agreement for material changes to architecture, domain behavior, security, dependencies,
   deployment, or scope. An AI recommendation alone is not agreement.
3. Persist a short implementation plan with tasks and checks. Implement a reviewable
   vertical slice across affected layers. Tests accompany the behavior they protect.
4. Run the assignment guard, targeted checks, and the growing regression suite.
   Perform code review and architecture-impact review; resolve findings or disclose them.
5. Update spec change notes, matrix evidence, work status, limitations, AI usage and
   effort records. Explain the resulting behavior and risks to the candidate.
6. Codex owns the handoff: commit the verified work, push the package branch, open
   its PR in the canonical KWMS repository, and give the candidate the link, concise change
   summary, verification, review focus and remaining risks. Do this proactively;
   routine PR publication does not require another approval request. The candidate
   reviews and merges manually. Wait for that merge before dependent implementation.

Package states and exit conditions:

- **In progress:** agreed work, documentation or verification remains.
- **Ready to publish:** scoped acceptance checks pass, relevant regression and
  source-integrity checks pass, independent code/architecture review findings are
  resolved or explicitly accepted, records are current, and material decisions are agreed.
- **Awaiting candidate review/merge:** the branch is pushed and its non-draft PR
  is open; the handoff includes actual evidence and limitations. Pending/failed required
  checks are disclosed and keep the PR in draft rather than being called green.
- **Done:** confirm the candidate's manual merge, record its evidence in the work
  board, then start dependent implementation. Local `lgtm` or an open PR alone is not a merge.

If a handoff condition is blocked, surface it immediately and request the exact
missing action when necessary. Do not wait for the candidate to discover an unpushed
branch or missing PR. On resuming work, check the relevant PR's current state.

If publication is unavailable, prepare the complete local diff and verification record.
The candidate may explicitly authorize continuation from a reviewed local commit on
a stacked local branch; this does not authorize agent merges or waive later manual
PR review/merge. Until that instruction, continue only independent preparation/checks
while the publication route is resolved. No temporary exception is assumed.

Routine mechanical choices inside the agreed spec do not need repeated approval.
Discovery of a conflicting requirement or changed material behavior returns to step 2.
Use independent review agents where useful; they cannot approve decisions for the candidate.

## Git conventions and publication

- Preserve original history. Branch names start with `codex/`.
- Prefer a small number of coherent commits per package, with tests beside changes.
  Do not split by arbitrary line count or create a large unreviewable backend-only phase.
- PRs are self-contained, layered review guides following the description contract
  below. Keep the opening short, not the explanation of material behavior and risk.
- No auto-merge or agent merge. Commits, pushes and PR creation are standing authority
  for agreed work once the readiness gate is met.
- `origin` is now `KWMS-GmbH/Take-Home-Task`; `fork` is the public historical
  `krebsni/Take-Home-Task` fork. The candidate authorized continuing from KWMS after
  manually merging integration PR #1 on 2026-10-17 (`2abf0cc`). Both original fork
  merges/commit identities remain ancestors of KWMS main; do not recreate their PR
  metadata, delete the historical fork, or rewrite that history.
- Target `--repo KWMS-GmbH/Take-Home-Task` explicitly for all subsequent work-package
  PRs. The same proactive publication and candidate-only manual merge gates apply.
  Access/integration is complete, but final assignment verification/submission remains
  WP-4. Do not treat importing the authorization milestone as finishing the take-home.

## PR description contract

The candidate requested this format after PR #2's short handoff note did not provide
enough context for review or presentation preparation. Use
`.github/pull_request_template.md` as the default. The PR is a snapshot/review guide
for its actual diff, not a replacement specification: `SPEC.md` remains the living
behavior contract, and `REQUIREMENTS.md` remains the original-requirement/evidence map.

Each substantive implementation PR covers:

1. **At a glance:** the problem, before/after outcome, where the whole application
   stands now, and the most important exclusion. Make this independently scannable.
2. **Business workflows:** actor/input, principal steps, observable result, and
   important rejected/exceptional paths. Explain domain terms and distinguish a
   protocol acknowledgment from an actual business outcome.
3. **Technical walkthrough:** relevant components, data flow, transaction/state
   boundaries, concurrency/retry behavior and failure handling. Explain important
   mechanisms in plain language; a small diagram is useful when it clarifies the flow.
4. **Decisions and trade-offs:** what was chosen, why, a meaningful alternative or
   cost, and whether it comes from the brief, a candidate addition, or a derived
   implementation choice. Link affected requirement/decision IDs without relying
   on those links to explain the essentials.
5. **Risks, open points and deferred scope:** separate implemented limitations from
   unanswered questions and scheduled future work. State who/which package owns the
   next action. Never describe future session/scaling/security behavior as delivered.
6. **Evidence:** checks actually run, what invariants they prove, links to tests/CI,
   and remaining verification gaps. Separate repeatable automation from observed
   live browser QA; disclose material findings and fixes.
7. **Review and presentation preparation:** suggested file-reading order, highest-risk
   decisions to examine, a brief demo route, and concise answers to likely interview
   questions. Candidate review and understanding remain required, not asserted by AI.

Use expandable details for deeper walkthroughs/rehearsal notes so the opening stays
quick to read. Size the detail to the change: a documentation-only update can simply
say that application behavior is unchanged; do not fill sections with boilerplate.
Links supplement a self-contained explanation rather than replacing it. Use actual
repository links and keep the description aligned with the current PR head before
every handoff, including amendments. No description change alters the immutable brief
or authorizes a new behavior/assumption. Manual candidate merge remains the gate.

## Verification strategy

| Layer | Valuable checks | When |
| --- | --- | --- |
| Source integrity | `make verify-assignment`, source/spec traceability review | Every gate |
| Static | Formatting/lint, type checks and build/config validation with pinned container tools | Once tooling exists; every code PR |
| Unit | Authorization decisions, time normalization, energy/quality and lifecycle rules | With the owning behavior |
| Integration | Real PostgreSQL, HTTP contracts, OCPP framing/errors, atomic audit/replay, permission changes | Every affected package |
| Scenario | Unmodified supplied runner plus independent API/DB postcondition assertions | Once the vertical slice supports scenarios |
| Replica/failure | Explicit replica routing, takeover/races, commit-before-reply failure, serialized migrations | WP-1a ownership/migration foundations; WP-3 extends charging/crash/load proof |
| Deployment | Image builds, stack validation, clean single-node Swarm, startup ordering, secrets/network/volume/health | Deployment-affecting changes; final gate |
| Browser | Live UI operation, auth gating, permission controls, actual scenario session results and visual clarity | UI milestones and final gate |

The provided runner accepts some wrong behaviors as warnings; its green result is
insufficient proof. Live browser QA complements repeatable regression tests and must
be reported as observed evidence, not claimed as a checked-in browser test suite.
No arbitrary test-count/coverage-percentage target. Prioritize meaningful invariants.

Only commands that exist are advertised as runnable. WP-1a established canonical
container entrypoints; later packages extend the meaningful scenario/failure coverage.
The repeatable gates now are:

```sh
make verify-assignment
make check
make test
make smoke                     # requires an already deployed stack
```

Deploy with `./deploy.sh` after one-time Swarm initialization; `make teardown` removes
the project stack but preserves its database volume/secrets. CI runs check/test, creates
a fresh Swarm and deploys/smoke-tests all three backend replicas.
`make test` includes pytest and a separate owned, disposable volume-backed PostgreSQL
stop/restart check; `make test-recovery` runs only that gate. Neither injects faults
into the deployed stack, and cleanup apply is tested only against disposable test data.

The guard itself needs only Git and a POSIX shell. Its 10 regression tests run inside
`make check`/`make test`; `make test-assignment-guard` is also an optional host-Python
stdlib shortcut. CI checkouts retain the original baseline commit, not only HEAD.

## Time and handoff

Keep candidate-reported active effort and observed agent work separately in
[TIME_LOG.md](TIME_LOG.md); note estimates and parallel activity. Do not infer voice
duration from transcripts or represent only human interaction as the total project time.
Honor the brief's six-hour intent and review scope as work grows.

Safety target: Sunday 2026-10-18 18:00 Europe/Berlin. Submission deadline: 21:00.
Before the target, verify the final commit reached the accepted submission repository,
review the README and AI_USAGE, record omitted work, and rehearse deployment plus a small change.
