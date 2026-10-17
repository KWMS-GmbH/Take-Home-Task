# AI usage

I used Codex, including parallel review agents, to inspect the assignment, discuss
architecture and edge cases, research protocol/tooling behavior, draft process
documentation, and implement/test an assignment-integrity guard. Application
implementation has not started yet; this note will be updated to describe the final
submitted work.

I corrected Codex's recommendation of one final PR: I will review and manually merge
several reasonably sized vertical-slice PRs. I also required actual three-backend
replica correctness rather than only a written scaling explanation. Protocol source
review corrected an early proposal for permanent station-wide message-ID uniqueness;
OCPP's uniqueness guarantee is scoped to a sender and WebSocket connection.
