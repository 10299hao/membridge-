import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from membridge.adapters import PARSERS, READERS, adapter_catalog, parse, register_adapter
from membridge.api import create_app
from membridge.cli import app
from membridge.discovery import AiderClient, ClaudeClient, GeminiClient
from membridge.engine import Engine
from membridge.formats import load_transcript
from membridge.handoff import inject
from membridge.tools import CONTEXT_FILES
from tests.conftest import ROOT

SAMPLES = ROOT / "data/samples"


@pytest.mark.parametrize(
    ("source", "fixture", "query"),
    [
        ("claude", "claude/session.jsonl", "notifications"),
        ("gemini", "gemini/session.json", "index"),
        ("gemini", "gemini/session.jsonl", "composite"),
        ("aider", "aider/history.md", "validation"),
        ("deepseek", "universal/deepseek.json", "cache"),
        ("grok", "universal/grok.md", "idempotency"),
    ],
)
def test_source_imports_through_api_with_correct_provenance(settings, source, fixture, query):
    data = load_transcript(SAMPLES / fixture)
    with TestClient(create_app(settings=settings)) as client:
        result = client.post("/imports", json={"source_tool": source, "project": "a", "data": data})
        assert result.status_code == 202, result.text
        engine = Engine(settings)
        assert engine.process_one(result.json()["id"])
        rows = client.post("/search", json={"project": "a", "query": query}).json()["memories"]
        assert rows and all(row["source_tool"] == source for row in rows)
        evidence = client.get(f"/memories/{rows[0]['id']}/evidence", params={"project": "a"}).json()
        assert evidence["messages"] and "Private reasoning" not in json.dumps(evidence)


def test_claude_selected_branch_excludes_abandoned_decisions_and_sidechains():
    records = [
        {
            "type": "user",
            "uuid": "u",
            "parentUuid": None,
            "sessionId": "s",
            "message": {"role": "user", "content": "Goal: database"},
        },
        {
            "type": "assistant",
            "uuid": "old",
            "parentUuid": "u",
            "message": {"role": "assistant", "content": "We chose MongoDB."},
        },
        {
            "type": "assistant",
            "uuid": "new",
            "parentUuid": "u",
            "message": {"role": "assistant", "content": "We chose Postgres."},
        },
        {
            "type": "assistant",
            "uuid": "sub",
            "parentUuid": "old",
            "isSidechain": True,
            "message": {"role": "assistant", "content": "We chose Redis."},
        },
    ]
    session = parse({"records": records}, "claude-code", "a")
    assert session.source_tool == "claude" and session.session_id == "s"
    assert "Postgres" in session.model_dump_json()
    assert "MongoDB" not in session.model_dump_json() and "Redis" not in session.model_dump_json()


def test_gemini_patches_rewinds_and_order_are_replayed_without_mutating_input():
    data = {
        "records": [
            {"sessionId": "g", "projectHash": "hash"},
            {"id": "u", "type": "user", "content": "Goal: task API"},
            {"id": "a", "type": "gemini", "content": "old decision"},
            {"id": "b", "type": "gemini", "content": "discarded"},
            {"$rewindTo": "b"},
            {"$patch": {"updates": [{"id": "a", "content": "We chose Postgres."}], "orderIds": ["u", "a"]}},
        ]
    }
    original = json.dumps(data)
    session = parse(data, "gemini", "a")
    assert [m.id for m in session.messages] == ["u", "a"]
    assert session.messages[-1].content == "We chose Postgres."
    assert json.dumps(data) == original


def test_universal_chatgpt_branch_and_common_provider_formats():
    mapping = {
        "root": {"parent": None, "message": None},
        "old": {
            "parent": "root",
            "message": {"author": {"role": "assistant"}, "content": {"parts": ["old branch"]}},
        },
        "new": {
            "parent": "root",
            "message": {"author": {"role": "assistant"}, "content": {"parts": ["We chose Postgres."]}},
        },
    }
    session = parse({"mapping": mapping, "current_node": "new"}, "chatgpt", "a")
    assert "old branch" not in session.model_dump_json()
    assert session.messages[0].content == "We chose Postgres."
    data = {
        "contents": [
            {
                "role": "model",
                "parts": [{"text": "secret thought", "thought": True}, {"text": "TODO: test cache."}],
            }
        ]
    }
    assert parse(data, "new-provider", "a").messages[0].content == "TODO: test cache."


