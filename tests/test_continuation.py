import json

import pytest
from typer.testing import CliRunner

from membridge.cli import app
from membridge.config import Settings, project_key
from membridge.continuation import candidates, prepare
from membridge.continuity_evaluation import CASES, evaluate_continuity, score_answers
from membridge.engine import Engine
from membridge.models import Message, Session

runner = CliRunner()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("MEMBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("MEMBRIDGE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine(Settings.load())

    def save(sid, text, directory=project):
        value = Session(
            session_id=sid,
            source_tool="opencode",
            project=project_key(directory),
            messages=[Message(id="m1", role="user", content=text)],
        )
        job = engine.store.enqueue(value)
        engine.process_one(job["id"])
        return next(r for r in engine.store.resumable(value.project) if r["source_session"] == sid)

    yield project, engine, save
    engine.store.engine.dispose()


def test_resume_selected_session_without_source_discovery_and_across_projects(setup, monkeypatch):
    project, engine, save = setup
    selected = save("first", "Current task: Repair lantern.\nTODO: Add silver test.")
    save("newer", "Current task: Unrelated work.\nTODO: Add bronze test.")
    foreign = project.parent / "foreign"
    foreign.mkdir()
    other = save("other", "Decision: foreign secret.", foreign)
    engine.store.engine.dispose()
    monkeypatch.setattr("membridge.cli.READERS", {})
    result = runner.invoke(app, ["resume", "codex", "--session", selected["id"], "--project", str(project)])
    assert result.exit_code == 0, result.output
    brief = (project / "AGENTS.md").read_text()
    assert "silver test" in brief and "bronze test" not in brief and "foreign secret" not in brief
    with pytest.raises(ValueError, match="unavailable"):
        prepare(project, "codex", other["id"])
    with pytest.raises(ValueError, match="Select a saved session"):
        prepare(project, "codex", None)


def test_failed_or_pending_import_not_offered_and_forgotten_not_resumable(setup):
    project, engine, save = setup
    selected = save("one", "TODO: Add silver test.")
    pending = Session(
        session_id="pending",
        source_tool="codex",
        project=project_key(project),
        messages=[Message(id="p", role="user", content="TODO: pending work.")],
    )
    engine.store.enqueue(pending)
    assert len(candidates(project)) == 1
    engine.store.forget(project_key(project), selected["id"], apply=True)
    assert candidates(project) == []
    with pytest.raises(ValueError, match="unavailable"):
        prepare(project, "codex", selected["id"])


def test_menu_selects_project_session_and_launches_once(setup, monkeypatch):
    project, _, save = setup
    save("one", "Current task: Repair lantern.\nTODO: Add silver test.")
    calls = []
    monkeypatch.setattr(
        "membridge.launch.launch_tool", lambda target, path: calls.append((target, path)) or 0
    )
    result = runner.invoke(app, ["--project", str(project)], input="1\n1\n1\n")
    assert result.exit_code == 0, (result.output, result.exception)
    assert "Repair lantern" in result.output and "Unsaved messages" in result.output
    assert calls == [("codex", project)]
    result = runner.invoke(app, ["--project", str(project)], input="3\n")
    assert result.exit_code == 0
    assert len(calls) == 1


def test_resume_list_does_not_write_handoff_and_allows_same_tool(setup):
    project, _, save = setup
    save("one", "TODO: Add silver test.")
    result = runner.invoke(app, ["resume", "opencode", "--list", "--project", str(project)])
    assert result.exit_code == 0 and len(json.loads(result.output)) == 1
    assert not (project / "AGENTS.md").exists()
    assert prepare(project, "opencode", None)["target"] == "opencode"


def test_answer_scores_distinguish_missing_stale_and_unknown_usage():
    runs = [{"case_id": name, "answer": None} for name, _, _ in CASES]
    runs[0]["answer"] = "foreign-project-secret"
    report = score_answers({"runs": runs})
    assert report["answered"] == 1 and report["literal_rubric_passes"] == 0
    assert report["cases"][0]["rubric"]["missing"]
    assert report["cases"][0]["rubric"]["forbidden_present"]
    assert report["cases"][0]["cost_usd"] is None
    runs[0]["cost_usd"] = float("nan")
    with pytest.raises(ValueError):
        score_answers({"runs": runs})
    with pytest.raises(ValueError):
        score_answers({"runs": []})


def test_restart_benchmark_and_artifact_safety(tmp_path):
    output = tmp_path / "trial"
    report = evaluate_continuity(output)
    assert report["passed"] == report["total"] == 8, report
    assert report["receiving_agent_accuracy"] == "not measured"
    assert all(row["resume_seconds"] > 0 for row in report["cases"])
    assert (output / "responses-template.json").exists()
    with pytest.raises(ValueError):
        evaluate_continuity(output)
