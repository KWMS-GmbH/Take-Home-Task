# WP-R1 — Backend readability without behavior changes

Candidate approved D-36/D-37 and independent review. Baseline: manually merged
PR #5 `9c66794`, same tree as reviewed `6e90db3`, with green reviewed/main CI.
Branch `codex/backend-readability`. SPEC.md owns the unchanged behavior contract.

1. Preserve baseline; inspect backend entry points and test/crash-hook dependencies.
   Record the approved contract, WP-3b merge and candidate-reported effort estimate.
2. Add meaningful characterization before extraction: mixed out-of-order/duplicate/
   conflicting meter packet, older/equal connector status, and response mutation
   isolation. Run those against the baseline; do not rewrite existing assertions.
3. Extract pure request data helpers and a composed management/query service sharing
   the existing pool. Keep Service/public signatures and OCPP transaction ownership.
4. Extract connection ownership/inbox helpers with deferred audited errors; keep an
   explicit request coordinator. Split charging dispatch into named handlers and
   separate meter-packet evidence processing. Prefer direct functions and cohesive
   modules, no new framework or fixed line-length/function-size targets.
5. Check exact statement/validation/audit/commit order, response copies, one action
   clock instant, Start supersession flush and same-connection versus session retry.
   Independent reviewer inspects both behavior equivalence and human followability.
6. Focused/all container tests, isolated DB recovery, static/source checks, normal
   redeploy/three-replica smoke. No live fault/purge. Record actual results/limitations.
7. Make coherent commits, push/open layered KWMS PR, verify exact-head CI, mark ready
   and hand off. Candidate alone merges. Frontend and full reader-doc rewrite wait.

Any discovered behavior defect is a separate explicit finding/fix with regression
evidence; a material domain/security change returns to candidate approval.

Execution checkpoint: three characterization cases passed on the unchanged backend
before extraction. Direct cohesive modules and named charging handlers are implemented;
independent diff/architecture review finds no behavior defect or blocker. Final 67
focused/327 full tests, source/static gates, actual isolated DB recovery and normal
three-replica redeploy/smoke pass. WORK.md records scope and limitations. Publication,
exact-head CI readiness and candidate manual review/merge remain the handoff gates;
frontend and documentation reorganization have not started.
