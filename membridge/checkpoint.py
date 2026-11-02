"""Cited task checkpoints. These are reported transcript claims, not code verification."""

import re

from membridge.privacy import redact_text

FIELDS = {
    "current_task": "Current task",
    "next_steps": "Next actions",
    "blockers": "Reported blockers",
    "verification": "Latest reported verification",
    "completed": "Reported progress",
    "files": "Reported relevant files",
}
PATTERNS = {
    "current_task": r"^(?:current task|task|goal|objective)\s*:",
    "completed": r"^(?:completed|done|finished)\s*:|^\[x\]",
    "blockers": r"^(?:blocker|blocked|current blocker|problem)\s*:",
    "verification": r"^(?:tests?|verification|validation|test results?)\s*:|\b\d+ (?:tests? )?(?:passed|failed)\b",
    "next_steps": r"^(?:todo|next step|next action|remaining|unfinished)\s*:|^\[ \]",
    "files": r"^(?:updated|modified|created|changed|fixed|added|implemented)\s+.*[./\\]",
}


def build_checkpoint(session, drafts):
    fields = {key: [] for key in FIELDS}
    task_position = -1
    latest_request = None
    for position, message in enumerate(session.messages):
        if message.role == "tool":
            continue
        if message.role == "user" and len(message.content.strip()) >= 12:
            # Prefer a newer substantive request over a goal from the start of a long session.
            lines = [line.strip() for line in message.content.splitlines() if line.strip()]
            if any(
                not any(
                    re.search(pattern, line, re.I)
                    for key, pattern in PATTERNS.items()
                    if key != "current_task"
                )
                for line in lines
            ):
                latest_request = (position, message)
        for line in message.content.splitlines():
            line = line.strip().lstrip("-*").strip()
            for key, pattern in PATTERNS.items():
                if re.search(pattern, line, re.I):
                    item = {"text": redact_text(line[:800]), "evidence": [message.id]}
                    fields[key].append(item)
                    if key == "current_task":
                        task_position = position
    if latest_request and latest_request[0] > task_position:
        latest = latest_request[1]
        fields["current_task"] = [{"text": redact_text(latest.content[:600]), "evidence": [latest.id]}]
    if not fields["current_task"]:
        latest = next((m for m in reversed(session.messages) if m.role == "user"), None)
        if latest:
            fields["current_task"] = [{"text": redact_text(latest.content[:600]), "evidence": [latest.id]}]
    # Include extracted task/file memories if the transcript did not use explicit labels.
    for kind, field in (("todo", "next_steps"), ("file_change", "files")):
        if not fields[field]:
            fields[field] = [
                {"text": d.text, "evidence": d.evidence}
                for d in drafts
                if d.type == kind and d.status == "active"
            ]
    for key in fields:
        unique = {item["text"].casefold(): item for item in fields[key]}
        fields[key] = list(unique.values())[-(1 if key in {"current_task", "verification"} else 3) :]
    return {
        "source_tool": session.source_tool,
        "source_session": session.session_id,
        "mode": "extractive-checkpoint",
        "fields": fields,
    }


def task_key(value):
    """Only reconcile explicitly labelled tasks with the same remaining text."""
    value = re.sub(
        r"^\s*(?:todo|completed|done|finished|next step)\s*:\s*|^\s*\[[ xX]\]\s*", "", value, flags=re.I
    )
    value = " ".join(value.casefold().split()).rstrip(".! ")
    value = re.sub(r"^(?:i|we)\s+(?:have\s+)?", "", value)
    value = re.sub(
        r"^(?:add|added|implement|implemented|create|created|write|written|completed|finished)\s+(?:the\s+|a\s+|an\s+)?",
        "",
        value,
    )
    return value
