import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from membridge.api import create_app
from membridge.cli import READERS, Client, app
from membridge.config import Settings, project_key
from membridge.engine import Engine
from membridge.evaluation import CASES, evaluate, score_responses
from membridge.launch import PROMPT, launch_tool
from membridge.models import Message, Session
from membridge.store import commits
from membridge.sync import check_permission, paths, start, status, stop, watch


@pytest.fixture
def clean_project(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("MEMBRIDGE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "state"))
    project = tmp_path / "project"
    project.mkdir()
    return project


def session(project="project", sid="one", text="Current task: Add pagination.\nTODO: Add cursor validation."):
    return Session(
        session_id=sid,
        source_tool="generic",
        project=project,
        messages=[Message(id="u1", role="user", content=text)],
    )


def process(engine, value, refresh=False):
    job = engine.store.enqueue(value, refresh)
    engine.process_one(job["id"])
    assert engine.store.job(job["id"], value.project)["state"] == "complete"
    return job


def test_append_extracts_only_new_messages_and_edit_reextracts(settings, monkeypatch):
    engine = Engine(settings)
    seen = []
    original_extract = engine.ai.extract

    def record(value):
        seen.append([m.id for m in value.messages])
        return original_extract(value)

    monkeypatch.setattr(engine.ai, "extract", record)
    first = session()
    process(engine, first)
    old = engine.search("project", "cursor")[0]
    appended = first.model_copy(deep=True)
    appended.messages.append(Message(id="a2", role="assistant", content="Decision: We chose PostgreSQL."))
    process(engine, appended)
    assert seen == [["u1"], ["a2"]]
    assert engine.search("project", "cursor")[0]["id"] == old["id"]
    appended.messages[0].content = "Current task: Add offset pagination.\nTODO: Add offset validation."
    process(engine, appended)
    assert seen[-1] == ["u1", "a2"]
    assert not engine.search("project", "cursor")
    assert engine.store.evidence(old["id"], "project")["messages"][0]["id"] == "u1"


def test_signature_change_and_refresh_force_full_extraction(settings, monkeypatch):
    engine = Engine(settings)
    first = session()
    process(engine, first)
    first.messages.append(Message(id="a", role="assistant", content="TODO: Add integration tests."))
    engine.settings.llm_url = "http://changed-provider"
    calls = []
    extract = engine.ai.extract
    monkeypatch.setattr(
        engine.ai, "extract", lambda value: calls.append(len(value.messages)) or extract(value)
    )
    process(engine, first)
    process(engine, first, refresh=True)
    assert calls == [2, 2]


def test_existing_pre_checkpoint_jobs_rebuild_on_next_save(settings):
    from sqlalchemy import delete

    engine = Engine(settings)
    value = session()
    job = process(engine, value)
    with engine.store.engine.begin() as conn:
        conn.execute(delete(commits))
    assert engine.store.enqueue(value)["state"] == "pending"
    engine.process_one(job["id"])
    assert engine.store.checkpoint("project", job["id"])["fields"]["current_task"]


def test_handoff_uses_exact_source_checkpoint_and_respects_budget(settings):
    engine = Engine(settings)
    job = process(
        engine,
        session(
            text=(
                "Current task: Fix cursor validation.\nCompleted: Add request logging.\n"
                "Blocker: Duplicate IDs fail.\nTests: 8 passed; 1 failed.\nNext step: Reject duplicate IDs."
            )
        ),
    )
    process(engine, session(sid="other", text="Decision: We chose unrelated MongoDB storage."))
    result = engine.handoff("project", source_job=job["id"])
    assert "Where work stopped" in result["brief"]
    assert "Duplicate IDs" in result["brief"] and "8 passed" in result["brief"]
    assert "MongoDB" not in result["brief"]
    assert len(engine.handoff("project", source_job=job["id"], max_chars=1000)["brief"]) <= 1000
    with pytest.raises(ValueError):
        engine.handoff("another-project", source_job=job["id"])


def test_new_request_replaces_old_goal_in_checkpoint(settings):
    engine = Engine(settings)
    value = session(text="Goal: Build a task API.")
    value.messages.append(Message(id="u2", role="user", content="Now fix duplicate cursor handling."))
    process(engine, value)
    current = engine.store.checkpoint("project")["fields"]["current_task"]
    assert current == [{"text": "Now fix duplicate cursor handling.", "evidence": ["u2"]}]


def test_explicit_completion_removes_next_action(settings):
    engine = Engine(settings)
    value = session()
    process(engine, value)
    value.messages.append(Message(id="a", role="assistant", content="Completed: Add cursor validation."))
    process(engine, value)
    assert not engine.store.checkpoint("project")["fields"]["next_steps"]
    assert any(row["status"] == "completed" for row in engine.search("project", "cursor"))


def test_resolution_survives_refresh_and_preserves_history(settings):
    engine = Engine(settings)
    value = session(text="Decision: We chose SQLite.")
    process(engine, value)
    old = engine.search("project", "SQLite")[0]
    process(engine, session(sid="two", text="Decision: We chose PostgreSQL."))
    new = engine.search("project", "PostgreSQL")[0]
    engine.store.resolve("project", old["id"], "supersedes", new["id"])
    assert not engine.search("project", "SQLite")
    assert engine.store.evidence(old["id"], "project")
    process(engine, value, refresh=True)
    assert not engine.search("project", "SQLite")
    assert len(engine.store.search("project", "SQLite", history=True)) == 2
    with pytest.raises(ValueError):
        engine.store.resolve("elsewhere", new["id"], "disputed")


def test_manual_task_completion_affects_checkpoint_and_api(settings):
    engine = Engine(settings)
    process(engine, session())
    todo = engine.search("project", "cursor")[0]
    settings.api_key = "private"
    headers = {"Authorization": "Bearer private"}
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/checkpoint", params={"project": "project"}).status_code == 401
        result = client.post(
            "/resolve",
            json={"project": "project", "memory_id": todo["id"], "action": "completed"},
            headers=headers,
        )
        assert result.status_code == 200
        cp = client.get("/checkpoint", params={"project": "project"}, headers=headers).json()
        assert cp["fields"]["next_steps"] == []
        assert client.get("/history", params={"project": "project"}, headers=headers).status_code == 200


@pytest.mark.parametrize("target,flags", [("codex", []), ("opencode", ["--prompt"])])
def test_launch_has_fixed_prompt_project_and_no_shell(clean_project, monkeypatch, target, flags):
    calls = []
    monkeypatch.setattr("membridge.launch.shutil.which", lambda _: "C:/Program Files/tool.cmd")
    monkeypatch.setattr(
        "membridge.launch.subprocess.run",
        lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=7),
    )
    assert launch_tool(target, clean_project) == 7
    assert calls[0][0] == ["C:/Program Files/tool.cmd", *flags, PROMPT]
    assert calls[0][1] == {"cwd": clean_project.resolve(), "check": False}


def test_switch_launches_only_after_cited_handoff(clean_project, monkeypatch):
    class Reader:
        def __init__(self, project):
            pass

        def list_sessions(self):
            return [{"id": "live"}]

        def export(self, sid):
            return {
                "messages": [
                    {"role": "user", "content": "Current task: Add pagination.\nTODO: Test cursors."}
                ]
            }

    monkeypatch.setitem(READERS, "generic", Reader)

    def launch(target, project):
        assert "Where work stopped" in (project / "AGENTS.md").read_text()
        return 0

    monkeypatch.setattr("membridge.launch.launch_tool", launch)
    result = CliRunner().invoke(
        app, ["switch", "codex", "--from", "generic", "--launch", "--project", str(clean_project)]
    )
    assert result.exit_code == 0, result.output
    assert "unfinished tasks" in result.output


def test_watch_scans_all_project_sessions_and_cleans_lock(clean_project, monkeypatch):
    class Reader:
        def __init__(self, project):
            assert project == clean_project

        def list_sessions(self):
            return [{"id": "one"}, {"id": "two"}]

        def export(self, sid):
            return {"id": sid, "messages": [{"role": "user", "content": f"TODO: Finish task {sid}."}]}

    monkeypatch.setitem(READERS, "generic", Reader)
    state = watch(clean_project, "generic", once=True)
    assert state["processed"] == 2 and state["errors"] == 0
    assert not paths(clean_project, "generic")[0].exists()
    assert len(Client(clean_project).search("Finish", 20)) == 2


def test_watch_requires_model_consent_and_respects_existing_lock(clean_project, monkeypatch):
    monkeypatch.setenv("MEMBRIDGE_LLM_URL", "http://provider")
    monkeypatch.setenv("MEMBRIDGE_LLM_MODEL", "chat")
    with pytest.raises(ValueError, match="allow-model"):
        check_permission(Client(clean_project), False)
    check_permission(Client(clean_project), True)
    monkeypatch.delenv("MEMBRIDGE_LLM_URL")
    lock, _, _ = paths(clean_project, "opencode")
    lock.mkdir()
    with pytest.raises(ValueError, match="lock"):
        watch(clean_project, "opencode", once=True)


def test_background_watcher_starts_imports_and_stops(clean_project):
    (clean_project / ".aider.chat.history.md").write_text(
        "#### Goal: Test a background watcher\n\nTODO: Add a background sync test.\n", encoding="utf-8"
    )
    state = start(clean_project, "aider", interval=1, allow_model=False)
    assert state["state"] in {"running", "starting"}
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if status(clean_project, "aider").get("processed", 0):
                break
            time.sleep(0.1)
        assert status(clean_project, "aider")["processed"] >= 1
        assert Client(clean_project).search("background", 10)
    finally:
        stop(clean_project, "aider")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and paths(clean_project, "aider")[0].exists():
            time.sleep(0.1)
    assert status(clean_project, "aider")["state"] == "stopped"


def test_evaluation_separates_content_checks_from_agent_answers(tmp_path):
    output = tmp_path / "evaluation"
    report = evaluate(output)
    assert report["live_agent_evaluation"] == "not run"
    assert all(check["expected_phrases_present"] for case in report["cases"] for check in case["checks"])
    data = json.loads((output / "responses-template.json").read_text())
    score = score_responses(data)
    assert score["answered"] == 0 and score["rubric_passes"] == 0
    for run in data["runs"]:
        case = next(c for c in CASES if c["id"] == run["case_id"])
        run["answers"] = {q[0]: " ".join(q[3]) for q in case["questions"]}
    score = score_responses(data)
    assert score["answered"] == 27 and score["rubric_passes"] == 27
    data["runs"].append(data["runs"][0])
    with pytest.raises(ValueError, match="duplicate"):
        score_responses(data)


def test_real_mcp_stdio_is_project_bound_and_read_only(clean_project):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine = Engine(Settings.load())
    process(engine, session(project_key(clean_project), text="Decision: We chose PostgreSQL."))
    process(engine, session("other", sid="secret", text="Decision: We chose secret-other-project."))

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "membridge", "mcp", "--project", str(clean_project)],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {t.name for t in tools.tools} == {
                    "search_project_memory",
                    "get_current_checkpoint",
                    "get_memory_evidence",
                }
                hit = await client.call_tool("search_project_memory", {"query": "PostgreSQL"})
                assert not hit.isError and "PostgreSQL" in str(hit)
                hidden = await client.call_tool("search_project_memory", {"query": "secret-other-project"})
                assert "secret-other-project" not in str(hidden)
                invalid = await client.call_tool(
                    "search_project_memory", {"query": "PostgreSQL", "limit": 999}
                )
                assert invalid.isError

    asyncio.run(check())
