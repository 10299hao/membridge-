import json

import pytest

from membridge.adapters import OpenCodeClient, parse
from membridge.config import project_key
from membridge.privacy import redact
from tests.conftest import ROOT


def test_v2_and_legacy_preserve_evidence_but_not_reasoning(export):
    session = parse(export, "opencode", "project-a")
    assert session.source_tool == "opencode"
    assert [m.id for m in session.messages] == ["msg_goal", "msg_decision", "msg_todo"]
    assert "reasoning" not in session.model_dump_json()
    legacy = json.loads((ROOT / "data/samples/opencode/legacy-session.json").read_text())
    parsed = parse(legacy, "opencode", "project-a")
    assert parsed.messages[1].id == "msg_legacy_assistant"
    assert "Tool edit" in parsed.messages[1].content


def test_unrecognized_format_is_rejected():
    with pytest.raises(ValueError):
        parse({"messages": [{"type": "idle"}]}, "opencode", "a")


def test_redaction_catches_values_and_nested_credentials():
    value = {
        "api_key": "secret-value",
        "nested": [
            "password='do-not-leak'",
            "Bearer abc.def.ghi",
            "postgres://user:sensitive@localhost/db",
            "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        ],
    }
    cleaned = json.dumps(redact(value))
    assert all(
        secret not in cleaned
        for secret in ["secret-value", "do-not-leak", "abc.def.ghi", "sensitive", "sk-abc"]
    )


def test_discovery_filters_parent_directory(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _: "opencode")
    adapter = OpenCodeClient(tmp_path)
    monkeypatch.setattr(
        adapter,
        "_run",
        lambda _: [
            {"id": "outside", "directory": str(tmp_path.parent), "updated": 99},
            {"id": "old", "directory": str(tmp_path), "updated": 1},
            {"id": "new", "location": {"directory": str(tmp_path)}, "time": {"updated": 2}},
        ],
    )
    assert [s["id"] for s in adapter.list_sessions()] == ["new", "old"]
    assert project_key(tmp_path / ".") == project_key(tmp_path)
