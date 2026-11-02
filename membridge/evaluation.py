"""A small reproducible benchmark; content checks never masquerade as agent QA."""

import json
import tempfile
from pathlib import Path

from membridge.config import Settings
from membridge.engine import Engine
from membridge.models import Message, Session

CONDITIONS = ("no_handoff", "manual_handoff", "membridge_handoff")
CASES = [
    {
        "id": "task-api",
        "messages": [
            (
                "user",
                "Current task: Add pagination to the task API.\nDecision: We chose PostgreSQL.\nPreference: Use pytest.",
            ),
            (
                "assistant",
                "Updated src/tasks.py with cursor validation.\nTests: 8 passed; 1 failed.\nBlocker: Duplicate task IDs fail validation.\nNext step: Fix duplicate task ID handling.",
            ),
        ],
        "manual": "Use PostgreSQL and pytest. Cursor validation is in src/tasks.py. Tests: 8 passed; 1 failed. Fix duplicate task ID handling next.",
        "questions": [
            ("database", "decision", "Which database was chosen?", ["PostgreSQL"], ["SQLite"]),
            (
                "tests",
                "verification",
                "What were the last reported test results?",
                ["8 passed", "1 failed"],
                ["all tests passed"],
            ),
            ("next", "task", "What should happen next?", ["duplicate task ID"], ["deploy to production"]),
        ],
    },
    {
        "id": "cache-fix",
        "messages": [
            (
                "user",
                "Current task: Repair cache invalidation.\nDecision: Keep Redis.\nConstraint: Preserve the cache key format.",
            ),
            (
                "assistant",
                "Completed: Add cache hit logging.\nUpdated src/cache.py with logging.\nBlocker: Stale entries remain after writes.\nNext step: Invalidate entries after writes.",
            ),
        ],
        "manual": "Keep Redis and the cache key format. Cache-hit logging in src/cache.py is complete. Stale entries remain after writes; invalidate entries after writes next.",
        "questions": [
            ("backend", "decision", "Which cache backend should remain?", ["Redis"], ["Memcached"]),
            (
                "constraint",
                "constraint",
                "What must remain unchanged?",
                ["cache key format"],
                ["database schema"],
            ),
            (
                "next",
                "task",
                "What is the next step?",
                ["after writes", "invalidat"],
                ["add cache hit logging"],
            ),
        ],
    },
    {
        "id": "upload-validation",
        "messages": [
            (
                "user",
                "Current task: Validate uploads.\nDecision: Limit uploads to 5 MiB.\nConstraint: Preserve the /upload response fields.",
            ),
            (
                "assistant",
                "Updated src/uploads.py with size checks.\nTests: 6 passed.\nBlocker: Empty files are still accepted.\nNext step: Reject empty files.",
            ),
        ],
        "manual": "Limit uploads to 5 MiB and preserve /upload response fields. Size checks are in src/uploads.py; 6 tests passed. Reject empty files next.",
        "questions": [
            ("size", "decision", "What upload-size limit was chosen?", ["5 MiB"], ["10 MiB"]),
            ("tests", "verification", "How many tests were reported passing?", ["6"], ["8"]),
            (
                "next",
                "task",
                "What unfinished validation should be added?",
                ["empty files"],
                ["virus scanning"],
            ),
        ],
    },
]


def contains(text, phrase):
    return phrase.casefold() in " ".join(text.casefold().split())


