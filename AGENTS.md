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
- The fork is public. Publication of solution work is on hold until the candidate
  confirms the submission/publication route with KWMS. Keep work local meanwhile;
  do not push, create a public PR, or change repository visibility on assumption.
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