def test_format_override_preserves_model_label_and_redacts_tokens():
    data = load_transcript(SAMPLES / "opencode/v2-session.json")
    data["messages"][0]["text"] += " api_key=super-secret-value"
    session = parse(data, "deepseek", "a", "opencode")
    assert session.source_tool == "deepseek" and session.metadata["input_format"] == "opencode"
    assert "super-secret-value" not in session.model_dump_json()


def test_claude_discovery_uses_exact_cwd_and_ignores_subagents(tmp_path, monkeypatch):
    root = tmp_path / "claude"
    logs = root / "projects" / "project-slug"
    logs.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    records = load_transcript(SAMPLES / "claude/session.jsonl")["records"]
    records[0]["cwd"] = str(tmp_path)
    path = logs / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    reader = ClaudeClient(tmp_path)
    assert reader.list_sessions()[0]["id"] == "claude-demo"
    assert reader.export("claude-demo")["records"][0]["sessionId"] == "claude-demo"
    assert ClaudeClient(tmp_path / "other").list_sessions() == []


def test_gemini_discovery_marker_overrides_inferred_identity(tmp_path, monkeypatch):
    root = tmp_path / "gemini"
    logs = root / "tmp" / "project-short-id" / "chats"
    logs.mkdir(parents=True)
    (logs.parent / ".project_root").write_text(str(tmp_path))
    (logs / "session-one.json").write_bytes((SAMPLES / "gemini/session.json").read_bytes())
    monkeypatch.setenv("MEMBRIDGE_GEMINI_HOME", str(root))
    reader = GeminiClient(tmp_path)
    rows = reader.list_sessions()
    assert len(rows) == 1
    assert reader.export(rows[0]["id"])["sessionId"] == "gemini-demo"
    assert GeminiClient(tmp_path / "other").list_sessions() == []


def test_aider_discovery_is_project_local(tmp_path):
    path = tmp_path / ".aider.chat.history.md"
    path.write_bytes((SAMPLES / "aider/history.md").read_bytes())
    reader = AiderClient(tmp_path)
    assert reader.list_sessions()[0]["id"] == "chat-history"
    assert "TODO" in reader.export("chat-history")["text"]