def score_responses(data):
    known = {(case["id"], condition): case for case in CASES for condition in CONDITIONS}
    rows, seen = [], set()
    for entry in data["runs"]:
        key = (entry["case_id"], entry["condition"])
        if key not in known or key in seen:
            raise ValueError("Unknown or duplicate evaluation run")
        seen.add(key)
        case = known[key]
        answers = entry["answers"]
        expected = {q[0] for q in case["questions"]}
        if set(answers) != expected:
            raise ValueError("Response question IDs must match the case")
        elapsed = entry.get("elapsed_seconds")
        if elapsed is not None and (not isinstance(elapsed, (int, float)) or elapsed < 0):
            raise ValueError("Elapsed seconds must be nonnegative or null")
        for qid, category, _, required, forbidden in case["questions"]:
            answer = answers[qid]
            if answer is not None and not isinstance(answer, str):
                raise ValueError("Answers must be text or null")
            rows.append(
                {
                    "case_id": key[0],
                    "condition": key[1],
                    "question_id": qid,
                    "category": category,
                    "answered": answer is not None,
                    "rubric_pass": None
                    if answer is None
                    else (
                        all(contains(answer, phrase) for phrase in required)
                        and not any(contains(answer, phrase) for phrase in forbidden)
                    ),
                    "known_wrong_claim": None
                    if answer is None
                    else any(contains(answer, p) for p in forbidden),
                    "elapsed_seconds": elapsed,
                }
            )
    if seen != set(known):
        raise ValueError("Include all cases and conditions; leave uncollected answers null")
    return {
        "mode": "supplied-agent-answers",
        "scoring": "Literal phrase rubric; human review required. Not a general hallucination detector.",
        "answered": sum(r["answered"] for r in rows),
        "rubric_passes": sum(r["rubric_pass"] is True for r in rows),
        "results": rows,
    }


def evaluate(output: Path, responses=None):
    if output.exists():
        raise ValueError("Choose a new output directory so existing evaluation evidence is preserved")
    report = score_responses(json.loads(responses.read_text(encoding="utf-8"))) if responses else None
    output.mkdir(parents=True)
    if report:
        (output / "answer-scores.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    results, runs = [], []
    with tempfile.TemporaryDirectory(prefix="membridge-evaluation-") as temporary:
        # Explicit Settings: never inherit a user's model credentials or remote server.
        engine = Engine(Settings(database_url=f"sqlite:///{Path(temporary) / 'evaluation.db'}"))
        try:
            for case in CASES:
                session = Session(
                    session_id=case["id"],
                    source_tool="generic",
                    project=case["id"],
                    messages=[
                        Message(id=f"m{i}", role=role, content=text)
                        for i, (role, text) in enumerate(case["messages"])
                    ],
                )
                job = engine.store.enqueue(session)
                engine.process_one(job["id"])
                result = engine.handoff(case["id"], source_job=job["id"])
                handoff = result["brief"]
                transcript = "\n".join(m.content for m in session.messages)
                checks = [
                    {"question_id": q[0], "expected_phrases_present": all(contains(handoff, p) for p in q[3])}
                    for q in case["questions"]
                ]
                results.append(
                    {
                        "case_id": case["id"],
                        "transcript_chars": len(transcript),
                        "handoff_chars": len(handoff),
                        "character_ratio": len(handoff) / len(transcript),
                        "checks": checks,
                    }
                )
                for condition in CONDITIONS:
                    context = {
                        "no_handoff": "No previous context is available.",
                        "manual_handoff": case["manual"],
                        "membridge_handoff": handoff,
                    }[condition]
                    prompt = (
                        "Answer only from the supplied context; say unknown when it does not support an answer.\n\n"
                        + context
                        + "\n\nQuestions:\n"
                        + "\n".join(f"{q[0]}: {q[2]}" for q in case["questions"])
                    )
                    (output / f"{case['id']}-{condition}.md").write_text(prompt, encoding="utf-8")
                    runs.append(
                        {
                            "case_id": case["id"],
                            "condition": condition,
                            "answers": {q[0]: None for q in case["questions"]},
                            "elapsed_seconds": None,
                        }
                    )
        finally:
            engine.store.engine.dispose()
    report = {
        "mode": "synthetic-content-check",
        "live_agent_evaluation": "not run",
        "note": "Phrase presence in generated handoffs is not receiving-agent accuracy. Ratios count characters, not tokens; short cited handoffs can be larger than transcripts.",
        "cases": results,
    }
    (output / "content-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "responses-template.json").write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
    (output / "HOW_TO_EVALUATE.md").write_text(
        "# Controlled receiving-agent evaluation\n\nUse fresh, separate sessions and clean project copies for every case/condition. "
        "Give the receiving agent only the matching .md prompt; do not expose answer keys, other conditions or previous sessions. "
        "Record its verbatim answers and measured elapsed_seconds in responses-template.json. Leave missing answers null. "
        "Then run membridge evaluate --responses <filled-template.json> --output <new-results-folder>. "
        "Inspect the literal phrase scores manually: paraphrases may be marked wrong and negated phrases may be marked right. "
        "No model was contacted by the content benchmark.\n",
        encoding="utf-8",
    )
    return report
