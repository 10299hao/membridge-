"""Restart/isolation benchmark with separate receiving-agent answer scoring."""

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from membridge.config import Settings, project_key
from membridge.engine import Engine
from membridge.models import Message, Session
from membridge.repository import snapshot

CASES = (
    ("closed-source", ["cobalt lantern", "silver cursor"], ["foreign-project-secret"]),
    ("same-tool-resume", ["cobalt lantern", "silver cursor"], ["foreign-project-secret"]),
    (
        "selected-older-task",
        ["cobalt lantern", "silver cursor"],
        ["newer unrelated task", "foreign-project-secret"],
    ),
    ("changed-decision", ["PostgreSQL", "silver cursor"], ["SQLite", "foreign-project-secret"]),
    ("completed-task", ["completed", "silver cursor", "gold cursor"], ["foreign-project-secret"]),
    (
        "corrected-memory",
        ["corrected lantern", "silver cursor"],
        ["cobalt lantern", "foreign-project-secret"],
    ),
    ("long-conversation", ["final lantern task", "silver cursor"], ["foreign-project-secret"]),
    ("changed-file", ["changed_file", "silver cursor"], ["foreign-project-secret"]),
)


def rubric(text, required, forbidden):
    normalized = " ".join(text.casefold().split())
    missing = [p for p in required if p.casefold() not in normalized]
    stale = [p for p in forbidden if p.casefold() in normalized]
    return {"missing": missing, "forbidden_present": stale, "passed": not missing and not stale}


def score_answers(data):
    known = {name: (required, forbidden) for name, required, forbidden in CASES}
    seen, results = set(), []
    for row in data["runs"]:
        name = row["case_id"]
        if name not in known or name in seen:
            raise ValueError("Unknown or duplicate case")
        seen.add(name)
        answer = row["answer"]
        if answer is not None and not isinstance(answer, str):
            raise ValueError("Answer must be text or null")
        for field in ("elapsed_seconds", "input_tokens", "output_tokens", "cost_usd"):
            value = row.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("Usage and latency must be finite nonnegative numbers or null")
        results.append(
            {
                "case_id": name,
                "answered": answer is not None,
                "rubric": rubric(answer, *known[name]) if answer is not None else None,
                **{
                    key: row.get(key)
                    for key in ("elapsed_seconds", "input_tokens", "output_tokens", "cost_usd")
                },
            }
        )
    if seen != set(known):
        raise ValueError("Include every case; use null for uncollected answers")
    answered = [r for r in results if r["answered"]]
    return {
        "mode": "receiving-agent-answers",
        "cases": results,
        "answered": len(answered),
        "literal_rubric_passes": sum(r["rubric"]["passed"] for r in answered),
        "missing_content_answers": sum(bool(r["rubric"]["missing"]) for r in answered),
        "stale_or_foreign_content_answers": sum(bool(r["rubric"]["forbidden_present"]) for r in answered),
        "measured_cost_usd": sum(r["cost_usd"] for r in results)
        if all(r["cost_usd"] is not None for r in results)
        else None,
        "note": "Literal scoring requires human review for paraphrases and negation. Null usage is unknown, not zero.",
    }