@pytest.mark.parametrize(
    ("target", "filename"),
    [
        ("claude", "CLAUDE.md"),
        ("gemini", "GEMINI.md"),
        ("aider", "MEMBRIDGE_HANDOFF.md"),
        ("file", "MEMBRIDGE_HANDOFF.md"),
    ],
)
def test_native_target_files_preserve_existing_instructions(tmp_path, monkeypatch, target, filename):
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    for key in ("MEMBRIDGE_SERVER", "MEMBRIDGE_DATABASE_URL", "MEMBRIDGE_LLM_URL", "MEMBRIDGE_AI_URL"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    original = b"# My instructions\r\nKeep these unchanged.\r\n"
    (project / filename).write_bytes(original)
    runner = CliRunner()
    args = ["--project", str(project)]
    saved = runner.invoke(
        app, ["save", "--from", "deepseek", "--file", str(SAMPLES / "universal/deepseek.json"), *args]
    )
    assert saved.exit_code == 0, saved.output
    result = runner.invoke(app, ["handoff", "--to", target, *args])
    assert result.exit_code == 0, result.output
    assert (project / filename).read_bytes().startswith(original)
    assert b"deepseek:deepseek-demo" in (project / filename).read_bytes()
    assert list((project / ".membridge/backups").glob("*.md"))
    assert not (project / "AGENTS.md").exists()


def test_unknown_provider_cli_is_export_only_and_handoff_is_paste_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    for key in ("MEMBRIDGE_SERVER", "MEMBRIDGE_DATABASE_URL", "MEMBRIDGE_LLM_URL", "MEMBRIDGE_AI_URL"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    runner = CliRunner()
    args = ["--project", str(project)]
    result = runner.invoke(app, ["save", "--from", "new-tool", *args])
    assert result.exit_code == 1 and "--file" in result.output
    result = runner.invoke(
        app, ["save", "--from", "new-tool", "--file", str(SAMPLES / "universal/grok.md"), *args]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["handoff", "--to", "grok", *args])
    assert result.exit_code == 0 and "Paste this brief" in result.output
    assert "new-tool:" in result.output
    assert not any(project.glob("*.md"))


@pytest.mark.parametrize("filename", ["../CLAUDE.md", "nested/GEMINI.md", "..\\CLAUDE.md", ".hidden.md"])
def test_handoff_filenames_cannot_escape_project(tmp_path, filename):
    with pytest.raises(ValueError):
        inject(tmp_path, "brief", filename)


def test_adapter_plugin_registration_and_validation(monkeypatch):
    monkeypatch.setattr("membridge.adapters.PARSERS", dict(PARSERS))
    monkeypatch.setattr("membridge.adapters.READERS", dict(READERS))
    # Mutate shared mapping reversibly: tools and CLI hold the same context map.
    monkeypatch.setitem(CONTEXT_FILES, "test-plugin", "TEST.md")

    def parser(data, project):
        return parse(data, "generic", project)

    register_adapter("test-plugin", parser, context_file="TEST.md")
    assert "test-plugin" in adapter_catalog()["sources"]
    assert (
        parse(
            {"messages": [{"role": "user", "content": "Goal: test plugin"}]}, "test-plugin", "a"
        ).source_tool
        == "test-plugin"
    )
    with pytest.raises(ValueError):
        register_adapter("codex", parser)
    with pytest.raises(ValueError):
        register_adapter("bad-plugin", parser, context_file="../escape.md")


def test_markdown_roles_inside_code_fences_are_not_message_boundaries():
    data = {
        "text": "User: Goal: test a parser\nAssistant: We chose JSON.\n```text\nUser: example code only\n```\nTODO: add tests."
    }
    session = parse(data, "grok", "a")
    assert len(session.messages) == 2
    assert session.messages[1].role == "assistant"
    assert "User: example code only" in session.messages[1].content


def test_invalid_native_structures_return_safe_api_errors(settings):
    with TestClient(create_app(settings=settings)) as client:
        for source in ("claude", "gemini", "new-tool"):
            result = client.post(
                "/imports",
                json={"source_tool": source, "project": "a", "data": {"messages": ["secret-value"]}},
            )
            assert result.status_code == 400
            assert "secret-value" not in result.text


def test_universal_exports_exclude_reasoning_and_system_messages():
    data = {
        "messages": [
            {"role": "system", "content": "internal system instructions"},
            {"role": "assistant", "channel": "analysis", "content": "secret analysis"},
            {"role": "assistant", "content": {"content_type": "thoughts", "parts": ["secret thoughts"]}},
            {"role": "assistant", "content": "We chose Postgres."},
        ]
    }
    session = parse(data, "deepseek", "a")
    assert len(session.messages) == 1 and session.messages[0].content == "We chose Postgres."


def test_entrypoint_plugins_load_and_report_failures(monkeypatch):
    from types import SimpleNamespace

    import membridge.adapters as adapters

    monkeypatch.setattr(adapters, "PARSERS", dict(adapters.PARSERS))
    monkeypatch.setattr(adapters, "PLUGIN_ERRORS", {})
    good = SimpleNamespace(
        name="loaded-plugin",
        load=lambda: lambda: {"parser": lambda data, project: parse(data, "generic", project)},
    )
    bad = SimpleNamespace(name="broken-plugin", load=lambda: lambda: {"parser": "not callable"})
    monkeypatch.setattr(adapters, "entry_points", lambda **kwargs: [good, bad])
    adapters.load_adapter_plugins()
    assert "loaded-plugin" in adapters.PARSERS
    assert adapters.adapter_catalog()["plugin_errors"]["broken-plugin"] == "ValueError"
