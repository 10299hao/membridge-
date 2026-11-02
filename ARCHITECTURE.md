# System Architecture

OpenCode, Codex, Claude Code, Gemini CLI and Aider have native session readers. Any other
tool name can use portable transcript imports. All sources use the same store and retrieval
flow. Target delivery uses `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, an Aider read-file,
or stdout/manual paste. Tool names remain attached to memories as provenance.

## Implemented v0.1 flow

```text
User machine
  OpenCode session list/export -> MemBridge CLI -> redaction
                                 |                 |
                                 | local mode      | API mode
                                 v                 v
                           shared engine        API gateway
                                 |                 |
                                 |             ingestion service
                                 |             (parse + recheck redaction)
                                 v                 v
                           persistent jobs and normalized sessions
                                 |
                           extraction worker -> AI service or local intelligence
                                 |              (extract / embed)
                                 v
                           memories + source snapshots + embeddings
                                 |
                  project-scoped hybrid search -> bounded handoff brief
                                 |
                  local CLI -> managed AGENTS.md block -> new Codex session
```

OpenCode/Codex session discovery and handoff file writes always happen on the user machine.
Services never need access to the user's home directory. See ADAPTERS.md for all discovery paths.

## Components

| Role | Implementation | Deployment |
|---|---|---|
| Local CLI | Typer; `membridge/cli.py` | User machine |
| Gateway | FastAPI; `create_app("gateway")` | Stateless HTTP service |
| Ingestion | FastAPI; `create_app("ingestion")`; adapters | Stateless HTTP service |
| AI | FastAPI; `create_app("ai")`; compatible HTTP provider | Stateless HTTP service |
| Worker | `membridge worker`; leases and heartbeats | One or more processes; Postgres for replicas |
| Memory store | SQLAlchemy; SQLite or Postgres + pgvector | Local file or StatefulSet + persistent volume |

One shared Python package/image avoids schema drift across roles. Roles can run in separate
containers or together locally; imports, extraction, persistence and delivery remain separate modules.

## Data contracts

`membridge/models.py` defines validated Session, Message, MemoryDraft, and API request contracts.
Normalized messages preserve stable source IDs, roles, text, and available timestamps. Unsupported
OpenCode internal events and reasoning parts are excluded. Generic JSON and Markdown imports are supported.

Sessions are scoped by canonical absolute project directory, source tool and source session ID.
A content digest identifies a revision. Repeated identical saves are idempotent; `--refresh` reprocesses
terminal jobs after provider configuration changes. Committed source snapshots and extraction
signatures detect append-only updates: only new messages are extracted, with bounded reference-only
model context (up to four earlier messages, 500 characters each). Edits, reordering, settings
changes and explicit refresh use full extraction.
The additive `session_commits` table stores the committed digest, source snapshot and cited
checkpoint. Existing completed jobs without a checkpoint are rebuilt on their next save.
The `memory_resolutions` table records explicit lifecycle choices. Full replacement marks old
memory rows superseded instead of deleting their source evidence. This increases retained data;
retention policies can expire old revisions, and explicit forgetting removes saved data.

Memory types are `decision`, `todo`, `file_change`, `preference`, and `fact`. Each includes source
message IDs, redacted source snapshots, source session/tool, extraction mode, and task status.
Snapshots preserve evidence while a newer session revision is awaiting processing.

## Processing and failure handling

Imports persist a durable job before extraction starts. A worker claims one job, heartbeats its
120-second lease every 20 seconds, and commits memories atomically. Postgres uses row locks and
`SKIP LOCKED`; SQLite is intended for one local worker. A stale attempt cannot commit after a newer
claim. A revision cannot overwrite results for a newer imported revision. Expired final attempts
become visible failed jobs. Processing retries up to three attempts, then requires `jobs retry`.

Without a configured model, the extractive baseline selects original lines and uses keyword search.
Configured chat models receive bounded transcript chunks and must return validated JSON with existing
evidence IDs. Configured embedding models enable cosine similarity, combined with keyword overlap.
Postgres calculates vector similarity with pgvector; SQLite calculates it in Python. Embedding
provider/model identity and dimension must match before vectors are compared.

Retrieval always filters by project. Search currently scans project memories; fixed-dimension ANN
indexes and large-scale ranking optimizations are future work. Handoff composition is deterministic,
preserves complete citations, and respects a configurable character budget.

Switching scopes retrieval to the exact completed source job and its committed checkpoint.
Checkpoint fields select explicit task/progress/blocker/verification/next-action/file lines;
these are transcript claims, not independent repository/test observations. Manual lifecycle
resolutions hide superseded claims or complete/dispute memories. Conservative rules reconcile
clear adopted decisions for known subjects within branch scope and clear task-action aliases;
general semantic conflicts still need review. `memory_details` stores per-memory repository
observations and decision keys. File/Git checks generate review warnings, not correctness proofs.

`sync start` runs a project/source-scoped polling subprocess. A per-source directory lock prevents
simultaneous watchers, a receipt records safe status counters, and a stop sentinel requests graceful
shutdown. OS file leases release on process exit and a supervisor restarts failed workers with
bounded backoff. Known stale receipts can be reclaimed; unknown contents are preserved. Optional
Windows HKCU login registration resumes explicitly enabled sources. Configured model/service
transmission needs `--allow-model`; credentials are never written to project settings.

The optional MCP stdio server uses the official Python MCP SDK and binds three read-only tools
to one project. It searches local storage without model calls; it exposes neither arbitrary paths
nor memory mutations. Host configuration, automatic prompt prefetch and remote-gateway MCP are
outside this version. File-based delivery remains available independently of MCP.

## Codex delivery

The no-argument CLI provides a project/session menu. `resume` selects a committed
session/job by project and saved ID, independently of native discovery. Multiple
sessions require explicit selection; the last committed revision remains available
while a newer import is pending. The source application need not be running.
Both this menu and saved-session resume currently require local storage.

The CLI updates exactly one `<!-- MEMBRIDGE:START -->` / `<!-- MEMBRIDGE:END -->` block in `AGENTS.md`.
It preserves bytes outside the block, creates a backup, rejects malformed/duplicate markers and
symlinked targets, uses a local write lock, detects concurrent file changes, and replaces the file atomically.
The user starts a new Codex session afterwards. Stdout and preview modes do not change instructions.

## API and security boundary

The gateway exposes `/imports`, `/jobs`, `/search`, `/handoff`, and evidence lookup. Internal services
expose `/normalize`, `/extract`, and `/embed`. All data routes support a shared bearer token. Binding
beyond loopback through the CLI requires the token. Request bodies are limited to 8 MiB. Health
endpoints remain unauthenticated. Validation errors and worker error records omit transcript input.

This is a single-user service: a shared token is not multi-tenant authorization. Use a private
network or HTTPS with appropriate ingress controls for non-local deployment.

Redaction runs on the client before transmission and during ingestion. Redacted transcripts are
retained to support processing and evidence. Regex-based redaction is best effort and does not remove
all sensitive code or personal data. Providers see the redacted content sent for extraction/embedding.

## Deployment and remaining work

Compose runs all roles and persistent Postgres; Minikube manifests provide HTTP probes, resources,
a StatefulSet and persistent volume. Startup schema creation is serialized in Postgres. There is no
schema migration framework yet; future upgrades must add migrations.

The local dashboard is a separate project-bound FastAPI surface with static packaged assets,
per-run bearer authorization, same-origin reads and no external scripts. It is a
read-only monitoring surface: memory mutations, handoffs and sync control remain in
the CLI/API service. The dashboard rejects legacy action requests with HTTP 405. Project-local command
assets bridge OpenCode commands and Codex skills to a fixed CLI workflow. Forgetting deletes
session-associated records and leaves import-suppression markers without transcript text.
Retention policies expire old revisions and suppress unchanged reimports. Local forgetting
clears generated handoff text/backups; source app histories and separately exported reports
remain outside MemBridge's deletion boundary. Database record deletion is not forensic disk erasure.

Not yet implemented: host-native capture plugins, automatic prompt prefetch,
general semantic contradiction reasoning, bulk reindex, and
multi-user identities. See DATASET.md for quality-evaluation goals beyond functional tests.
