import functools
import json
import os
import shutil
import time
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from membridge.adapters import PARSERS, READERS, adapter_catalog, parse
from membridge.config import Settings, project_key
from membridge.engine import Engine
from membridge.formats import load_transcript
from membridge.handoff import inject
from membridge.privacy import redact
from membridge.tools import CONTEXT_FILES, context_filename, tool_name

app = typer.Typer(
    help="Share project memory across AI tools using native readers or portable transcript exports.",
    no_args_is_help=False,
)
adapters_app = typer.Typer(help="Supported input and output adapters.")
jobs_app = typer.Typer(help="Inspect and retry durable processing jobs.")
sync_app = typer.Typer(help="Start, inspect or stop explicitly enabled project synchronization.")
app.add_typer(adapters_app, name="adapters")
app.add_typer(jobs_app, name="jobs")
app.add_typer(sync_app, name="sync")
Project = Annotated[Path, typer.Option(help="Project directory; defaults to the current directory.")]


def safe(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except typer.Exit:
            raise
        except ValidationError:
            typer.echo("Error: invalid session data; check the documented export format.", err=True)
        except httpx.HTTPStatusError as exc:
            typer.echo(
                f"Error: service returned HTTP {exc.response.status_code}; check server and provider configuration.",
                err=True,
            )
        except httpx.HTTPError:
            typer.echo("Error: cannot reach the configured service.", err=True)
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
        except Exception as exc:
            typer.echo(
                f"Error: {type(exc).__name__}; operation failed. No raw transcript is included in this error.",
                err=True,
            )
        raise typer.Exit(1)

    return wrapped


class Client:
    def __init__(self, project):
        self.directory = project.resolve()
        if not self.directory.is_dir():
            raise ValueError("Project directory does not exist")
        self.project = project_key(project)
        self.settings = Settings.load()
        config_file = self.directory / ".membridge" / "config.json"
        self.config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
        self.server = os.getenv("MEMBRIDGE_SERVER") or self.config.get("server", "")
        self.engine = None if self.server else Engine(self.settings)

    def request(self, method, path, body=None, params=None):
        headers = {"Authorization": "Bearer " + self.settings.api_key} if self.settings.api_key else {}
        with httpx.Client(timeout=120) as client:
            response = client.request(
                method, self.server.rstrip("/") + path, json=body, params=params, headers=headers
            )
            response.raise_for_status()
            return response.json()

    def job(self, jid):
        return (
            self.request("GET", f"/jobs/{jid}", params={"project": self.project})
            if self.server
            else self.engine.store.job(jid, self.project)
        )

    def search(self, query, limit):
        if self.server:
            return self.request("POST", "/search", {"project": self.project, "query": query, "limit": limit})[
                "memories"
            ]
        return self.engine.search(self.project, query, limit)


def show(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


@app.callback(invoke_without_command=True)
@safe
def entry(ctx: typer.Context, project: Project = Path(".")):
    """Run without a command for the project and continuation menu."""
    if ctx.invoked_subcommand is None:
        from membridge.continuation import menu

        menu(project)


@app.command()
@safe
def resume(
    target: Annotated[str, typer.Argument(help="Continue in codex or opencode.")],
    project: Project = Path("."),
    session: Annotated[str | None, typer.Option(help="Saved session ID from resume --list.")] = None,
    list_saved: Annotated[
        bool, typer.Option("--list", help="List saved sessions without writing files.")
    ] = False,
    launch: bool = False,
    open_window: bool = False,
):
    """Continue from saved memory, even when the original coding tool is closed."""
    from membridge.continuation import candidates, prepare

    if list_saved:
        show(candidates(project))
        return
    result = prepare(project, target, session)
    show(result)
    if launch:
        from membridge.launch import launch_tool

        raise typer.Exit(launch_tool(target, project))
    if open_window:
        from membridge.launch import open_tool_window

        show(open_tool_window(target, project))


@app.command()
@safe
def init(project: Project = Path("."), server: str = ""):
    """Initialize project configuration without changing coding-agent instructions."""
    directory = project.resolve() / ".membridge"
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "config.json"
    if config.exists():
        raise ValueError("Project is already initialized; edit .membridge/config.json to change the server")
    config.write_text(
        json.dumps(
            {"sources": list(PARSERS), "targets": list(CONTEXT_FILES), "server": server},
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo(
        f"Initialized {project.resolve()}. OpenCode: {'detected' if shutil.which('opencode') else 'not on PATH'}. Run `membridge adapters list` for native and export routes."
    )


@adapters_app.command("list")
def adapters_list():
    show(adapter_catalog())


@app.command()
@safe
def sessions(project: Project = Path("."), source: Annotated[str, typer.Option("--from")] = "opencode"):
    """List native sessions belonging to this exact project."""
    source = tool_name(source)
    if source not in READERS:
        raise ValueError(f"No native session discovery for {source}; save an exported transcript with --file")
    show(READERS[source](project).list_sessions())


@app.command()
@safe
def save(
    project: Project = Path("."),
    source: Annotated[str, typer.Option("--from")] = "opencode",
    file: Annotated[
        Path | None, typer.Option(help="Session JSON, JSONL, Markdown or plain-text export.")
    ] = None,
    session: Annotated[
        str | None, typer.Option(help="Source session ID; defaults to latest in this project.")
    ] = None,
    wait: Annotated[bool, typer.Option("--wait/--no-wait")] = True,
    refresh: Annotated[
        bool, typer.Option(help="Re-extract an already processed session after changing models.")
    ] = False,
    timeout: Annotated[int, typer.Option(min=1, max=3600)] = 300,
    input_format: Annotated[
        str,
        typer.Option("--format", help="auto, generic, or a native parser name; source label is preserved."),
    ] = "auto",
):
    """Import a session, redact it, and extract memory. Repeated identical imports are idempotent."""
    job = save_session(project, source, file, session, wait, refresh, timeout, input_format)
    show(job)
    check_job(job, wait)


def check_job(job, wait=True):
    if job["state"] == "failed":
        typer.echo("Processing failed. Inspect `membridge jobs list` for details.", err=True)
        raise typer.Exit(1)
    if wait and job["state"] in {"pending", "processing"}:
        typer.echo("Processing continues in the queue. Inspect `membridge jobs list`.", err=True)
        raise typer.Exit(2)


def save_session(project, source, file, session, wait, refresh, timeout, input_format):
    source = tool_name(source)
    input_format = tool_name(input_format)
    if input_format != "auto" and input_format not in PARSERS:
        raise ValueError("Unknown parser format; run `membridge adapters list`")
    if file and session:
        raise ValueError("Choose either --file or --session")
    client = Client(project)
    if file:
        data = load_transcript(file)
    elif source in READERS:
        adapter = READERS[source](project)
        available = adapter.list_sessions()
        if not available:
            raise ValueError(f"No {source} sessions match this project. Use --project or --file explicitly.")
        session_id = session or available[0]["id"]
        if session_id not in {r["id"] for r in available}:
            raise ValueError("Session is not in this project's recent session list; export it and use --file")
        data = adapter.export(session_id)
    else:
        raise ValueError(
            f"{source} uses exported transcripts: add --file conversation.json (or .jsonl/.md/.txt)"
        )
    data = redact(data)  # Always before the HTTP boundary.
    normalized = parse(data, source, client.project, input_format)
    from membridge.repository import snapshot

    repository = snapshot(project, normalized.messages)
    if client.server:
        job = client.request(
            "POST",
            "/imports",
            {
                "source_tool": source,
                "project": client.project,
                "data": data,
                "refresh": refresh,
                "input_format": input_format,
                "repository": repository,
            },
        )
    else:
        normalized.metadata["repository"] = repository
        job = client.engine.store.enqueue(normalized, refresh=refresh)
    if wait:
        deadline = time.monotonic() + timeout
        while job["state"] in {"pending", "processing"} and time.monotonic() < deadline:
            if client.engine:
                client.engine.process_one(job["id"])
            else:
                time.sleep(1)
            job = client.job(job["id"])
    return job


@app.command()
@safe
def switch(
    target: Annotated[str, typer.Argument(help="Tool to continue in: codex or opencode.")],
    project: Project = Path("."),
    source: Annotated[
        str | None, typer.Option("--from", help="Source tool; defaults to the other tool.")
    ] = None,
    session: Annotated[
        str | None, typer.Option(help="Source session ID; defaults to latest in this project.")
    ] = None,
    timeout: Annotated[int, typer.Option(min=1, max=3600)] = 300,
    launch: Annotated[
        bool, typer.Option("--launch", help="Open the next tool after saving the handoff.")
    ] = False,
    open_window: Annotated[
        bool, typer.Option(help="Open the destination in a separate Windows terminal.")
    ] = False,
):
    """Save the latest conversation and prepare memory for the next tool in one step."""
    target = tool_name(target)
    if target not in {"codex", "opencode"}:
        raise ValueError("Switch supports codex and opencode. Use save and handoff for other destinations.")
    source = tool_name(source) if source else {"codex": "opencode", "opencode": "codex"}[target]
    if source not in READERS:
        raise ValueError("Switch needs a native source reader; use save --file for exported conversations.")
    if source == target:
        raise ValueError("Choose a destination different from the source tool.")
    typer.echo(f"Saving the latest {source} conversation for this project...")
    job = save_session(project, source, None, session, True, False, timeout, "auto")
    check_job(job)
    if job["state"] != "complete":
        raise ValueError(
            "This session revision was superseded. Run switch again to save the latest revision."
        )
    result = handoff(
        project=project,
        target=target,
        query="",
        preview=False,
        limit=30,
        max_chars=6000,
        source_job=job["id"],
    )
    typer.echo(
        f"Prepared {result['decisions']} decisions, {result['unfinished_tasks']} unfinished tasks "
        f"and {result['checkpoint_items']} checkpoint items."
    )
    typer.echo(f"Ready. Run `{target}` in {project.resolve()} and ask it to continue from the handoff.")
    if launch:
        from membridge.launch import launch_tool

        raise typer.Exit(launch_tool(target, project))
    if open_window:
        from membridge.launch import open_tool_window

        show(open_tool_window(target, project))


@app.command()
@safe
def search(
    query: str, project: Project = Path("."), limit: Annotated[int, typer.Option(min=1, max=100)] = 12
):
    """Search memory within this project, with evidence IDs."""
    show(Client(project).search(query, limit))


@app.command()
@safe
def handoff(
    project: Project = Path("."),
    target: Annotated[str, typer.Option("--to")] = "codex",
    query: str = "",
    preview: bool = False,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 30,
    max_chars: Annotated[int, typer.Option(min=1000, max=32000)] = 6000,
    source_job: Annotated[str | None, typer.Option(hidden=True)] = None,
):
    """Write a known context file, or print a brief to paste into any other AI tool."""
    target = tool_name(target)
    client = Client(project)
    if client.server:
        result = client.request(
            "POST",
            "/handoff",
            {
                "project": client.project,
                "query": query,
                "limit": limit,
                "max_chars": max_chars,
                "source_job": source_job,
            },
        )
    else:
        result = client.engine.handoff(client.project, query, limit, max_chars, source_job)
    filename = "MEMBRIDGE_HANDOFF.md" if target == "file" else context_filename(target)
    if preview or target == "stdout" or filename is None:
        typer.echo(result["brief"])
        if filename is None and target != "stdout" and not preview:
            typer.echo(
                f"Paste this brief into {target}; no automatic injection is configured. Use --to file for a portable Markdown file.",
                err=True,
            )
    else:
        path = inject(project, result["brief"], filename=filename)
        if target == "aider":
            action = "Load it with `aider --read MEMBRIDGE_HANDOFF.md` or `/read MEMBRIDGE_HANDOFF.md`."
        elif target == "file":
            action = "Attach or paste this file in your chosen AI tool."
        else:
            action = f"Start a new {target} session in this project."
        typer.echo(f"Wrote {result['chars']} characters to {path}. {action}")
    return result


@app.command()
@safe
def checkpoint(project: Project = Path(".")):
    """Show the latest cited task checkpoint for this project."""
    client = Client(project)
    show(
        client.request("GET", "/checkpoint", params={"project": client.project})
        if client.server
        else client.engine.store.checkpoint(client.project)
    )


@app.command()
@safe
def resolve(
    memory_id: str,
    action: Annotated[str, typer.Option(help="supersedes, completed, or disputed")],
    replacement: Annotated[str | None, typer.Option(help="New active memory ID for supersedes.")] = None,
    project: Project = Path("."),
):
    """Record a changed decision, completed task or disputed claim; keep its evidence."""
    client = Client(project)
    show(
        client.request(
            "POST",
            "/resolve",
            {
                "project": client.project,
                "memory_id": memory_id,
                "action": action,
                "replacement_id": replacement,
            },
        )
        if client.server
        else client.engine.store.resolve(client.project, memory_id, action, replacement)
    )


@app.command()
@safe
def history(project: Project = Path(".")):
    """List local project memory, including superseded claims."""
    client = Client(project)
    if client.server:
        show(client.request("GET", "/history", params={"project": client.project}))
    else:
        show(client.engine.store.search(client.project, "", limit=100, history=True))


@app.command()
@safe
def mcp(project: Project = Path(".")):
    """Serve read-only project memory over MCP stdio (install the mcp extra first)."""
    from membridge.mcp_server import create_server

    create_server(project).run(transport="stdio")


@app.command()
@safe
def watch(
    project: Project = Path("."),
    source: Annotated[str, typer.Option("--from")] = "opencode",
    interval: Annotated[int, typer.Option(min=1, max=3600)] = 5,
    once: bool = False,
    allow_model: Annotated[
        bool, typer.Option(help="Allow configured services/models to process synced content.")
    ] = False,
    managed: Annotated[bool, typer.Option(hidden=True)] = False,
):
    """Continuously save sessions in this exact project; Ctrl+C stops the foreground watcher."""
    from membridge.sync import watch as run_watch

    show(run_watch(project, tool_name(source), interval, once, allow_model, managed))


@app.command(hidden=True)
@safe
def supervise(
    project: Project = Path("."),
    source: Annotated[str, typer.Option("--from")] = "opencode",
    interval: int = 5,
    allow_model: bool = False,
):
    from membridge.sync import supervise as run_supervisor

    run_supervisor(project, tool_name(source), interval, allow_model)


@sync_app.command("resume")
@safe
def sync_resume(project: Project = Path(".")):
    """Resume previously enabled sources; used by optional login startup."""
    from membridge.sync import resume

    show(resume(project))


@sync_app.command("autostart")
@safe
def sync_autostart(project: Project = Path("."), enable: bool = True):
    """Enable/disable this project's current-user Windows login startup."""
    from membridge.autostart import configure

    show(configure(project, enable))


@app.command()
@safe
def integrate(project: Project = Path("."), uninstall: bool = False):
    """Install project-local OpenCode /switch and Codex $membridge-switch integrations."""
    from membridge import integrations

    show(integrations.uninstall(project) if uninstall else integrations.install(project))


@app.command()
@safe
def dashboard(
    project: Project = Path("."),
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    open_browser: bool = False,
):
    """Run the read-only local project dashboard; the printed URL includes its access token."""
    import secrets
    import webbrowser

    import uvicorn

    from membridge.dashboard import create_dashboard

    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/#token={token}"
    typer.echo(f"Project dashboard: {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_dashboard(project, token), host="127.0.0.1", port=port, log_level="warning")


@app.command()
@safe
def verify(project: Project = Path(".")):
    """Compare remembered file hashes and Git branch/commit with this local project."""
    from membridge.repository import verify as inspect_repository

    client = Client(project)
    contexts = (
        client.request("GET", "/contexts", params={"project": client.project})
        if client.server
        else client.engine.store.contexts(client.project)
    )
    show(inspect_repository(project, client.search("", 100), contexts))


@app.command()
@safe
def correct(memory_id: str, text: str, project: Project = Path(".")):
    """Replace a memory with a cited user correction; keep the original as history."""
    client = Client(project)
    show(
        client.request("POST", "/correct", {"project": client.project, "memory_id": memory_id, "text": text})
        if client.server
        else client.engine.store.correct(client.project, memory_id, text)
    )


@app.command("saved-sessions")
@safe
def saved_sessions(project: Project = Path(".")):
    """List imported session IDs for selective deletion."""
    client = Client(project)
    show(
        client.request("GET", "/saved-sessions", params={"project": client.project})
        if client.server
        else client.engine.store.session_list(client.project)
    )


@app.command()
@safe
def forget(project: Project = Path("."), session: str | None = None, yes: bool = False):
    """Preview deletion of a saved session or whole project's memory; --yes applies it."""
    client = Client(project)
    result = (
        client.request("POST", "/forget", {"project": client.project, "session_id": session, "apply": yes})
        if client.server
        else client.engine.store.forget(client.project, session, apply=yes)
    )
    if yes:
        from membridge.handoff import clear_generated
        from membridge.sync import stop

        if not session:
            for source in READERS:
                stop(project, source)
        result["generated_files"] = clear_generated(project)
    show(result)


@app.command()
@safe
def restore(project: Project = Path("."), session: str | None = None):
    """Permit reimport of forgotten sources; this does not recover erased content."""
    client = Client(project)
    show(
        client.request("POST", "/restore", {"project": client.project, "session_id": session})
        if client.server
        else client.engine.store.restore(client.project, session)
    )


@app.command()
@safe
def retention(
    project: Project = Path("."),
    days: Annotated[int | None, typer.Option(min=0, max=36500)] = None,
    apply: bool = False,
):
    """Preview/set an expiry policy and purge old saved sessions. Zero disables expiry."""
    client = Client(project)
    result = (
        client.request("POST", "/retention", {"project": client.project, "days": days, "apply": apply})
        if client.server
        else client.engine.store.retention(client.project, days, apply)
    )
    if apply and result["expired_sessions"]:
        from membridge.handoff import clear_generated

        result["generated_files"] = clear_generated(project)
    show(result)


@sync_app.command("start")
@safe
def sync_start(
    project: Project = Path("."),
    source: Annotated[str, typer.Option("--from")] = "opencode",
    interval: Annotated[int, typer.Option(min=1, max=3600)] = 5,
    allow_model: bool = False,
):
    """Enable a hidden background watcher for this project and source."""
    from membridge.sync import start

    show(start(project, tool_name(source), interval, allow_model))


@sync_app.command("status")
@safe
def sync_status(project: Project = Path("."), source: Annotated[str, typer.Option("--from")] = "opencode"):
    """Inspect watcher heartbeat, completed imports and errors."""
    from membridge.sync import status

    show(status(project, tool_name(source)))


@sync_app.command("stop")
@safe
def sync_stop(project: Project = Path("."), source: Annotated[str, typer.Option("--from")] = "opencode"):
    """Ask the project watcher to stop after its current import; keep saved memory."""
    from membridge.sync import stop

    show(stop(project, tool_name(source)))


@app.command()
@safe
def evaluate(
    output: Annotated[
        Path, typer.Option(help="New directory for results and controlled evaluation prompts.")
    ],
    responses: Annotated[
        Path | None, typer.Option(help="Filled answer template from fresh receiving agents.")
    ] = None,
):
    """Run a synthetic handoff benchmark, or score supplied answers from fresh agents."""
    from membridge.evaluation import evaluate as run_evaluation

    show(run_evaluation(output, responses))


@app.command("evaluate-continuity")
@safe
def evaluate_continuity(output: Annotated[Path, typer.Option()], responses: Path | None = None):
    """Measure restarted continuation, isolation and stale/missing content; score supplied agent answers."""
    from membridge.continuity_evaluation import evaluate_continuity as run_evaluation

    result = run_evaluation(output, responses)
    show(result)
    if result.get("passed", result.get("total")) != result.get("total"):
        raise typer.Exit(1)


@app.command("evaluate-live")
@safe
def evaluate_live(
    output: Annotated[Path, typer.Option()],
    timeout: Annotated[int, typer.Option(min=10, max=600)] = 90,
    opencode_model: str | None = None,
):
    """Attempt a real OpenCode to Codex round trip using only invented project facts."""
    from membridge.live_evaluation import run_live

    show(run_live(output, timeout, opencode_model))


@jobs_app.command("list")
@safe
def jobs_list(project: Project = Path(".")):
    client = Client(project)
    show(
        client.request("GET", "/jobs", params={"project": client.project})
        if client.server
        else client.engine.store.list_jobs(client.project)
    )


@jobs_app.command("retry")
@safe
def jobs_retry(job_id: str, project: Project = Path(".")):
    client = Client(project)
    if client.server:
        show(client.request("POST", f"/jobs/{job_id}/retry", params={"project": client.project}))
    else:
        if not client.engine.store.retry(job_id, client.project):
            raise ValueError("Job is absent or not failed")
        show(client.job(job_id))


@app.command()
@safe
def evidence(memory_id: str, project: Project = Path(".")):
    """Show the redacted source messages supporting a memory."""
    client = Client(project)
    result = (
        client.request("GET", f"/memories/{memory_id}/evidence", params={"project": client.project})
        if client.server
        else client.engine.store.evidence(memory_id, client.project)
    )
    if result is None:
        raise ValueError("Memory not found in this project")
    show(result)


@app.command()
@safe
def process():
    """Drain pending jobs in the local database, including --no-wait saves."""
    engine = Engine(Settings.load())
    count = 0
    while engine.process_one():
        count += 1
    typer.echo(f"Processed {count} attempts. Inspect `membridge jobs list` for outcomes.")


@app.command()
@safe
def worker():
    """Run the durable extraction worker. Use a shared Postgres database to scale replicas."""
    Engine(Settings.load()).run()


@app.command()
@safe
def serve(
    service: Annotated[str, typer.Argument()] = "gateway",
    host: str = "127.0.0.1",
    port: int = 8000,
    with_worker: bool = False,
):
    """Run gateway, ingestion, or ai. Add --with-worker for an all-in-one local API."""
    import uvicorn

    from membridge.api import create_app

    settings = Settings.load()
    if host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_key:
        raise ValueError("Set MEMBRIDGE_API_KEY before binding a service beyond loopback")
    uvicorn.run(create_app(service, settings, with_worker), host=host, port=port, log_level="info")
