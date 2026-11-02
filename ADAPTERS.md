# Adapter Guide

MemBridge supports any named tool through a portable transcript. Native adapters add direct local
discovery and specialized parsers; they are distinct from manual export/paste support.

## Capability matrix

| Tool | Input/discovery | Output |
|---|---|---|
| OpenCode | CLI session listing/export; v2 and legacy JSON | Managed `AGENTS.md` |
| Codex | Local JSONL rollouts under `CODEX_HOME/sessions` | Managed `AGENTS.md` |
| Claude Code | JSONL under `CLAUDE_CONFIG_DIR/projects/*`; default `~/.claude/projects` | Managed `CLAUDE.md` |
| Gemini CLI | Project chats under `~/.gemini/tmp/*/chats`; JSON/JSONL | Managed `GEMINI.md` |
| Aider | Project-local `.aider.chat.history.md` | `MEMBRIDGE_HANDOFF.md`, loaded explicitly with `aider --read` |
| Any other name | Manual JSON, JSONL, Markdown or text transcript | Print/paste brief, or `--to file` |

Examples of export-only labels include Grok, DeepSeek, ChatGPT, Cursor, Windsurf, Copilot, Cline,
Roo, Continue, Qwen, Kimi, Mistral, Perplexity, Ollama and LM Studio. These labels do not imply
automatic access to the app, its cloud account, its browser conversation, or a proprietary database.

```powershell
membridge adapters list
membridge sessions --from claude
membridge save --from claude
membridge handoff --to gemini
membridge save --from deepseek --file conversation.json
membridge handoff --to grok
membridge handoff --to file
```

Tool names use lowercase letters, digits and hyphens, up to 64 characters. Aliases include
`claude-code`, `claude-cli`, `gemini-cli`, `codex-cli`, `open-code` and `grok-cli`.

## Native discovery and parsing

Discovery and handoff writes happen on the local computer. Services only receive the redacted
selected export. Imports are always scoped to the CLI's selected project; paths in an export
cannot override that scope. Native discovery uses exact canonical directory matching.

- **OpenCode:** command help selects v2 `session export` or legacy `export`. User/assistant
  text and tool-state evidence are retained; internal events and reasoning are excluded.
- **Codex:** reads each rollout's initial metadata record for project matching. It imports
  response messages and tool outputs, with event-only compatibility. Duplicate event messages,
  system/developer instructions and reasoning records are excluded.
- **Claude Code:** checks bounded leading JSONL records for `cwd`, ignores subagent files and
  sidechains, and follows the latest conversational parent branch. Text and tool results are
  retained; thinking blocks are excluded. Missing branch history fails explicitly. Logs are an
  internal format and can change; use Claude's text/Markdown export as a fallback.
- **Gemini CLI:** matches `.project_root`, the `projects.json` path registry, or legacy path hashes.
  Set `MEMBRIDGE_GEMINI_HOME` to override the root `.gemini` directory. Parsing supports full JSON
  conversation snapshots and JSONL metadata/message records, patches, rewinds, removals and ordering.
  Thoughts and non-conversation info/error/warning records are excluded. Project hash calculation
  can vary across versions; explicit `--file` remains available when discovery finds no match.
- **Aider:** discovers only the current project's default history file. `####` headings are user
  prompts; the response body is assistant content. Metadata/command quote lines are skipped.
  Customized history locations can be selected explicitly with `--file`.

These implementations are verified with synthetic fixtures and service tests. MemBridge does
not claim a live check of every CLI version or all possible storage configurations.

## Universal transcript schema

Every source name accepts a standard message transcript:

```json
{
  "session_id": "project-conversation-1",
  "title": "API design",
  "messages": [
    {"id": "m1", "role": "user", "content": "Goal: build a task API"},
    {"id": "m2", "role": "assistant", "content": "We chose Postgres. TODO: add pagination."}
  ]
}
```

Supported variants include a top-level message array, JSONL role/content records, Gemini-style
`contents`/`parts`, text content-block arrays, and a single ChatGPT-style `mapping` conversation with
`current_node` selecting the branch. Multi-conversation archives must first be reduced to one
conversation; sibling branches are not merged. Unknown proprietary structures are rejected rather
than guessed. Image, audio, and hidden reasoning payloads are not imported.

Markdown/text exports recognize User/Human and Assistant/AI/Model role headings, including inline
`User: ...` labels. Role labels inside fenced code stay in the original message. Without role
labels, text is treated as a user-supplied note. A file path provides stable identity for text
imports; a supplied `session_id` is preferable for structured exports. The import limit is 8 MiB.

When a model runs inside another coding tool, use the application's reader or override its format:

```powershell
membridge save --from deepseek --format opencode --file session.json
membridge save --from grok --format generic --file messages.json
```

The source label remains `deepseek` or `grok`; `input_format` records which parser was selected.
Native tools can also import portable JSON via `--format generic` or plain text without an override.

## Handoff delivery

Known instruction files are updated with one managed block. Existing bytes outside the block
are preserved, prior files are backed up, symlinks and malformed markers are rejected, and a
local lock plus atomic replacement protects the write. No arbitrary tool label becomes a path.

Unknown targets print a brief with a paste instruction; they do not silently write an unrelated
context file. `--to file` writes a managed `MEMBRIDGE_HANDOFF.md`; attach it or ask the receiving
tool to read it. Aider requires `/read MEMBRIDGE_HANDOFF.md` or `aider --read MEMBRIDGE_HANDOFF.md`.
Use a new session for tools that load instruction files at startup. Customized file-loading settings
may require manual attachment. `--preview` never writes a context file. MCP is not implemented.

## Installable adapter plugins

The built-in registry accepts adapters without changing model enums, CLI branches or service schemas.
Install a trusted Python package with an entry point in the `membridge.adapters` group:

```toml
[project.entry-points."membridge.adapters"]
mytool = "my_package.adapter:descriptor"
```

```python
def descriptor():
    return {
        "parser": parse_export,      # (data: dict, project: str) -> Session
        "reader": MyLocalReader,     # optional; constructor(project), list_sessions(), export(id)
        "context_file": "MYTOOL.md", # optional project-local Markdown basename
    }
```

The reader returns session dictionaries containing at least `id` and `updated`; its export method
returns a JSON-serializable object. Parsers must preserve source IDs as evidence and return the
shared Session schema. MemBridge validates the result and enforces the selected project/source.
Names cannot overwrite built-ins. Plugin errors are reported by `adapters list` without disabling
built-in adapters. Entry points execute installed package code; install only trusted adapters and
install the same package in the CLI and relevant service images. Container images must be rebuilt
after adding packages.

Add fixtures for every new format, test privacy and project filtering, then verify import through
gateway/ingestion and a handoff to a different tool. Fixtures should be synthetic or anonymized.

## Format references

- [Claude session storage and format stability](https://code.claude.com/docs/en/sessions)
- [Claude project context](https://support.claude.com/en/articles/14553240-give-claude-context-claude-md-and-better-prompts)
- [Gemini session management](https://geminicli.com/docs/cli/session-management/)
- [Gemini chat recording types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts)
- [Gemini project context files](https://geminicli.com/docs/cli/gemini-md/)
- [Aider read-only context](https://aider.chat/docs/usage/conventions.html)
- [OpenCode project instructions](https://dev.opencode.ai/v2/docs/instructions/)
