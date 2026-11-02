# MemBridge

Portable project memory across AI tools. Import a session, keep evidence-backed decisions and TODOs, then send a concise handoff to the next tool.

After installation, run **`membridge`** for a project/session menu. Choose where to
continue and resume a saved task even after closing the original coding tool.
See [the product guide](PRODUCT_GUIDE.md#start-with-one-command) for the simple workflow.

This is a working first version with native readers for OpenCode, Codex, Claude Code, Gemini CLI and Aider, plus portable transcript imports for other tools. It includes a local dashboard, task checkpoints, repository checks, supervised sync, in-chat switching, decision/task updates, corrections, deletion/retention and optional MCP retrieval. See [PRODUCT_GUIDE.md](PRODUCT_GUIDE.md) for the latest commands and [CONTINUITY.md](CONTINUITY.md) for the original CLI workflows.

## Supported tools and routes

See [PRODUCT_TESTING.md](PRODUCT_TESTING.md) for the latest automated, installed-package,
browser and real cross-tool validation results and their limits.

| Source | Session discovery | Import route | Handoff target |
|---|---|---|---|
| OpenCode | Native CLI export | v2/legacy JSON, text | Managed `AGENTS.md` |
| Codex | Local rollouts | JSONL, text | Managed `AGENTS.md` |
| Claude Code | Local project logs | JSONL, Markdown/text exports | Managed `CLAUDE.md` |
| Gemini CLI | Local project chat files | JSON or JSONL, text | Managed `GEMINI.md` |
| Aider | Project chat history | Aider Markdown, standard JSON | `MEMBRIDGE_HANDOFF.md`, then `aider --read MEMBRIDGE_HANDOFF.md` |
| Grok, DeepSeek, ChatGPT, Cursor, Windsurf, Copilot, Cline, Roo, Continue, Qwen, Kimi, Mistral, Perplexity, Ollama, LM Studio, or any other name | Export only | Standard JSON/JSONL or Markdown/text | Print a brief to paste, or `--to file` |

Export-only does **not** mean MemBridge automatically reads that app's private history. It means you supply a supported transcript. Grok and DeepSeek may be models inside another coding app: in that case use that app's native reader, or select `--format` explicitly while preserving the model label.

```powershell
membridge save --from claude
membridge handoff --to gemini
membridge save --from gemini
membridge handoff --to codex

membridge save --from deepseek --file conversation.json
membridge handoff --to grok
# Copy the printed brief into Grok.

membridge save --from my-new-tool --file notes.md
membridge handoff --to file
# Attach/read MEMBRIDGE_HANDOFF.md in the receiving tool.

membridge save --from deepseek --format opencode --file session.json
membridge adapters list
```

Every named source can pass memory to any destination. New tool names need no code change for portable imports. Proprietary exports must be converted into the standard message schema or get a dedicated adapter. Browser conversations are not scraped and no model accounts are contacted during import.

Native readers are tested with synthetic fixtures based on observed/documented formats; this does not claim a live test against every installed CLI version. See [ADAPTERS.md](ADAPTERS.md) for discovery paths, format limits, and installable plugin support.

## Install and try it (Windows PowerShell)

Requires Python 3.11+. Docker is optional for local use.

Clone the project first:

```powershell
git clone https://github.com/10299hao/membridge-.git
cd membridge-
```

For a normal Windows installation, run `.\install.ps1`. It creates a separate runtime
and a user-PATH launcher; no environment activation is needed afterward. Open a new
PowerShell, enter your project folder and run `membridge dashboard --open-browser`.
The virtual-environment commands below are for development.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements.lock -e '.[dev,postgres]'
.\.venv\Scripts\Activate.ps1
membridge --help
```

The included demo uses synthetic data and writes only into `examples/demo-project`:

```powershell
./scripts/demo.ps1
Get-Content examples/demo-project/AGENTS.md
```

No LLM account or API key is needed for the demo. With no model configured, MemBridge selects original transcript lines using rules, labels the brief **extractive**, and searches by keywords. This mode does not provide semantic understanding.

## Use with a real project

Run `membridge integrate` once to install project-local in-chat commands. Then use
`/switch codex` in OpenCode or `$membridge-switch opencode` in Codex. These prepare
the handoff and open a separate Windows terminal without forcibly closing the source.
Normal host permissions still apply. See [PRODUCT_GUIDE.md](PRODUCT_GUIDE.md).

The shortest switch saves memory, prepares a checkpoint and opens a fresh destination session:

```powershell
# Exit OpenCode, then run in the same project:
membridge switch codex --launch
# Exit Codex to switch back:
membridge switch opencode --launch
```

For OpenCode and Codex, use one command to save and prepare the next tool. Run these
in your project's terminal after exiting the current AI tool:

```powershell
# After chatting in OpenCode:
membridge switch codex
codex

# After chatting in Codex:
membridge switch opencode
opencode
```

`switch codex` imports the latest OpenCode session for this exact project, waits for
processing, and writes the handoff to `AGENTS.md`. `switch opencode` does the reverse.
No `init`, separate `save`, or separate `handoff` command is needed. In the new tool,
ask it to continue from the MemBridge handoff. This supplies saved context, rather
than resuming the other tool's conversation. `--launch` opens the destination;
MemBridge does not exit a running tool.
Use `--session ID` to choose a source session, or `--from claude` (or another native
reader) to override the source. If processing fails, the handoff is not updated.

The separate commands are also available for more control:

Both directions work inside the same project directory:

```powershell
# OpenCode -> Codex
membridge save --from opencode
membridge handoff --to codex

# Codex -> OpenCode
membridge save --from codex
membridge handoff --to opencode
```

Start a new session in the receiving tool after handoff. MemBridge reads the source conversation,
saves useful details, and writes a short note into `AGENTS.md`. Both tools can read that note.
Your code remains in the same folder. Saving can be manual or enabled continuously
for a selected project/source with `membridge sync start --from opencode`.

Codex sessions are discovered under `CODEX_HOME/sessions`, defaulting to `~/.codex/sessions`,
with exact project-directory filtering. Explicit imports support
`membridge save --from codex --file rollout.jsonl`. Internal instructions and reasoning records
are excluded. A synthetic fixture is in `data/samples/codex/session.jsonl`.

Run these inside the project whose context you want to transfer:

```powershell
membridge init
membridge sessions --from opencode
membridge save --from opencode
membridge search 'pagination'
membridge handoff --to codex --preview
membridge handoff --to codex
```

Start a **new Codex session** in that directory to use the generated instructions. MemBridge does not launch Codex or modify an already-running conversation.

- `save` selects the latest session from the chosen source whose directory exactly matches the project. Use `--session ID` to select another session.
- Every project command accepts `--project C:\path\to\project`.
- Discovery reads only session metadata; export reads the selected transcript. Both installed v2 `opencode session export` and legacy `opencode export` are supported.
- Identical saves return the existing job. Append-only updates extract just new messages and keep existing memory IDs. Edits, changed model settings and `--refresh` trigger full extraction. Replaced memories remain as superseded history with their original evidence.
- `handoff --query 'task' --max-chars 6000` selects relevant memory within a character budget. This is not an exact token budget.
- Existing instructions outside the managed block are preserved. Previous files are backed up to `.membridge/backups/`. Malformed blocks and symlinked `AGENTS.md` files are rejected.
- `membridge evidence MEMORY_ID` shows the redacted source messages behind a search result.

Manual imports work without OpenCode on PATH:

```powershell
membridge save --from opencode --file session.json
membridge save --from generic --file notes.md
membridge handoff --to stdout
```

Generic JSON has a `messages` array with `role` and `content`; IDs are generated if absent. See [ADAPTERS.md](ADAPTERS.md) for schemas and supported formats.

## Enable AI extraction and semantic search

Use an OpenAI-compatible backend (local or hosted) with `/chat/completions` and optionally `/embeddings`. The chat endpoint must support JSON object responses. Set environment variables before running the CLI or services:

```powershell
$env:MEMBRIDGE_LLM_URL = 'http://localhost:11434/v1'
$env:MEMBRIDGE_LLM_MODEL = '<your-installed-chat-model>'
$env:MEMBRIDGE_EMBEDDING_MODEL = '<your-installed-embedding-model>'
# Set MEMBRIDGE_LLM_KEY if the provider requires a credential.
```

Model names are configured, not downloaded automatically. Leave the embedding model empty for keyword retrieval with AI extraction. Long transcripts are chunked; all extracted memories are schema-validated and must cite existing source message IDs. Evidence references do not guarantee that an LLM interpreted them correctly.

Changing provider/embedding models requires reprocessing saved sessions to obtain comparable vectors. Search still uses keywords for memories from a different embedding configuration. Use `membridge save --from opencode --file session.json --refresh` to re-extract a previously saved session. A bulk reindex command is future work.

## Local API

```powershell
membridge serve gateway --with-worker
# Another terminal:
$env:MEMBRIDGE_SERVER = 'http://127.0.0.1:8000'
membridge save --from opencode --file session.json
```

Interactive API documentation is at [localhost:8000/docs](http://127.0.0.1:8000/docs). Set `MEMBRIDGE_API_KEY` on the server and client to require bearer authentication. Non-loopback binding requires this key.

`membridge init --server http://127.0.0.1:8000` persists the server URL for a project; an environment variable overrides it. Model credentials are never written to project configuration. `.env` is read by Docker Compose, not automatically by the local CLI.

## Docker Compose

Run Docker Desktop first:

```powershell
./scripts/configure.ps1
# Optional: edit .env to configure the model backend.
docker compose up --build -d
```

This runs gateway, ingestion, AI, a durable worker, and Postgres with pgvector. A shared image supplies each application role. The database is persisted in a named volume; it is not published to the host. Only the gateway is published on loopback port 8000.

Set `MEMBRIDGE_SERVER=http://127.0.0.1:8000` and `MEMBRIDGE_API_KEY` from `.env` in the local CLI terminal. If the model runs on the Windows host, use `http://host.docker.internal:11434/v1` in `.env` rather than `localhost`.

See [k8s/README.md](k8s/README.md) for Minikube deployment, persistence, and scaling commands.

## Jobs and recovery

```powershell
membridge save --from opencode --file session.json --no-wait
membridge process                  # process ready jobs in local mode
membridge jobs list
membridge jobs retry JOB_ID
membridge worker                   # continuous worker for the configured database
```

Workers claim jobs with leases, heartbeat during extraction, and fence stale results. Processing retries up to three attempts; failures remain visible and can be retried after fixing the provider. A newer session revision supersedes older pending results. In API mode a worker process, or `serve gateway --with-worker`, is required.

## Configuration

| Variable | Purpose |
|---|---|
| `MEMBRIDGE_HOME` | Local state directory; defaults to `~/.membridge` |
| `MEMBRIDGE_DATABASE_URL` | SQLAlchemy URL; defaults to a local SQLite database |
| `MEMBRIDGE_SERVER` | Remote gateway URL; unset for local mode |
| `MEMBRIDGE_API_KEY` | Shared bearer token for client and internal service calls |
| `MEMBRIDGE_LLM_URL`, `MEMBRIDGE_LLM_MODEL`, `MEMBRIDGE_LLM_KEY` | Compatible chat provider settings |
| `MEMBRIDGE_EMBEDDING_MODEL` | Optional semantic embedding model |
| `MEMBRIDGE_INGESTION_URL`, `MEMBRIDGE_AI_URL` | Internal service URLs for distributed operation |

## Verification

```powershell
python -m pytest -q
python -m ruff check membridge tests scripts/verify_http.py
python scripts/verify_http.py
```

The last command starts three temporary HTTP servers and exercises the real CLI, gateway routing, ingestion, AI service, worker, database, evidence lookup, and repeatable Codex-file injection. It uses synthetic data and stops its own servers afterwards.

Tests cover both OpenCode formats, project filtering, redaction, revisions, source snapshots, duplicate saves, expired-worker recovery, bounded failures, malformed instructions files, byte preservation, provider contracts, and API authentication. Live model quality and actual Codex answers require a configured model and manual evaluation; they are not claimed by these tests.

## Privacy and current limits

Redaction runs before remote transmission and again during ingestion. It covers common credentials, bearer tokens, private keys, and credential-bearing URLs. It is **best effort**, not a guarantee that private code, personal information, or every secret has been removed. Redacted normalized transcripts and source snapshots are retained locally or in the configured database; avoid importing data you cannot store or send to your selected provider.

This version is for one trusted user. Project filtering prevents accidental context mixing, but is not a tenant authorization boundary. Project identity uses canonical absolute directory paths, so moving a project requires reimporting it. Search currently scans a project's memories; large-scale ANN indexing, general semantic contradiction reasoning, host-native capture plugins and automatic MCP prompt prefetch remain future work. Decision updates use conservative rules. File/Git checks generate review warnings, not proofs that code or tests are correct.

See [ARCHITECTURE.md](ARCHITECTURE.md), [DATASET.md](DATASET.md), and [PROBLEM_STATEMENT.md](PROBLEM_STATEMENT.md) for architecture and evaluation goals.
