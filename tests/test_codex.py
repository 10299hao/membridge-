import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from membridge.adapters import CodexClient, parse, read_codex_file
from membridge.api import create_app
from membridge.cli import app
from membridge.engine import Engine
from tests.conftest import ROOT

SAMPLE = ROOT / "data/samples/codex/session.jsonl"


def test_codex_parses_messages_without_internal_instructions_reasoning_or_duplicates():
    session = parse(read_codex_file(SAMPLE), "codex", "a")
    assert session.source_tool == "codex" and session.session_id == "codex-demo"
    assert len(session.messages) == 2
    assert session.messages[1].id == "record-3"
    assert "cursor pagination" in session.messages[1].content
    assert "must not be imported" not in session.model_dump_json()


def test_codex_event_only_format():
    session = parse(
        {
            "records": [
                {"type": "session_meta", "payload": {"id": "old"}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "Goal: build an API"}},
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "TODO: add tests"}},
            ]
        },
        "codex",
        "a",
    )
    assert len(session.messages) == 2


def test_codex_discovery_exact_project_and_export(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    logs = home / "sessions" / "2026" / "10" / "23"
    logs.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    records = read_codex_file(SAMPLE)["records"]
    records[0]["payload"]["cwd"] = str(tmp_path)
    (logs / "rollout-good.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    records[0]["payload"]["id"] = "outside"
    records[0]["payload"]["cwd"] = str(tmp_path.parent)
    (logs / "rollout-outside.jsonl").write_text(json.dumps(records[0]), encoding="utf-8")
    reader = CodexClient(tmp_path)
    assert [r["id"] for r in reader.list_sessions()] == ["codex-demo"]
    assert reader.export("codex-demo")["records"][0]["payload"]["id"] == "codex-demo"


def test_codex_to_opencode_cli_flow(tmp_path, monkeypatch):
    for name in ("MEMBRIDGE_DATABASE_URL", "MEMBRIDGE_SERVER", "MEMBRIDGE_LLM_URL", "MEMBRIDGE_AI_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Keep these project instructions.\n")
    runner = CliRunner()
    args = ["--project", str(project)]
    result = runner.invoke(app, ["save", "--from", "codex", "--file", str(SAMPLE), *args])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["handoff", "--to", "opencode", *args])
    assert result.exit_code == 0, result.output
    content = (project / "AGENTS.md").read_text()
    assert content.startswith("Keep these project instructions.")
    assert "cursor pagination" in content and "Source (extractive): codex:codex-demo" in content
    assert "Start a new opencode session" in result.output


def test_codex_import_through_gateway(settings):
    with TestClient(create_app(settings=settings)) as client:
        result = client.post(
            "/imports", json={"source_tool": "codex", "project": "a", "data": read_codex_file(SAMPLE)}
        )
        assert result.status_code == 202
        engine = Engine(settings)
        engine.process_one(result.json()["id"])
        result = client.post("/handoff", json={"project": "a"})
        assert result.status_code == 200
        assert "codex:codex-demo" in result.json()["brief"]
