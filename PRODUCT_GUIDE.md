# MemBridge product guide

## Install once (Windows)

```powershell
cd C:\Users\mingh\Downloads\MemBridge
.\install.ps1
```

The installer creates `%USERPROFILE%\.membridge-app\runtime`, installs the package and MCP
extra, and adds a user-level `membridge` launcher. Open a new PowerShell afterward.
No virtual-environment activation is needed. Python 3.11+ and network access for package
installation are required. User-modified launchers are preserved rather than overwritten.
To uninstall the owned launcher/runtime while keeping memory and projects:

```powershell
.\scripts\uninstall.ps1
```

## Start with one command

Open a new PowerShell in your project and run:

```powershell
membridge
```

Choose the current project, another saved project, or enter a project path. Then
choose **Continue in Codex**, **Continue in OpenCode**, or **Open dashboard**.
For continuation, select the saved session by its task, source and save time.
MemBridge writes its handoff and opens the selected tool in the current terminal.
No virtual-environment activation or separate handoff command is needed.

This menu and saved-session resume use local storage. They can reopen saved work
after the source tool has closed, without reading that tool's history again.
They use the **last successfully saved revision**; unsaved conversation text cannot
be recovered. Save before exiting, use the in-chat switch, or explicitly enable sync.

For a scripted workflow:

```powershell
membridge save --from opencode
# Close OpenCode. Later, in the same project:
membridge
# Or list and explicitly select a saved session:
membridge resume codex --list
membridge resume codex --session SAVED_SESSION_ID --launch
```

Without `--launch`, `resume` only prepares the handoff. `--open-window` opens a
separate Windows terminal. If exactly one saved session exists, `--session` may be
omitted; multiple sessions require selection. You can resume into the same tool.
Deleted, failed and never-processed sessions are not offered. With a newer pending
revision, the last committed revision remains available. Gateway mode still uses
the existing `switch` workflow.

## Open the dashboard

```powershell
cd C:\path\to\your-project
membridge dashboard --open-browser
```

The private local URL includes a per-run access token. Do not share it. The dashboard
is bound to this project on loopback, with authenticated reads and no external scripts.
It is **read-only**: view the checkpoint, searchable/filterable memory table, source
evidence, history, saved sessions, sync status and retention policy. Saving, tool
switching, corrections, deletions and sync changes stay in the CLI. The dashboard
rejects mutation requests, including requests from older dashboard pages. It requires
local storage, not gateway mode.
The terminal process must keep running; Ctrl+C stops it. Use `--port 8766` if needed.

## Switch inside the coding tool

```powershell
membridge integrate
```

Then use **`/switch codex` in OpenCode**, or **`$membridge-switch opencode` in Codex**.
OpenCode uses a project command; Codex uses a project-local skill. A fixed bridge saves
the source session, writes the handoff and opens a separate Windows terminal. The
source process stays open; close it when ready. Normal host tool permissions apply.
Restart the host if its command/skill list has not refreshed.

To remove unchanged MemBridge-owned assets:

```powershell
membridge integrate --uninstall
```

User-edited command files are preserved. For ordinary terminal switching:

```powershell
membridge switch codex --launch
membridge switch opencode --launch
```

## Check and update memory

```powershell
membridge verify
membridge search "database"
membridge history
membridge evidence MEMORY_ID
membridge correct MEMORY_ID "Decision: We chose PostgreSQL for concurrent writes."
membridge resolve TODO_ID --action completed
membridge resolve OLD_ID --action supersedes --replacement NEW_ID
membridge resolve MEMORY_ID --action disputed
```

Imported file/Git observations can detect missing/changed referenced files and changed
branches/commits. Handoffs include review warnings. Hash matches do not prove a claim
correct, and tests are not rerun automatically. Legacy unobserved memories remain
unverified. Only bounded relative files inside the project are inspected.

