# Assignment baseline and traceability

Baseline: `1b8ccff372d81691f50480a4d8d4e1f17a839095` in the preserved Git history.
The authoritative assignment is [take-home/BRIEF.md](../take-home/BRIEF.md).
The original [root README](../README.md) supplies the Sunday 21:00 submission deadline;
its original text remains retrievable with `git show <baseline>:README.md` when the
delivery README evolves. The candidate confirmed October 18, 2026, 21:00
Europe/Berlin and an 18:00 safety target.

`make verify-assignment` verifies the entire original `take-home/` tree against
the baseline, including both replay scripts and all scenarios. Do not change the
baseline or originals to make a check pass. Report an intentional upstream change
for reconciliation with the candidate first.

## Reading the matrix

- `R-*`: original assignment requirement; source `B:n` means BRIEF line n.
- `U-*`: additional requirement approved by the candidate in discussion.
- `D-*` and `Q-*`: decisions and open questions in [SPEC.md](SPEC.md).
- WP-1a evidence below covers authorization/deployment only. `Partial` explicitly
  leaves remaining parts unverified; `—` means pending. Planned checks are not passes.
- Scenario sources `S01`–`S08` are the numbered JSON files under
  [take-home/scenarios](../take-home/scenarios/); each file's `check` lines are part
  of the baseline. The supplied runner tests protocol, not session correctness.

## Original requirements

