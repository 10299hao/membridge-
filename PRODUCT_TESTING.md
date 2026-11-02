# Product validation

## Read-only dashboard — 2 November 2026

Replaced the journal layout with compact project monitoring, a searchable memory
table, session/history tables, capture status and a source-evidence viewer. The
dashboard has no save, edit, delete, handoff or sync-control actions. Its legacy
mutation endpoint rejects authenticated POST/PUT/PATCH/DELETE with HTTP 405;
unauthenticated requests remain rejected. The CLI and main service API keep their
management capabilities.

All **105 tests passed** in 66.90 seconds, with one existing upstream deprecation
warning. The dashboard test checks all ten former actions are rejected, stored
data stays unchanged, no handoff is written, evidence remains project-scoped and
the frontend does not dispatch mutations. JavaScript syntax and lint passed.
Report: `outputs/dashboard-readonly-20261102/tests.xml`.

Browser verification covered search, combined type/status filters, details and
source evidence, session/history navigation, desktop layout and a 390px phone
layout. A mobile overflow issue was fixed: wide tables scroll inside their own
container without widening the page. No browser errors were logged. Screenshots
in `outputs/dashboard-readonly-20261102` use synthetic project data.

Restart existing dashboard processes after upgrading to load the new backend.

## Priorities 1, 2 and 7 follow-up

The updated suite passed **105 tests**, with zero failures in 66.59 seconds.
Results: `outputs/priorities-tests-20261027.xml`. One upstream Starlette/httpx
deprecation warning was reported. Lint and formatting checks passed.

- The no-argument menu selects a project and a saved session, then prepares a handoff
  and launches Codex or OpenCode. Tests verify selection, launch, exit, list-only
  behavior, same-tool continuation, project isolation and forgotten/pending sessions.
- Eight fresh-process scenarios passed in `outputs/continuity-priorities-20261027/report.json`.
  They cover closed-source resume, same-tool resume, older-task selection, changed
  decisions, completed tasks, corrections, long transcript and changed files. Each
  also contains an unrelated second project. No required rubric phrases were missing,
  and no forbidden stale/foreign phrases appeared. Mean resume time was 2.87 seconds;
  p95 was 4.53 seconds on this machine. These are synthetic content checks, not agent
  accuracy or a general performance guarantee. Baseline extraction made no model calls.
- The real OpenCode → Codex → OpenCode run additionally passed all eight stages,
  including saved-session listing and resume after each source process exited.
  Both receivers recalled their expected invented facts. Evidence:
  `outputs/live-resume-priorities-20261027/report.json` and adjacent stage logs.
  OpenCode used `opencode/deepseek-v4.1-flash-free`; Codex used the installed CLI.
- The new evaluation command creates receiver prompts and an answer template, and
  separately scores supplied answers, missing/stale content, latency and optional
  usage/cost. Unknown usage remains null. Invalid/duplicate/incomplete evidence and
  nonfinite costs are rejected. Literal scoring needs human review.

Saved-session resume uses local storage and only committed revisions. It does not
recover unsaved messages or imply that a pending revision has finished. More than
one saved session requires explicit selection. The menu is terminal based; the
browser dashboard remains available as an action.

The installed copy was updated and checked from outside the repository, without
environment activation. Its launcher opened the menu and exited cleanly; its
runtime listed and resumed a saved scenario without native source discovery.
The installed dependency check reported no broken requirements.

## Initial product validation

The final automated suite passed **99 tests**, with zero failures, errors or skips
in 43.23 seconds. Machine-readable results: `outputs/product-tests-final-20261027.xml`.
Ruff lint and formatting checks passed. The HTTP verification script passed.

| Improvement | Verification |
|---|---|
| Real cross-tool continuity | Actual OpenCode → Codex → OpenCode processes recalled the invented facts in both directions. All five stages exited successfully. |
| In-chat switching | Generated OpenCode command and Codex skill ownership, idempotent installation, bridge execution, handoff creation and separately opened terminal arguments tested. |
| Repository checks | Changed files and Git branch/commit observations appear in handoffs; repeated imports preserve the original baseline. |
| Memory lifecycle | Replacement decisions, completed tasks, corrections, history and checkpoint filtering tested, including source-specific handoffs. |
| Installation | Disposable install/uninstall tested. Full installation additionally verified outside the source repository, without environment activation; packaged dashboard assets present and pip dependency check clean. |
| Background sync | Worker crash recovery, stale-lock recovery, live-worker protection, start/stop and project-specific login registration tested. |
| Deletion and retention | Preview/apply, source suppression, generated-file cleanup, correction derivatives and concurrent capture/deletion tested. |
| Local dashboard | Authentication, project scope, correction and deletion API tests; browser checks of search, evidence, correction, history, settings and narrow-screen layout. |
| Existing integrations | Real project-bound MCP stdio exchange and HTTP/API coverage passed alongside existing adapter and handoff tests. |

Live trial evidence: `outputs/live-validation-20261027-final/report.json` and the
adjacent stage logs. OpenCode used `opencode/deepseek-v4.1-flash-free`; Codex used
the installed authenticated CLI. This is one successful synthetic round trip,
not a statistical benchmark of long-term recall accuracy. Earlier failed attempts
remain available in their separate output directories.

The interactive slash-command/skill selection itself was not driven through both
hosts' terminal interfaces. Its generated bridge and launch arguments were tested;
the real agent round trip used their CLIs. Login startup registration was tested
with an isolated registry substitute and is not enabled automatically.

Repository verification detects saved file/Git changes; it does not prove a claim
or implementation is correct. Automatic decision replacement is conservative and
limited to recognized subjects. MCP search retrieves saved memory without running
live Git checks; use `membridge verify` or create a handoff for those checks.
Deletion removes MemBridge records and managed handoffs, not original application
logs, external exports or forensic traces in database storage.

Dashboard browser evidence: `outputs/dashboard-review/dashboard.png` (synthetic data).