def evaluate_continuity(output, responses=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new evaluation directory")
    # Validate supplied evidence before creating any output.
    if responses:
        report = score_answers(json.loads(Path(responses).read_text(encoding="utf-8")))
        output.mkdir(parents=True)
        (output / "answer-scores.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    output.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRIDGE_")}
    env["MEMBRIDGE_HOME"] = str(output / "memory")
    settings = Settings(database_url=f"sqlite:///{output / 'memory.db'}")
    env["MEMBRIDGE_DATABASE_URL"] = settings.database_url
    results, template = [], []
    for name, required, forbidden in CASES:
        project = output / name
        project.mkdir()
        engine = Engine(settings)

        def save(sid, text, directory=project, source="opencode"):
            value = Session(
                session_id=sid,
                source_tool=source,
                project=project_key(directory),
                title=f"Synthetic {name}",
                messages=[Message(id="m1", role="user", content=text)],
            )
            value.metadata["repository"] = snapshot(directory, value.messages)
            job = engine.store.enqueue(value)
            engine.process_one(job["id"])
            if engine.store.job(job["id"], value.project)["state"] != "complete":
                raise ValueError("Synthetic extraction failed")
            return value, job

        text = "Current task: Build the lantern API.\nDecision: The label is cobalt lantern.\nTODO: Add silver cursor."
        if name == "changed-decision":
            save("old", "Decision: We chose SQLite.")
            text = "Current task: Update storage.\nDecision: We now use PostgreSQL.\nTODO: Add silver cursor."
        if name == "completed-task":
            text += "\nCompleted: Add silver cursor.\nTODO: Add gold cursor."
        if name == "long-conversation":
            text = (
                "\n".join([f"Discussion line {i}: considering interface colors." for i in range(200)])
                + "\nCurrent task: Build the final lantern task.\nTODO: Add silver cursor."
            )
        if name == "changed-file":
            (project / "src").mkdir()
            (project / "src/app.py").write_text("before", encoding="utf-8")
            text += "\nUpdated src/app.py for the lantern."
        value, job = save(
            "selected",
            text,
            source="codex"
            if name in {"same-tool-resume", "changed-decision", "long-conversation"}
            else "opencode",
        )
        selected = next(r for r in engine.store.resumable(value.project) if r["source_session"] == "selected")
        if name == "selected-older-task":
            save("newer", "Current task: Build a newer unrelated task.\nTODO: Add unrelated tests.")
        if name == "corrected-memory":
            row = next(r for r in engine.search(value.project, "") if r["type"] == "decision")
            engine.store.correct(value.project, row["id"], "Decision: The label is corrected lantern.")
        if name == "changed-file":
            (project / "src/app.py").write_text("after", encoding="utf-8")
        foreign = output / f"{name}-foreign"
        foreign.mkdir()
        save("foreign", "Decision: The codeword is foreign-project-secret.", foreign)
        engine.store.engine.dispose()
        # The receiver is a fresh process, with no source logs or native tool discovery.
        started = time.perf_counter()
        target = "opencode" if name in {"changed-decision", "long-conversation"} else "codex"
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "membridge",
                "resume",
                target,
                "--project",
                str(project),
                "--session",
                selected["id"],
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        elapsed = time.perf_counter() - started
        (project / "resume.stdout.txt").write_text(child.stdout, encoding="utf-8")
        (project / "resume.stderr.txt").write_text(child.stderr, encoding="utf-8")
        handoff = (
            (project / "AGENTS.md").read_text(encoding="utf-8") if (project / "AGENTS.md").exists() else ""
        )
        checks = rubric(handoff, required, forbidden)
        if name == "completed-task":
            # Completed work may appear as history, but must not be an active next action.
            checks["completed_task_not_active"] = (
                "[active] TODO: Add silver cursor" not in handoff
                and "**Next actions:** TODO: Add silver cursor" not in handoff
            )
            checks["passed"] &= checks["completed_task_not_active"]
        results.append(
            {
                "case_id": name,
                "returncode": child.returncode,
                "source": value.source_tool,
                "target": target,
                "checks": checks,
                "passed": child.returncode == 0 and checks["passed"],
                "resume_seconds": elapsed,
                "handoff_chars": len(handoff),
                "model_calls": 0,
                "provider_cost_usd": 0,
                "input_tokens": None,
                "output_tokens": None,
            }
        )
        (output / f"{name}-receiver.md").write_text(
            "Use only this saved handoff. Summarize the current task, current decisions, completed and pending tasks, and repository warnings. Say unknown for unsupported claims.\n\n"
            + handoff,
            encoding="utf-8",
        )
        template.append(
            {
                "case_id": name,
                "answer": None,
                "elapsed_seconds": None,
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
            }
        )
    latencies = sorted(r["resume_seconds"] for r in results)
    report = {
        "mode": "restart-continuity-content",
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "cases": results,
        "metrics": {
            "missing_content_cases": sum(bool(r["checks"]["missing"]) for r in results),
            "stale_or_foreign_content_cases": sum(bool(r["checks"]["forbidden_present"]) for r in results),
            "resume_mean_seconds": sum(latencies) / len(latencies),
            "resume_p95_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1],
            "provider_cost_usd": 0,
            "task_continuity_pass_rate": sum(r["passed"] for r in results) / len(results),
        },
        "receiving_agent_accuracy": "not measured",
        "note": "Fresh-process saved-memory checks, not agent accuracy. Baseline extraction only; no model calls. Source hosts need not run. Each case also tests project isolation.",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "responses-template.json").write_text(
        json.dumps({"runs": template}, indent=2), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# Continuation evaluation\n\nEach case reopens storage in a fresh process. Use a fresh isolated agent session for each *-receiver.md prompt, with no access to sibling files or answer keys. Record verbatim answers and measured usage in responses-template.json; leave unavailable data null. Score with `membridge evaluate-continuity --responses PATH --output NEW_DIRECTORY`. Review phrase scores manually. Never substitute a generated handoff for an actual agent answer.\n",
        encoding="utf-8",
    )
    return report
