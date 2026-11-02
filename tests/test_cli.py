import json

import pytest
from typer.testing import CliRunner

from membridge.cli import READERS, app
from membridge.formats import load_transcript
from tests.conftest import ROOT

runner = CliRunner()


def test_local_cli_complete_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    for name in ("MEMBRIDGE_DATABASE_URL", "MEMBRIDGE_SERVER", "MEMBRIDGE_LLM_URL", "MEMBRIDGE_AI_URL"):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    args = ["--project", str(project)]
    result = runner.invoke(app, ["init", *args])
    assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["adapters", "list"]).exit_code == 0
    result = runner.invoke(
        app,
        ["save", "--from", "opencode", "--file", str(ROOT / "data/samples/opencode/v2-session.json"), *args],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output)["state"] == "complete"
    result = runner.invoke(app, ["search", "Postgres", *args])
    assert result.exit_code == 0 and "Postgres" in result.output
    result = runner.invoke(app, ["handoff", "--to", "codex", "--preview", *args])
    assert result.exit_code == 0 and "pagination" in result.output
    assert not (project / "AGENTS.md").exists()
    (project / "AGENTS.md").write_text("Keep my instructions.\n")
    result = runner.invoke(app, ["handoff", "--to", "codex", *args])
    assert result.exit_code == 0, result.output
    assert (project / "AGENTS.md").read_text().startswith("Keep my instructions.")
    result = runner.invoke(app, ["jobs", "list", *args])
    assert result.exit_code == 0 and '"state": "complete"' in result.output


def test_invalid_source_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["save", "--from", "../../claude"])
    assert result.exit_code == 1
    assert "Tool name must use" in result.output


@pytest.fixture
def switch_project(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    for name in (
        "MEMBRIDGE_DATABASE_URL", "MEMBRIDGE_SERVER", "MEMBRIDGE_LLM_URL",
        "MEMBRIDGE_AI_URL", "MEMBRIDGE_EMBEDDING_MODEL", "MEMBRIDGE_INGESTION_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Keep existing instructions.\n")
    return project


@pytest.mark.parametrize(
    ("source", "target", "fixture"),
    [
        ("opencode", "codex", "opencode/v2-session.json"),
        ("codex", "opencode", "codex/session.jsonl"),
    ],
)
def test_switch_native_session_to_handoff(switch_project, monkeypatch, source, target, fixture):
    class Reader:
        def __init__(self, project):
            assert project == switch_project

        def list_sessions(self):
            return [{"id": "selected-session"}]

        def export(self, session_id):
            assert session_id == "selected-session"
            return load_transcript(ROOT / "data/samples" / fixture)

    monkeypatch.setitem(READERS, source, Reader)
    result = runner.invoke(app, ["switch", target, "--project", str(switch_project)])
    assert result.exit_code == 0, result.output
    instructions = (switch_project / "AGENTS.md").read_text()
    assert instructions.startswith("Keep existing instructions.")
    assert "MEMBRIDGE:START" in instructions and f"{source}:" in instructions
    assert f"Run `{target}`" in result.output


def test_switch_without_source_keeps_handoff(switch_project, monkeypatch):
    class Reader:
        def __init__(self, project):
            pass

        def list_sessions(self):
            return []

    monkeypatch.setitem(READERS, "opencode", Reader)
    result = runner.invoke(app, ["switch", "codex", "--project", str(switch_project)])
    assert result.exit_code == 1
    assert "No opencode sessions match" in result.output
    assert (switch_project / "AGENTS.md").read_text() == "Keep existing instructions.\n"


def test_switch_failed_extraction_keeps_handoff(switch_project, monkeypatch):
    from membridge.intelligence import Intelligence

    class Reader:
        def __init__(self, project):
            pass

        def list_sessions(self):
            return [{"id": "session"}]

        def export(self, session_id):
            return load_transcript(ROOT / "data/samples/opencode/v2-session.json")

    def fail(self, session):
        raise ValueError("Unavailable provider")

    monkeypatch.setitem(READERS, "opencode", Reader)
    monkeypatch.setattr(Intelligence, "extract", fail)
    result = runner.invoke(app, ["switch", "codex", "--project", str(switch_project)])
    assert result.exit_code == 1
    assert "Processing failed" in result.output
    assert (switch_project / "AGENTS.md").read_text() == "Keep existing instructions.\n"
