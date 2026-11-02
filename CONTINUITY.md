# Continuing work across tools

For the current installer, dashboard, in-chat commands, verification, deletion and
retention controls, see [PRODUCT_GUIDE.md](PRODUCT_GUIDE.md).

## One-command switching

Exit the current coding tool, stay in the same project directory, then run:

```powershell
membridge switch codex --launch
# Or, after working in Codex:
membridge switch opencode --launch
```

This saves the latest source session, prepares a cited checkpoint for that exact
revision, writes its memories into AGENTS.md and opens a fresh destination with a
prompt to summarize the task, progress and next step. No automatic permission
approval flags are passed. Leave off `--launch` to open the next tool yourself.
Use `--session ID` when multiple sessions exist; defaults select the latest source
session for this exact project. `--from claude` can override the native source.

## Checkpoints and changed memories

```powershell
membridge checkpoint
membridge history
membridge resolve OLD_ID --action supersedes --replacement NEW_ID
membridge resolve TODO_ID --action completed
membridge resolve MEMORY_ID --action disputed
```

The checkpoint selects explicit labels (`Current task:`, `Completed:`, `Blocker:`,
`Tests:`, `Next step:`), plus reported file changes. Without an explicit task it
uses the latest user message. It cites the original messages. Tests and progress
are reported claims, not independent repository verification.

Explicit `Completed: X` / `[x] X` records complete exactly matching labelled TODOs.
Clear adopted database/cache/testing/page-size decisions can supersede earlier
different decisions in the same branch scope. Clear task-action aliases also complete
matching TODOs. Ambiguous or general semantic conflicts need explicit review.
Superseded memories disappear from normal retrieval but retain
their evidence. Manual lifecycle choices survive full re-extraction. History is
limited to 100 rows and includes repeated claims from different revisions.

## Enable continuous saving once per project/source

```powershell
membridge sync start --from opencode
membridge sync start --from codex
membridge sync status --from opencode
membridge sync stop --from opencode
membridge sync stop --from codex
```

Windows workers are hidden; other platforms use detached processes. Polling is
project-scoped, with older sessions committed before newer sessions. Only appended
messages are extracted; the model receives at most four prior messages (500
characters each) for reference, without re-extracting them. Edits/reordering,
provider changes and refresh trigger full extraction. Offline rules do not resolve
pronouns from that reference context.

Configured models or remote services require `--allow-model` to explicitly allow
continuous processing/transmission. Model credentials are not saved to project files.
Saving does not regenerate the handoff: `switch` prepares it when needed.

Use `membridge watch --from opencode` for a foreground worker; `--once` performs one
scan. Stop requests finish the current import first. No startup service is installed;
restart watchers after reboot. State and safe logs live in `.membridge/watch-<source>.*`.
A supervisor restarts failed workers with bounded backoff, and OS leases allow
known stale state to be reclaimed after crashes. Unknown lock contents are preserved.
Optional Windows login startup is documented in PRODUCT_GUIDE.md. A failed import remains visible through
`jobs list`; use `jobs retry` after fixing the underlying problem.

## Optional local MCP access

```powershell
python -m pip install -c requirements.lock -e '.[mcp]'
membridge mcp --project C:\path\to\project
```

Register the absolute environment `python.exe` as an executable in your host's
supported MCP settings, with arguments:

```json
["-m", "membridge", "mcp", "--project", "C:\\path\\to\\project"]
```

MemBridge does not edit host settings. Its tools are `search_project_memory`,
`get_current_checkpoint` and `get_memory_evidence`. The project is fixed at startup;
tools cannot select other directories or mutate memories. Searches use local
keywords without calling a model. Evidence text is limited to 12,000 characters
with truncation metadata. This version requires local storage: unset the project
server and MEMBRIDGE_SERVER. The host agent must call the tools; automatic prompt
prefetch and native capture plugins are not implemented.

## Evaluate handoff quality

```powershell
membridge evaluate --output outputs\my-evaluation
```

This runs three synthetic scenarios offline and creates:

- `content-report.json`: expected phrase coverage and character-size ratios.
- Nine prompts: three tasks under no handoff, manual handoff and MemBridge handoff.
- `responses-template.json`: empty answer fields and optional measured elapsed times.
- `HOW_TO_EVALUATE.md`: instructions for collecting controlled agent answers.

Use a fresh, isolated agent session/project copy for each prompt. Give it only the
matching prompt, not answer keys or other conditions. Record verbatim responses,
then score the completed template into a new directory:

```powershell
membridge evaluate --responses filled-responses.json --output outputs\answer-scores
```

The literal phrase rubric needs human review: paraphrases can fail and negated
phrases can pass. Known-wrong-answer detection is not a general hallucination
detector. Content coverage is not receiving-agent QA accuracy. Ratios measure
characters, not tokens, and short cited handoffs can exceed short transcripts.
`membridge evaluate-live --output <new-folder>` now attempts a real OpenCode-to-Codex
round trip with invented facts, isolated storage and a separate Git boundary. Its logs
record actual availability and assistant answers. See PRODUCT_GUIDE.md for details.
