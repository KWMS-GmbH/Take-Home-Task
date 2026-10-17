# WP-R2 — Frontend/simulator readability without behavior changes

Candidate approved D-36/D-37, independent execution/review and frontend before docs.
Baseline: manually merged PR #6 `3e3c854`, identical to reviewed `689cb28`.
Branch: `codex/frontend-readability`. SPEC.md owns the unchanged behavior contract.

1. Confirm merge/tree/source; inspect the frontend, simulator and existing coverage.
   Independent baseline review identifies high-risk ordering and real test gaps.
2. Characterize HTTP outcome mapping, route/method/error security contract, truncated
   collector correlation and release-warning retention against the unchanged code.
3. Extract bounded client/errors and Authorize-only adapter, pure view/report validation
   and ordinary route registration. Keep factory/injections/imports/endpoints stable.
4. Separate fixed playback/correlation, API snapshots and run orchestration. Share the
   fixed allowlist. Split independent scenario checks into named ordered helpers,
   keeping every existing name/order/text/tri-state outcome and no rules framework.
5. Review exact preflight/lease/token/clock/pacing/snapshot/timeout/finally ordering;
   preserve correlation before truncation, data/protocol distinction and security.
6. Run focused and full pinned-container/static/source/recovery gates, normal redeploy/
   three-replica smoke and actual browser checks. No live outage, purge or demo reset.
7. Independently review code/architecture and evidence; commit coherent changes,
   push/open a layered KWMS PR, verify exact-head CI and hand off for manual merge.
   No full documentation rewrite or dependent work before that merge.

Any behavioral defect is reported explicitly and separately tested, not silently
reclassified as a refactor. A material scope/security/domain change needs agreement.
