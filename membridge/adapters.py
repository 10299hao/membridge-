"""Read-only local discovery; parsing also runs in the ingestion service."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path

from membridge.config import project_key
from membridge.discovery import AiderClient, ClaudeClient, GeminiClient
from membridge.executables import command_for
from membridge.formats import parse_aider, parse_claude, parse_gemini, parse_universal
from membridge.models import Message, Session
from membridge.privacy import redact
from membridge.tools import CONTEXT_FILES, EXPORT_TOOLS, tool_name


def timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    return value if isinstance(value, str) else None


def parse_opencode(data: dict, project: str) -> Session:
    info = data.get("info", data.get("session", {}))
    messages = []
    for i, item in enumerate(data.get("messages", [])):
        meta = item.get("info", item)
        role = meta.get("role", meta.get("type", ""))
        if role not in {"user", "assistant", "tool"}:
            continue  # Drop reasoning, idle markers, and synthetic system instructions.
        mid = str(meta.get("id", f"message-{i}"))
        parts = item.get("parts", item.get("content", []))
        content = []
        if isinstance(parts, str):
            content.append(parts)
        elif isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    content.append(part.get("text", ""))
                elif part.get("type") in {"tool", "tool-call"}:
                    state = part.get("state", {})
                    # Keep tool evidence explicitly attributed, never as an assistant claim.
                    content.append(
                        "Tool "
                        + str(part.get("tool", part.get("name", "unknown")))
                        + ": "
                        + json.dumps(state, ensure_ascii=False)
                    )
        if isinstance(item.get("text"), str):
            content.insert(0, item["text"])
        if content:
            messages.append(
                Message(
                    id=mid,
                    role=role,
                    content="\n".join(content),
                    ts=timestamp(meta.get("time", {}).get("created")),
                )
            )
    stable = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
    return Session(
        session_id=str(info.get("id", stable)),
        source_tool="opencode",
        project=project,
        title=info.get("title", "OpenCode session"),
        started_at=timestamp(info.get("time", {}).get("created")),
        messages=messages,
    )


def parse_generic(data: dict, project: str) -> Session:
    return parse_universal(data, project)


def parse_codex(data: dict, project: str) -> Session:
    records = data.get("records", [])
    meta = next((r.get("payload", {}) for r in records if r.get("type") == "session_meta"), {})
    messages = []
    # response_item is authoritative; event_msg duplicates are used only for older event-only logs.
    has_responses = any(
        r.get("type") == "response_item"
        and r.get("payload", {}).get("type") == "message"
        and r.get("payload", {}).get("role") in {"user", "assistant"}
        for r in records
    )
    for i, record in enumerate(records):
        payload = record.get("payload", {})
        role, content = None, ""
        kind = payload.get("type")
        if record.get("type") == "response_item":
            if kind == "message" and payload.get("role") in {"user", "assistant"}:
                if payload.get("channel") == "analysis":
                    continue
                role = payload["role"]
                parts = payload.get("content", [])
                content = (
                    parts
                    if isinstance(parts, str)
                    else "\n".join(
                        p.get("text", "")
                        for p in parts
                        if isinstance(p, dict) and p.get("type") in {"input_text", "output_text", "text"}
                    )
                )
            elif kind in {"function_call_output", "custom_tool_call_output"}:
                role = "tool"
                output = payload.get("output", "")
                content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        elif not has_responses and record.get("type") == "event_msg":
            if kind in {"user_message", "agent_message"}:
                role = "user" if kind == "user_message" else "assistant"
                content = payload.get("message", "")
        if role and content:
            if role == "user" and (
                re.match(r"^\s*#\s*AGENTS\.md instructions for\b", content)
                or re.fullmatch(r"\s*<environment_context>[\s\S]*</environment_context>\s*", content)
            ):
                continue  # Host-injected project instructions/environment are not conversation evidence.
            # Use record position: stable across append-only saves, unique even when upstream IDs repeat.
            messages.append(Message(id=f"record-{i}", role=role, content=content, ts=record.get("timestamp")))
    stable = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()[:24]
    return Session(
        session_id=str(meta.get("id", meta.get("session_id", stable))),
        source_tool="codex",
        project=project,
        title="Codex session",
        started_at=meta.get("timestamp"),
        messages=messages,
    )


def read_codex_file(path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Codex rollout exceeds 8 MiB; export a smaller transcript using Generic import")
    with path.open(encoding="utf-8-sig") as stream:
        return {"records": [json.loads(line) for line in stream if line.strip()]}


class CodexClient:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.root = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
        self.paths = {}

    def list_sessions(self):
        rows = []
        for path in self.root.rglob("*.jsonl"):
            try:
                # Read only the bounded metadata record, not other projects' transcript contents.
                with path.open(encoding="utf-8-sig") as stream:
                    first = stream.readline(1024 * 1024)
                record = json.loads(first)
                if record.get("type") != "session_meta":
                    continue
                meta = record.get("payload", {})
                if not meta.get("cwd") or project_key(meta["cwd"]) != project_key(self.directory):
                    continue
                sid = meta.get("id") or meta.get("session_id")
                if sid:
                    rows.append(
                        {
                            "id": sid,
                            "directory": meta["cwd"],
                            "updated": path.stat().st_mtime,
                            "created": meta.get("timestamp"),
                        }
                    )
                    self.paths[sid] = path
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda row: row["updated"], reverse=True)

    def export(self, session_id: str):
        self.list_sessions()
        if session_id not in self.paths:
            raise ValueError("Codex session does not match this project; use --project or --file explicitly")
        return read_codex_file(self.paths[session_id])


PARSERS = {
    "opencode": parse_opencode,
    "codex": parse_codex,
    "generic": parse_generic,
    "claude": parse_claude,
    "gemini": parse_gemini,
    "aider": parse_aider,
}


def _parse(data: dict, source: str, project: str, input_format=None) -> Session:
    source = tool_name(source)
    data = redact(data)
    if input_format and input_format != "auto":
        selected = tool_name(input_format)
        if selected not in PARSERS:
            raise ValueError("Unknown parser format; use generic or a registered native adapter")
        session = (
            parse_universal(data, project, source)
            if "text" in data and selected != "aider"
            else PARSERS[selected](data, project)
        )
        session = Session.model_validate(session)
        session.source_tool = source
        session.project = project
        session.metadata["input_format"] = selected
        return session
    # Text exports remain usable even for native tools whose raw log format differs.
    if "text" in data and source != "aider":
        return parse_universal(data, project, source)
    if source in PARSERS:
        session = Session.model_validate(PARSERS[source](data, project))
        session.source_tool, session.project = source, project
        return session
    return parse_universal(data, project, source)


def parse(data: dict, source: str, project: str, input_format=None) -> Session:
    try:
        return _parse(data, source, project, input_format)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            "Transcript structure does not match this parser; use a standard text export or --format generic"
        ) from exc


class OpenCodeClient:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.executable = shutil.which("opencode.cmd") or shutil.which("opencode")
        if not self.executable:
            raise ValueError("OpenCode is not on PATH. Use --file with an exported session instead.")
        self.command = command_for("opencode")

    def _run(self, args):
        result = subprocess.run(
            [*self.command, *args],
            cwd=self.directory,
            capture_output=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise ValueError("OpenCode command failed. Check `opencode session list` or use --file.")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "OpenCode returned non-JSON output; use an explicit exported JSON file."
            ) from exc

    def list_sessions(self):
        data = self._run(["session", "list", "--format", "json", "--max-count", "100"])
        rows = data if isinstance(data, list) else data.get("sessions", [])

        def directory(row):
            return row.get("directory") or row.get("location", {}).get("directory")

        return sorted(
            [r for r in rows if directory(r) and project_key(directory(r)) == project_key(self.directory)],
            key=lambda r: r.get("updated", r.get("time", {}).get("updated", 0)),
            reverse=True,
        )

    def export(self, session_id: str):
        # Detect command support without interpreting transcript content or running a shell.
        help_result = subprocess.run(
            [*self.command, "session", "--help"],
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        command = ["session", "export"] if "export" in help_result.stdout else ["export"]
        return self._run([*command, session_id])


READERS = {
    "opencode": OpenCodeClient,
    "codex": CodexClient,
    "claude": ClaudeClient,
    "gemini": GeminiClient,
    "aider": AiderClient,
}


def adapter_catalog():
    return {
        "sources": list(PARSERS) + EXPORT_TOOLS,
        "targets": list(CONTEXT_FILES) + ["stdout", "file"],
        "native_discovery": list(READERS),
        "export_only": EXPORT_TOOLS,
        "any_tool_via_export": True,
        "unknown_target": "stdout/paste; use --to file to export a file",
        "context_files": dict(CONTEXT_FILES),
        "plugin_errors": dict(PLUGIN_ERRORS),
    }


def register_adapter(name, parser, reader=None, context_file=None):
    name = tool_name(name)
    if name in PARSERS or name in {"auto", "stdout", "file"}:
        raise ValueError("Adapter name is already registered or reserved")
    if not callable(parser):
        raise ValueError("Adapter parser must be callable")
    if reader is not None and (
        not callable(reader) or not all(hasattr(reader, method) for method in ("list_sessions", "export"))
    ):
        raise ValueError("Adapter reader must implement list_sessions and export")
    if context_file is not None and (
        not re.fullmatch(r"[A-Za-z0-9_.-]+\.md", context_file) or context_file.startswith(".")
    ):
        raise ValueError("Context filename must be a project-local Markdown basename")
    PARSERS[name] = parser
    if reader:
        READERS[name] = reader
    if context_file:
        CONTEXT_FILES[name] = context_file


PLUGIN_ERRORS = {}


def load_adapter_plugins():
    for entry in entry_points(group="membridge.adapters"):
        try:
            factory = entry.load()
            descriptor = factory()
            register_adapter(entry.name, **descriptor)
        except Exception as exc:
            # Installed plugin failures should not break built-in readers or echo private paths.
            PLUGIN_ERRORS[entry.name] = type(exc).__name__


load_adapter_plugins()
