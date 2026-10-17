# Project working agreement

## Assignment and authority

- Read `docs/REQUIREMENTS.md`, `docs/SPEC.md`, and the relevant work package in
  `docs/WORK.md` before implementation. `docs/WORKFLOW.md` defines the review gates.
- The original assignment is pinned to commit
  `1b8ccff372d81691f50480a4d8d4e1f17a839095`. Preserve `take-home/` byte-for-byte,
  including both replay scripts and all supplied scenarios. Run
  `make verify-assignment` at each review gate.
- Original requirements take precedence over our derived spec and temporary plans.
  Surface conflicts and upstream changes; never silently reinterpret or expand them.
- Distinguish original requirements (`R-*`), user-approved additions (`U-*`),
  design decisions (`D-*`), and open questions (`Q-*`). A proposed detail is not approval.

## Collaboration

- Keep responses concise, explain the important reasoning, and progressively expand
  when needed. Keep the candidate informed about discoveries and trade-offs.
- Before each work package: targeted brainstorming/grilling, a reviewable spec
  update, and a persisted implementation plan. Material decisions require the
  candidate's agreement. Mechanical implementation within an agreed spec can proceed.
- Every implementation PR includes a spec review/update, even if its note is that
  behavior is unchanged. Update requirements evidence and work status honestly.
- Explain architecture, data integrity, security, failure modes, and verification.
  Use a short walkthrough/teach-back at milestones; the candidate must understand
  and agree with the submitted code.

## Git and review

- Use `codex/` branches and coherent, reasonably sized commits with relevant tests.
- Use vertical-slice PRs that can be reviewed independently. The candidate reviews
  and merges every PR manually. Never merge or enable auto-merge on their behalf.
- Treat each PR description as a layered review/presentation guide, not a changelog.
  Start with the outcome and current project state; explain important business and
  technical flows, decisions/trade-offs, risks, open/deferred work, actual evidence
  and a suggested review route. Use `.github/pull_request_template.md` and the PR
  description contract in `docs/WORKFLOW.md`; keep it current as the PR changes.
- The candidate authorized publishing project work to the public
  `krebsni/Take-Home-Task` fork on 2026-10-17. For every agreed package, proactively
  finish checks/reviews, commit, push, open the PR in that fork, and hand over its
  link, verification, review focus and remaining risks. Do not wait for the candidate
  to ask for a PR or ask again for routine publication already authorized.
- Use an explicit repository target for GitHub commands. Access/final submission
  to KWMS remains a separate open issue; fork publication does not resolve it.
- A package is review-ready after its scoped exit checks pass and the PR is ready
  for review; it is done only after the candidate's manual merge is confirmed.
  Surface any handoff blocker promptly with the exact missing decision/action.
- Wait for the candidate's merge before dependent implementation unless they
  explicitly authorize continuation from an exact reviewed local commit.
- Never send messages to KWMS or other people without an explicit instruction.
- Preserve unrelated user work and the original Git history.

## Implementation and verification

- Deliver the three specified Swarm services. Three backend replicas are an
  explicit user addition; distinguish this from the original README scaling question.
- Container tooling will be canonical. Pin runtime/dependency versions when the
  compatibility work establishes them; host Python must not be a deployment prerequisite.
- Run targeted tests, the growing regression suite, code review, and architecture
  impact review at each work-package gate. Record what actually ran and any limitations.
- Use durable PostgreSQL state, short transactions, bounded resources, and explicit
  error handling. Do not acknowledge mutations that were not committed.
- Keep tests meaningful: authorization, session data, protocol, concurrency,
  deployment, and browser behavior. Never equate a protocol pass with correct data.
- Do not add services, broad frameworks, or production claims merely to demonstrate
  architecture. Record deferred concerns and explain the trade-off.

## Process records

- `docs/SPEC.md`: living behavior contract, decisions, and open questions.
- `docs/REQUIREMENTS.md`: immutable-source references and verification traceability.
- `docs/WORK.md`: work packages, status, risks, and review evidence.
- `docs/WORKFLOW.md`: working conventions and verification gates.
- `docs/TIME_LOG.md`: approximate candidate effort and observed agent work, without
  invented voice durations or claims of a six-hour total that the records do not support.
- `AI_USAGE.md`: concise, factual account of assistance and actual corrections.