| ID | Required outcome | Source | Planned verification / package | Evidence |
| --- | --- | --- | --- | --- |
| R-ARCH | Flask frontend, backend and PostgreSQL as three Swarm services; frontend uses HTTP API, never DB | B:22–38,56–60,81 | Stack inspection; frontend/API integration / WP-1 | WP-1a: stack.yml; frontend boundary tests; actual Swarm 3/3+1/1+1/1 healthy |
| R-WS | Port 9000 `/ocpp/<charge_point_id>`, negotiated `ocpp1.6` | B:42–44 | WebSocket handshake/path tests / WP-1 | tests/test_protocol.py; actual Swarm smoke; wrong/missing subprotocol rejected |
| R-BOOT | Boot fields and exact response contract, UTC current time and interval 300 | B:49 | Protocol contract test / WP-1 | tests/test_replica_protocol.py; supplied helper exercised in UI/smoke |
| R-AUTHORIZE | Authorize input and `idTagInfo.status` response | B:50 | Authorization/protocol tests / WP-1 | tests/test_backend.py, test_protocol.py, test_replica_protocol.py; live browser three outcomes |
| R-STATUS | StatusNotification listed fields; `{}` response | B:51 | Protocol/persistence test / WP-1 | — |
| R-START | Start fields; integer transaction ID plus authorization outcome | B:52,75–77 | Start/denied-start integration tests / WP-1,WP-2 | — |
| R-METER | MeterValues listed fields, string Wh samples, `{}` response | B:53 | Protocol and session assertions / WP-1,WP-2 | — |
| R-STOP | Stop fields, optional reason, Accepted `idTagInfo` result | B:54 | Stop integration test / WP-1 | — |
| R-EXTRAS | Only listed fields matter; ignore remaining fields | B:45 | Nested extra/collision compatibility tests / WP-1 | Partial: Boot/Authorize wire tests and nested meter filtering unit test; four later actions pending |
| R-PERSIST | PostgreSQL stores stations, tags, permissions and sessions | B:56 | Real-PostgreSQL integration/restart tests / WP-1 | Partial: stations/tags/permissions/audit/inbox migrations + PG tests; sessions pending |
| R-API | API lists sessions per station, returns one session's times/energy/status, manages stations/tags/permissions | B:57–60 | HTTP contract/integration tests / WP-1 | Partial: management HTTP contracts in tests/test_backend.py; sessions pending |
| R-PERMISSIONS | Unknown→Invalid; known/unpermitted→Blocked; permitted→Accepted; station-specific and changes effective without restart | B:64–74 | Authorization matrix and permission-toggle tests / WP-1 | Partial: Authorize verified in PG/three replicas/browser; Start pending |
| R-UI-MANAGE | Stations appear on connect; manual pre-creation, tag creation and station/tag permission toggles | B:86–88 | HTTP/UI integration; live browser QA / WP-1 | tests/test_frontend.py and raw-connect PG test; live CP-REVIEW/TAG-REVIEW form/toggle walkthrough |
| R-UI-GATE | Simulator chooses station/tag, shows Authorize result and runs only if Accepted; tester chooses separate not-permitted tag | B:89–92,181–182 | Accepted/Blocked/Invalid browser flows / WP-2 | Partial: authorization probe only, three outcomes/context observed; scenario gating and second tag pending |
| R-UI-RESULTS | Per scenario: protocol outcome, collapsible exchanges, original checks, actual sessions from API with status/start/stop/duration/energy | B:93–97 | Scenario/API postconditions and browser QA / WP-2 | — |
| R-UI-SESSIONS | Sessions list per station; grading possible without terminal or DB access | B:95–98 | Browser walkthrough / WP-2 | — |
| R-STACK | One stack file plus build/deploy entrypoint; fresh single-node `docker swarm init && ./deploy.sh` | B:102–108,193–195 | Fresh Swarm deployment smoke / WP-1,WP-4 | stack.yml/deploy.sh; fresh Engine 29.8.2 Swarm deployment + repeated redeploy pass |
| R-IMAGES | Own small Dockerfiles; non-root where reasonable | B:111 | Build/image runtime checks / WP-1 | Own multistage Dockerfile; backend/frontend built, image user app (UID 10001) inspected |
| R-NETWORK | Overlay networking; publish only required ingress | B:112 | Stack/port inspection and reachability tests / WP-1,WP-4 | Actual service inspection: 8080/9000 only, DB unpublished; 9000/api/stations=404; overlay smoke |
| R-SECRET | DB password supplied by Docker secret, not plaintext environment | B:113 | Stack inspection and secret-backed deployment / WP-1 | stack.yml secret/file configuration; actual mounted-secret healthy deploy; no secret values recorded |
| R-VOLUME | Persistent database volume | B:114 | Restart/redeploy persistence smoke / WP-1,WP-4 | db_data volume declared; UI/API records survive application redeploy; database-restart proof extends in WP-4 |
| R-HEALTH | Health checks, restart policy and arbitrary startup order; no reliance on depends_on | B:115–117 | Startup/dependency failure tests / WP-1,WP-4 | Fresh unordered Swarm startup healthy, retry/readiness tests; full dependency recovery proof pending |
| R-HAPPY | One completed session for station/tag, 10.0 kWh, start about 60 minutes ago and stop about 5 minutes ago from message timestamps | S01 | Unchanged replay plus API session assertions / WP-1,WP-2 | — |
| R-EDGE-1 | Duplicates do not repeat mutations; duplicate Stop leaves one completion, 10.0 kWh and no duplicate stop-event rows | B:131–132; S02 | Replay plus API/DB event assertions / WP-2 | — |
| R-EDGE-2 | Reboot without Stop does not leave session active forever; last 7000 Wh reading yields provisional 6.0 kWh or energy is explicitly unknown | B:133–135; S03 | Reboot/meter/session tests / WP-2 | — |
| R-EDGE-3 | Late Stop uses real event time, repairs an interrupted session, and yields about 1-hour duration and 4.0 kWh | B:136–137; S04 | Historical timestamp/late correction test / WP-2 | — |
| R-EDGE-4 | Offset timestamps represent correct UTC instants, 45-minute duration and 8.0 kWh | B:138–139; S05 | UTC/unit tests and replay assertions / WP-2 | — |
| R-EDGE-5 | Backwards meter never produces negative energy; behavior explained | B:140–141; S06 | Meter-quality tests and visible review indication / WP-2 | — |
| R-EDGE-6 | Two same-station connections handled; transaction starts on one and stops on the other as one completed 3.0 kWh session; policy documented | B:142–143; S07 | Connection takeover/protocol tests / WP-3 | — |
| R-EDGE-7 | Invalid JSON/actions/fields cannot crash service or harm later valid calls/other stations; frontend remains healthy | B:144–145; S07 | Strict malformed-input/isolation and health tests / WP-1,WP-3 | Partial: malformed-then-valid, fields/actions, binary/oversize, other-station and live health checks pass; full S07 pending |
| R-EDGE-8 | Both Authorize and Start for blocked/unknown tag return non-Accepted; Start still returns ID; attempt not a normal billable session | B:146–147; S08 | Denied authorization/start/session/API/UI assertions / WP-2 | — |
| R-EDGE-9 | Revocation mid-session behavior and production intent explained; remote stopping optional | B:148–150 | Permission snapshot tests and README / WP-2 | — |
| R-TESTS | Meaningful automated tests for authorization and session correctness at least | B:196–198 | Unit plus PostgreSQL/protocol regression suite / all packages | Partial: 61 guard/auth/API/UI/PG/protocol tests pass; session correctness pending |
| R-DOC-DEPLOY | README: deploy, open URL, tests and teardown | B:195,199–200 | Documented command walkthrough / WP-4 | — |
| R-DOC-DECISIONS | README: decisions and rationale for all nine messy cases | B:201 | Matrix/spec/README review / WP-4 | — |
| R-DOC-SCALE | README answers behavior of backend=3 and changes needed | B:118–120,202 | Tested scaling account and limitations / WP-3,WP-4 | — |
| R-DOC-LIMITS | Known limitations and next steps, including production frontend security | B:79,203 | Architecture/security review / WP-4 | — |
| R-AI | AI_USAGE names tools/uses and a real disagreement/correction | B:204–205 | Candidate review / WP-0,WP-4 | Initial account in AI_USAGE.md; final review pending |
| R-HISTORY | Preserve history and make small, honest commits | B:207 | Git/PR review / all packages | Original history preserved |
| R-TIME | About six-hour timebox; disclose unfinished work; understand submission for live change | B:9–16,222–230 | Honest effort ledger, scope review and walkthrough / all packages | Ledger started; prior durations unknown |
| R-SUBMIT | Push work to the requested repository before Sunday 21:00 | Original README:3 | Candidate resolves access; verify submission before cutoff / WP-4 | KWMS access still READ; public fork created |

