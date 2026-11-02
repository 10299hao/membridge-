"""Explicit continuation from committed storage, without a running source host."""

from datetime import datetime
from pathlib import Path

import click
import typer


def local_client(project):
    from membridge.cli import Client

    client = Client(Path(project))
    if client.server:
        raise ValueError("Saved-session resume currently uses local storage; use switch for gateway mode")
    return client


def candidates(project):
    client = local_client(project)
    try:
        return client.engine.store.resumable(client.project)
    finally:
        client.engine.store.engine.dispose()


def prepare(project, target, session_id):
    from membridge.handoff import inject

    if target not in {"codex", "opencode"}:
        raise ValueError("Choose codex or opencode")
    client = local_client(project)
    try:
        rows = client.engine.store.resumable(client.project)
        if session_id is None:
            if len(rows) != 1:
                raise ValueError("Select a saved session with --session; run resume codex --list or the menu")
            selected = rows[0]
        else:
            selected = next((row for row in rows if row["id"] == session_id), None)
            if selected is None:
                raise ValueError("Saved session is unavailable in this project; save and process it first")
        result = client.engine.handoff(client.project, source_job=selected["job_id"])
        path = inject(Path(project), result["brief"], filename="AGENTS.md")
        return {
            "session": selected,
            "target": target,
            "handoff": str(path),
            "note": "Uses the last saved committed revision; unsaved source messages are not included.",
            "verification": result["verification"],
        }
    finally:
        client.engine.store.engine.dispose()


def choose(label, values):
    for index, value in enumerate(values, 1):
        typer.echo(f"  {index}. {value}")
    return typer.prompt(label, type=click.IntRange(1, len(values))) - 1


def menu(project):
    from membridge.config import project_key

    client = local_client(project)
    try:
        projects = [str(Path(project).resolve())]
        projects += [
            p
            for p in client.engine.store.saved_projects()
            if project_key(p) != project_key(project) and Path(p).is_dir()
        ]
    finally:
        client.engine.store.engine.dispose()
    typer.echo("MemBridge - continue your saved work")
    selected = choose("Choose project", projects + ["Enter another project path", "Exit"])
    if selected == len(projects) + 1:
        return
    directory = (
        Path(typer.prompt("Project path")).expanduser()
        if selected == len(projects)
        else Path(projects[selected])
    )
    action = choose("Choose action", ["Continue in Codex", "Continue in OpenCode", "Open dashboard", "Exit"])
    if action == 3:
        return
    if action == 2:
        from membridge.cli import dashboard

        dashboard(project=directory, port=8765, open_browser=True)
        return
    rows = candidates(directory)
    if not rows:
        typer.echo(
            "No saved sessions yet. In your coding tool, use the switch command, or save with membridge save --from opencode (or codex)."
        )
        return
    labels = [
        f"{r['title'] or r['source_session']} [{r['source_tool']}] - {r['task']} - saved {datetime.fromtimestamp(r['updated']).isoformat(timespec='seconds')} - {r['id']}"
        for r in rows
    ]
    chosen = rows[choose("Choose saved session", labels)]
    typer.echo("This continues the last saved revision. Unsaved messages are not included.")
    result = prepare(directory, ("codex", "opencode")[action], chosen["id"])
    typer.echo(f"Handoff ready: {result['handoff']}")
    from membridge.launch import launch_tool

    raise typer.Exit(launch_tool(result["target"], directory))
