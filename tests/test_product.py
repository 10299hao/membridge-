import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from typer.testing import CliRunner

from membridge.adapters import parse_codex
from membridge.cli import Client, app
from membridge.config import Settings, project_key
from membridge.dashboard import create_dashboard
from membridge.engine import Engine
from membridge.handoff import clear_generated, inject
from membridge.integrations import install, uninstall
from membridge.models import Message, Session
from membridge.repository import snapshot, verify
from membridge.store import commits, jobs, memories, sessions
from membridge.sync import paths, start, status, stop, watch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("MEMBRIDGE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "memory"))
    result = tmp_path / "project"
    result.mkdir()
    return result


def save(engine, project, text, sid="one"):
    session = Session(
        session_id=sid,
        source_tool="generic",
        project=project_key(project),
        messages=[Message(id="m", role="user", content=text)],
    )
    session.metadata["repository"] = snapshot(project, session.messages)
    job = engine.store.enqueue(session)
    engine.process_one(job["id"])
    assert engine.store.job(job["id"], session.project)["state"] == "complete"
    return session, job


def test_file_and_branch_changes_are_visible_in_handoff(project):
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    (project / "src").mkdir()
    (project / "src/api.py").write_text("original")
    engine = Engine(Settings.load())
    value, job = save(engine, project, "Goal: Maintain the API.\nUpdated src/api.py with validation.")
    rows = engine.search(value.project, "api.py")
    assert (
        verify(project, rows, engine.store.contexts(value.project))["memories"][0]["state"]
        == "observations_unchanged"
    )
    (project / "src/api.py").write_text("changed")
    assert "changed_file" in engine.handoff(value.project, source_job=job["id"])["brief"]
    (project / "src/api.py").unlink()
    assert "missing_file" in engine.handoff(value.project, source_job=job["id"])["brief"]
    subprocess.run(["git", "-C", str(project), "symbolic-ref", "HEAD", "refs/heads/other"], check=True)
    assert "branch_changed" in engine.handoff(value.project, source_job=job["id"])["brief"]


def test_new_observation_does_not_reset_identical_session_baseline(project):
    (project / "src").mkdir()
    path = project / "src/test.py"
    path.write_text("before")
    engine = Engine(Settings.load())
    value, first = save(engine, project, "Updated src/test.py with a test.")
    path.write_text("after")
    value.metadata["repository"] = snapshot(project, value.messages)
    assert engine.store.enqueue(value)["id"] == first["id"]
    assert "changed_file" in engine.handoff(value.project, source_job=first["id"])["brief"]


def test_automatic_decision_replacement_preserves_history_and_survives_refresh(project):
    engine = Engine(Settings.load())
    first, job = save(engine, project, "Decision: We chose SQLite.")
    old = engine.search(first.project, "SQLite")[0]
    save(engine, project, "Decision: We now use PostgreSQL.", sid="two")
    assert not engine.search(first.project, "SQLite")
    assert engine.store.evidence(old["id"], first.project)
    refreshed = engine.store.enqueue(first, refresh=True)
    engine.process_one(refreshed["id"])
    assert not engine.search(first.project, "SQLite")
    assert engine.search(first.project, "PostgreSQL")[0]["status"] == "active"
    save(engine, project, "Decision: Maybe we should use MySQL.", sid="uncertain")
    assert engine.search(first.project, "PostgreSQL")[0]["status"] == "active"


def test_task_completion_accepts_clear_action_aliases(project):
    engine = Engine(Settings.load())
    value, _ = save(engine, project, "TODO: Add cursor validation.")
    save(engine, project, "Completed: Implement cursor validation.", sid="done")
    assert all(row["status"] == "completed" for row in engine.search(value.project, "cursor"))


def test_reported_implementation_completes_task_without_claiming_negative_changes(project):
    engine = Engine(Settings.load())
    value, _ = save(engine, project, "TODO: Add cursor validation.")
    followup = Session(
        session_id="done",
        source_tool="generic",
        project=value.project,
        messages=[
            Message(
                id="a", role="assistant", content="Implemented cursor validation.\nNo files were changed."
            )
        ],
    )
    job = engine.store.enqueue(followup)
    engine.process_one(job["id"])
    assert all(row["status"] == "completed" for row in engine.search(value.project, "cursor"))
    assert not any(row["type"] == "file_change" for row in engine.search(value.project, "changed"))


def test_live_scoring_does_not_count_tool_output_as_agent_recall():
    from membridge.live_evaluation import assistant_text

    events = "\n".join(
        [
            json.dumps({"type": "tool", "part": {"type": "tool", "text": "secret-codeword"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "I do not know."}}),
        ]
    )
    assert "secret-codeword" not in assistant_text(events)


def test_open_window_quotes_project_paths_without_shell_interpolation(project, monkeypatch):
    from types import SimpleNamespace

    from membridge.launch import open_tool_window

    invocations = []
    monkeypatch.setattr("membridge.launch.shutil.which", lambda _: "C:/Program Files/tool.cmd")
    monkeypatch.setattr(
        "membridge.launch.subprocess.Popen",
        lambda args, **kw: invocations.append((args, kw)) or SimpleNamespace(pid=12),
    )
    result = open_tool_window("codex", project)
    assert result["pid"] == 12
    script = base64.b64decode(invocations[0][0][-1]).decode("utf-16-le")
    assert "Set-Location -LiteralPath" in script and str(project.resolve()) in script
    assert "--auto" not in script and "bypass-approvals" not in script


def test_forget_erases_all_raw_copies_and_blocks_watcher_reimport(project):
    engine = Engine(Settings.load())
    value, job = save(engine, project, "Decision: The fictional codeword is PRIVATE_TEST.")
    original = engine.search(value.project, "PRIVATE_TEST")[0]
    assert not engine.store.forget(value.project)["applied"]
    assert engine.store.evidence(original["id"], value.project)
    sid = engine.store.session_list(value.project)[0]["id"]
    result = engine.store.forget(value.project, sid, apply=True)
    assert result["memories"] == 1
    assert engine.store.evidence(original["id"], value.project) is None
    with engine.store.engine.connect() as conn:
        for table in (sessions, jobs, memories, commits):
            assert not conn.execute(select(table).where(table.c.project == value.project)).first()
    with pytest.raises(ValueError, match="forgotten"):
        engine.store.enqueue(value)
    engine.store.restore(value.project, sid)
    assert engine.store.enqueue(value)["state"] == "pending"


def test_source_handoff_includes_user_correction_and_session_forget_erases_derivatives(project):
    engine = Engine(Settings.load())
    source, job = save(engine, project, "Decision: The fictional codeword is OLD_VALUE.")
    old = engine.search(source.project, "OLD_VALUE")[0]
    replacement = engine.store.correct(source.project, old["id"], "Decision: The codeword is NEW_VALUE.")
    text = engine.handoff(source.project, source_job=job["id"])["brief"]
    assert "NEW_VALUE" in text and "OLD_VALUE" not in text
    assert "Source (user-correction): membridge:" in text
    sid = next(s["id"] for s in engine.store.session_list(source.project) if s["source_tool"] == "generic")
    preview = engine.store.forget(source.project, sid)
    assert preview["sessions"] == 2
    engine.store.forget(source.project, sid, apply=True)
    assert not engine.search(source.project, "NEW_VALUE")
    assert engine.store.evidence(replacement["memory_id"], source.project) is None


def test_retention_is_previewable_expires_and_does_not_reimport_identical_source(project):
    engine = Engine(Settings.load())
    value, _ = save(engine, project, "TODO: Add the retention test.")
    with engine.store.engine.begin() as conn:
        conn.execute(update(sessions).values(updated=time.time() - 40 * 86400))
    preview = engine.store.retention(value.project, 30)
    assert preview["expired_sessions"] == 1 and engine.store.session_list(value.project)
    engine.store.retention(value.project, 30, apply=True)
    assert not engine.store.session_list(value.project)
    with pytest.raises(ValueError, match="expired"):
        engine.store.enqueue(value)
    value.messages.append(Message(id="new", role="user", content="TODO: Add a fresh requirement."))
    assert engine.store.enqueue(value)["state"] == "pending"


def test_forgetting_clears_managed_files_and_backups_but_preserves_code(project):
    (project / "AGENTS.md").write_text("Keep my instructions.\n")
    (project / "main.py").write_text("print('keep')")
    inject(project, "PRIVATE_TEST")
    inject(project, "PRIVATE_TEST_2")
    assert (project / ".membridge/backups").exists()
    clear_generated(project)
    assert "PRIVATE_TEST" not in (project / "AGENTS.md").read_text()
    assert "Keep my instructions." in (project / "AGENTS.md").read_text()
    assert not list((project / ".membridge/backups").glob("*.md"))
    assert (project / "main.py").read_text() == "print('keep')"


def test_integrations_install_idempotently_and_preserve_user_edits(project):
    result = install(project)
    assert result["opencode"] == "/switch codex"
    assert (project / ".agents/skills/membridge-switch/SKILL.md").exists()
    install(project)
    command = project / ".opencode/commands/switch.md"
    command.write_text("User custom command")
    with pytest.raises(ValueError, match="preserved"):
        install(project)
    removed = uninstall(project)
    assert ".opencode/commands/switch.md" in removed["preserved"]
    assert command.read_text() == "User custom command"


def test_generated_in_chat_bridge_prepares_handoff_and_opens_destination(project, monkeypatch):
    import runpy

    from membridge.cli import READERS

    class Reader:
        def __init__(self, directory):
            assert directory == project

        def list_sessions(self):
            return [{"id": "active"}]

        def export(self, sid):
            return {
                "info": {"id": "active"},
                "messages": [
                    {
                        "id": "m",
                        "type": "user",
                        "text": "Current task: Test in-chat switching.\nTODO: Verify the handoff.",
                    }
                ],
            }

    opened = []
    install(project)
    monkeypatch.setitem(READERS, "opencode", Reader)
    monkeypatch.setattr(
        "membridge.launch.open_tool_window",
        lambda target, folder: opened.append((target, folder)) or {"opened": target},
    )
    script = project / ".membridge/host_switch.py"
    monkeypatch.setattr(sys, "argv", [str(script), "opencode", "codex"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")
    assert result.value.code == 0
    assert opened == [("codex", project)]
    assert "Test in-chat switching" in (project / "AGENTS.md").read_text()


@pytest.mark.skipif(os.name != "nt", reason="Windows login integration")
def test_login_startup_changes_only_the_project_entry(project, monkeypatch):
    from types import SimpleNamespace

    from membridge.autostart import configure

    saved = {"unrelated": "keep"}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    fake = SimpleNamespace(
        HKEY_CURRENT_USER=0,
        REG_SZ=1,
        CreateKey=lambda *args: Key(),
        SetValueEx=lambda key, name, _, kind, value: saved.update({name: value}),
        DeleteValue=lambda key, name: saved.pop(name),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    result = configure(project, True)
    assert "sync resume" in saved[result["entry"]]
    configure(project, False)
    assert saved == {"unrelated": "keep"}


def test_codex_injected_instructions_do_not_become_memory():
    value = parse_codex(
        {
            "records": [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": "# AGENTS.md instructions for C:/elsewhere\n<INSTRUCTIONS>Decision: use OLD_VALUE.</INSTRUCTIONS>",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": "Current task: Keep this actual conversation.",
                    },
                },
            ]
        },
        "project",
    )
    assert len(value.messages) == 1 and "OLD_VALUE" not in value.model_dump_json()


def test_dashboard_is_authenticated_project_scoped_and_read_only(project):
    client = Client(project)
    value, _ = save(client.engine, project, "Decision: Use the fictional project codeword.")
    old = client.search("codeword", 10)[0]
    headers = {"Authorization": "Bearer private-test"}
    foreign = project.parent / "foreign-dashboard-project"
    foreign.mkdir()
    other, _ = save(client.engine, foreign, "Decision: This is a separate project's secret.")
    foreign_id = client.engine.search(other.project, "secret")[0]["id"]
    with TestClient(create_dashboard(project, "private-test", client)) as browser:
        page = browser.get("/")
        assert page.status_code == 200 and "Read-only" in page.text
        assert browser.get("/api/state").status_code == 401
        response = browser.get("/api/state", headers=headers)
        assert response.status_code == 200
        assert response.json()["read_only"] is True
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.json()["memories"][0]["id"] == old["id"]
        before = response.json()
        assert browser.get("/api/evidence/" + old["id"], headers=headers).json()["messages"]
        assert browser.get("/api/evidence/" + foreign_id, headers=headers).status_code == 404
        assert (
            browser.get("/api/state", headers={**headers, "Origin": "https://evil.test"}).status_code == 403
        )
        assert browser.post("/api/action", json={"action": "forget"}).status_code == 401
        for name in (
            "save",
            "handoff",
            "sync-start",
            "sync-stop",
            "integrate",
            "correct",
            "resolve",
            "forget",
            "retention",
            "restore",
        ):
            result = browser.post(
                "/api/action",
                headers=headers,
                json={"action": name, "memory_id": old["id"], "text": "Replacement", "apply": True},
            )
            assert result.status_code == 405, name
            assert "read-only" in result.json()["detail"]
        for method in ("PUT", "PATCH", "DELETE"):
            assert browser.request(method, "/api/action", headers=headers).status_code == 405
        after = browser.get("/api/state", headers=headers).json()
        # Retrieval scores decay with time; compare persisted content, not this derived rank.
        for payload in (before, after):
            for field in ("memories", "history"):
                for memory in payload[field]:
                    memory.pop("score", None)
        for field in ("memories", "history", "sessions", "jobs", "checkpoint", "retention"):
            assert after[field] == before[field]
        assert not (project / "AGENTS.md").exists()
        script = browser.get("/app.js").text
        assert "/api/action" not in script and "innerHTML" not in script
        assert "data-switch" not in page.text and "save-form" not in page.text
        assert client.engine.store.evidence(old["id"], value.project)


@pytest.mark.skipif(os.name != "nt", reason="Windows installer")
def test_installer_launches_without_activation_and_uninstalls_owned_files(tmp_path):
    install_dir = tmp_path / "installed app"
    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "install.ps1"),
        "-InstallDir",
        str(install_dir),
        "-PythonPath",
        sys.executable,
        "-SkipDependencies",
        "-NoPath",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    launcher = install_dir / "bin/membridge.cmd"
    checked = subprocess.run(
        [str(launcher), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert checked.returncode == 0 and "dashboard" in checked.stdout
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/uninstall.ps1"),
            "-InstallDir",
            str(install_dir),
            "-NoPath",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not launcher.exists() and Path(sys.executable).exists()


def test_stale_lock_recovers_but_live_worker_is_not_replaced(project, monkeypatch):
    from membridge.lease import FileLease

    lock, _, _ = paths(project, "aider")
    lock.mkdir()
    (lock / "state.json").write_text(json.dumps({"pid": 999999, "heartbeat": 0}))
    (project / ".aider.chat.history.md").write_text("#### Goal: Recover safely\nTODO: Add crash recovery.")
    with FileLease(lock.with_suffix(".mutex")):
        with pytest.raises(ValueError, match="live process"):
            watch(project, "aider", once=True)
    state = watch(project, "aider", once=True)
    assert state["processed"] == 1
    assert not lock.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process supervisor")
def test_supervisor_restarts_a_crashed_owned_worker(project):
    (project / ".aider.chat.history.md").write_text("#### Goal: Recover\nTODO: Add a supervisor test.")
    state = start(project, "aider", 1, False)
    try:
        assert state["state"] in {"running", "starting"}
        lock, _, _ = paths(project, "aider")
        deadline = time.monotonic() + 10
        while not (lock / "state.json").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        pid = json.loads((lock / "state.json").read_text())["pid"]
        # This PID belongs to the isolated worker started immediately above.
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=True)
        deadline = time.monotonic() + 15
        new_pid = pid
        while time.monotonic() < deadline:
            observed = status(project, "aider")
            new_pid = observed.get("pid", pid)
            if new_pid != pid and observed.get("state") == "running":
                break
            time.sleep(0.2)
        assert new_pid != pid
    finally:
        stop(project, "aider")
        deadline = time.monotonic() + 10
        while paths(project, "aider")[0].exists() and time.monotonic() < deadline:
            time.sleep(0.1)


def test_cli_forget_preview_requires_explicit_apply(project):
    engine = Engine(Settings.load())
    value, _ = save(engine, project, "TODO: Keep until explicitly forgotten.")
    runner = CliRunner()
    assert runner.invoke(app, ["forget", "--project", str(project)]).exit_code == 0
    assert engine.search(value.project, "forgotten")
    result = runner.invoke(app, ["forget", "--project", str(project), "--yes"])
    assert result.exit_code == 0, result.output
    assert not engine.search(value.project, "forgotten")


def test_concurrent_capture_cannot_restore_a_forgotten_project(project):
    import threading

    from sqlalchemy import event

    engine = Engine(Settings.load())
    source, _ = save(engine, project, "TODO: Keep this until forgetting.")
    selected, release = threading.Event(), threading.Event()
    errors = []

    def pause_selection(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == "forget-owner" and "FROM sessions" in statement:
            selected.set()
            assert release.wait(5)

    event.listen(engine.store.engine, "after_cursor_execute", pause_selection)

    def erase():
        try:
            engine.store.forget(source.project, apply=True)
        except Exception as exc:
            errors.append(exc)

    def capture():
        try:
            fresh = source.model_copy(update={"session_id": "brand-new-concurrent-session"})
            engine.store.enqueue(fresh)
        except ValueError as exc:
            errors.append(exc)

    owner = threading.Thread(target=erase, name="forget-owner")
    owner.start()
    assert selected.wait(5)
    incoming = threading.Thread(target=capture)
    incoming.start()
    release.set()
    owner.join(10)
    incoming.join(10)
    event.remove(engine.store.engine, "after_cursor_execute", pause_selection)
    assert not owner.is_alive() and not incoming.is_alive()
    assert len(errors) == 1 and "forgotten" in str(errors[0])
    assert not engine.store.session_list(source.project)