## Candidate-approved additions

| ID | Additional goal | Planned verification / package | Evidence |
| --- | --- | --- | --- |
| U-SCALE | Correct operation with three backend replicas, including cross-replica races and process failure | Deterministic three-instance integration and actual Swarm smoke / WP-3 | Partial: three-process migrations, explicit three-listener and actual Swarm auth/fencing pass; charging races/crashes pending |
| U-TRACE | Immutable original files and continuous requirement-to-test traceability | Baseline guard; matrix review at every gate / WP-0 onward | scripts/verify-assignment.sh; 10 regression cases in tests/test_assignment_guard.py pass; matrix independently reviewed |
| U-PR | Several reviewable vertical-slice PRs; Codex owns proactive verified commit/push/PR handoff, candidate manually reviews and merges each | PR descriptions, review gates and preserved commits / all packages | Foundation PR #1 merged (90dced97); [WP-1a PR #2](https://github.com/krebsni/Take-Home-Task/pull/2) published; candidate manual review/merge pending |
| U-SPEC | Discuss each package, maintain living spec, persist disposable plans, code/architecture review and growing meaningful tests | Package checklists and recorded evidence / all packages | Foundation approved with lgtm; owning-package open details remain explicit |
| U-AUDIT | Durable business audit history plus correlated, bounded operational logging | Atomic-audit and duplicate/replay tests; log inspection / WP-2,WP-3 | Partial: atomic auth/management/replay/conflict/stale audit tested; sanitized JSON/log rotation inspected; session audit/retention pending |
| U-AUTOMATE | Container-based reproducible tooling, automated unit/integration/full-stack checks and live browser QA | Documented one-command checks and browser evidence / WP-1 onward | make check/test/smoke pass for WP-1a; live browser three-outcome/form/frame QA recorded in WORK.md; growing coverage remains |
| U-TRACK | Submit lightweight Markdown work items, decisions, risks and honest effort records | Documentation review / all packages | Foundation records reviewed; active effort durations remain unreported |
| U-LEARN | Candidate understands material choices, risks and failure behavior | Milestone walkthroughs/teach-back / all packages | Architecture explained in discussion; implementation walkthrough/teach-back pending |
| U-CUTOFF | Safety target: 2026-10-18 18:00 Europe/Berlin; deadline 21:00 | Final submission checkpoint / WP-4 | Agreed; not a scheduled automation |

## Review rule

At each gate inspect the original-source guard, requirement coverage, open questions,
actual test evidence, and known limitations. Mark a requirement verified only when
its evidence demonstrates the required behavior. Do not turn an intended design,
green protocol replay, or documentation checkbox into a correctness claim.
