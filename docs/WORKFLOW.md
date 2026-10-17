# Working and verification conventions

## Authority and records

The original assignment and supplied tooling remain the baseline. A KWMS clarification
is recorded with its source and reconciled visibly. Candidate-approved additional
goals and interpretations are explicit; they cannot silently replace original requirements.
The living [spec](SPEC.md) guides code; the [matrix](REQUIREMENTS.md) checks coverage;
the [work board](WORK.md) owns progress/risks. Temporary plans under
`docs/plans/<work-package>.md` may later be discarded; durable decisions stay in the spec.

The initial documentation package is itself a review draft. No application
implementation starts until its spec and first package's material choices are reviewed.

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
6. Prepare a PR with a concise description. The candidate reviews and merges manually.
   Normally, wait for that merge before building a dependent slice on top of it.

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
- PRs explain the concrete change, decisions/risks, actual verification and limitations.
  Link relevant work/requirement IDs. Keep routine descriptions short.
- No auto-merge or agent merge. Local commits are allowed within approved work.
- `origin` is the public `krebsni/Take-Home-Task` fork; `upstream` is KWMS.
  Publication is on hold until the candidate confirms the route; never assume the
  fork will become private when upstream does. The candidate contacts KWMS.

## Verification strategy

| Layer | Valuable checks | When |
| --- | --- | --- |
| Source integrity | `make verify-assignment`, source/spec traceability review | Every gate |
| Static | Formatting/lint, type checks and build/config validation with pinned container tools | Once tooling exists; every code PR |
| Unit | Authorization decisions, time normalization, energy/quality and lifecycle rules | With the owning behavior |
| Integration | Real PostgreSQL, HTTP contracts, OCPP framing/errors, atomic audit/replay, permission changes | Every affected package |
| Scenario | Unmodified supplied runner plus independent API/DB postcondition assertions | Once the vertical slice supports scenarios |
| Replica/failure | Explicit routing to different replicas, takeover/races, commit-before-reply failure, serialized migrations | WP-3 and later regression |
| Deployment | Image builds, stack validation, clean single-node Swarm, startup ordering, secrets/network/volume/health | Deployment-affecting changes; final gate |
| Browser | Live UI operation, auth gating, permission controls, actual scenario session results and visual clarity | UI milestones and final gate |

The provided runner accepts some wrong behaviors as warnings; its green result is
insufficient proof. Live browser QA complements repeatable regression tests and must
be reported as observed evidence, not claimed as a checked-in browser test suite.
No arbitrary test-count/coverage-percentage target. Prioritize meaningful invariants.

Only commands that exist are advertised as runnable. WP-1 will establish container
entrypoints for checks/tests; later packages extend them with scenario and stack checks.
Automate CI where it proves behavior without adding unnecessary maintenance.

Bootstrap checks available now:

```sh
make verify-assignment
make test-assignment-guard
```

The guard itself needs only Git and a POSIX shell. Its regression tests additionally
use Python's standard library and isolated temporary local Git clones. Run them with
host `python3` during this bootstrap; WP-1 will include them in pinned container tooling.
CI checkouts must retain the original baseline commit rather than fetching only HEAD.

## Time and handoff

Keep candidate-reported active effort and observed agent work separately in
[TIME_LOG.md](TIME_LOG.md); note estimates and parallel activity. Do not infer voice
duration from transcripts or represent only human interaction as the total project time.
Honor the brief's six-hour intent and review scope as work grows.

Safety target: Sunday 2026-10-18 18:00 Europe/Berlin. Submission deadline: 21:00.
Before the target, verify the final commit reached the accepted submission repository,
review the README and AI_USAGE, record omitted work, and rehearse deployment plus a small change.