Clear adopted database, cache, test-framework and default-page-size decisions can
supersede earlier differing decisions in the same branch scope. Negations, proposals
and ambiguous claims remain reviewable. Clear reported implementations and action aliases
complete matching tasks. General semantic reasoning is not implemented. Corrections are
user-cited replacements; original memories remain addressable as history/evidence.

## Reliable background saving

```powershell
membridge sync start --from opencode
membridge sync start --from codex
membridge sync status --from opencode
membridge sync stop --from opencode
membridge sync stop --from codex
membridge sync resume
```

Configured model/service processing requires `--allow-model`. Watchers save only
matching project sessions. A supervisor retries failed workers with bounded backoff;
OS leases release on process exit so known stale state can be recovered safely.
Unknown state is preserved. Stop requests let the current import finish.

Optional current-user Windows login startup:

```powershell
membridge sync autostart
membridge sync autostart --no-enable
```

This manages only this project's named HKCU Run entry, not an administrator service.
It resumes previously enabled sources. Model credentials are not persisted and must
be available in the login environment if required. Without autostart, use `sync resume`
after reboot. The desktop dashboard does not need to stay open for sync.

## Delete saved data or set retention

```powershell
membridge saved-sessions
membridge forget --session SAVED_SESSION_ID
membridge forget --session SAVED_SESSION_ID --yes
membridge forget --yes
membridge restore
membridge retention --days 30
membridge retention --days 30 --apply
```

Forgetting previews by default. `--yes` erases saved session/job/commit snapshots,
memories and associated resolution records. Local CLI forgetting clears
managed handoff text and generated handoff backups while preserving code and instructions
outside the managed block. It suppresses automatic reimport. `restore` permits reimport;
it does not recover erased data. Source tool logs and separately exported reports are
outside MemBridge's deletion boundary. Database deletion is logical record deletion,
not a guarantee of forensic secure erasure of disk/WAL/backups.

Retention expires sessions whose saved revision has not changed within the chosen
number of days. Preview first, then apply. Zero disables expiry. Active watchers enforce
the policy. An unchanged expired source is suppressed; a fresh revision can be imported.
Explicitly forgotten sources stay blocked until restored. Remote gateway deletion only
erases server records; use the local CLI to clear local generated files as well.

## Evaluation

The restart-continuity suite covers eight synthetic scenarios: closed source,
same-tool continuation, selecting an older task among several, changed decisions,
completed tasks, corrected memory, a long transcript and changed files. Every case
also checks isolation from a second project. A fresh CLI process reopens the database
for each resume; native source applications do not have to be running.

```powershell
membridge evaluate-continuity --output outputs\restart-trial
```

The report records missing/stale/foreign content checks, task continuity, mean/p95
resume latency and handoff size. It uses the local extractive baseline, makes zero
model calls, and therefore incurs no provider charge. Its content scores are not
receiving-agent accuracy. The suite also writes isolated receiver prompts and an
answer template. Collect real answers in fresh sessions, record measured latency,
tokens and cost where available, then score them:

```powershell
membridge evaluate-continuity --responses filled-responses.json --output outputs\answer-trial
```

Missing answers and unknown usage stay null. Literal phrase scores need human review
for paraphrases and negation. The live round-trip test below additionally exercises
saved-memory resume after each source CLI has exited.

```powershell
membridge evaluate --output outputs\offline-trial
membridge evaluate-live --output outputs\live-trial --timeout 100
membridge evaluate-live --output outputs\live-trial-2 --opencode-model PROVIDER/MODEL
```

Use a new output directory each time. The offline trial measures synthetic phrase
coverage, creates three-condition prompts and an answer template; it does not claim
agent accuracy. The live trial uses installed CLI providers/accounts and invented facts
in an isolated Git project/data home. Stage logs and assistant answers are saved.
It can consume the tools' normal usage allowance. Provider failures/timeouts remain
visible failures, not passing tests. A small live trial is not a statistical quality benchmark.

The optional MCP tools and manual evaluation rubric are documented in CONTINUITY.md.
MCP searches read saved memory without launching Git. Use `verify`, the dashboard or
a freshly generated handoff for current repository observations.
