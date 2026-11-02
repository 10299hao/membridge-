"""Portable text-only transcript formats. No hidden reasoning or image payloads are imported."""

import copy
import hashlib
import json
import re
from pathlib import Path

from membridge.models import Message, Session

MAX_BYTES = 8 * 1024 * 1024


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if (
            value.get("thought")
            or value.get("type") in {"thinking", "reasoning", "analysis"}
            or value.get("content_type") in {"thoughts", "reasoning", "analysis"}
        ):
            return ""
        return text_content(value.get("parts", value.get("text", "")))
    if isinstance(value, list):
        return "\n".join(
            text_content(p)
            for p in value
            if isinstance(p, str)
            or (
                isinstance(p, dict)
                and not p.get("thought")
                and p.get("type", "text") in {"text", "input_text", "output_text"}
            )
        )
    return ""


def make_session(data, project, source, rows):
    unique = {}
    for i, row in enumerate(rows):
        if row.get("channel") == "analysis":
            continue
        role = row.get("role", row.get("type"))
        role = {"model": "assistant", "gemini": "assistant", "human": "user", "ai": "assistant"}.get(
            role, role
        )
        if role not in {"user", "assistant", "tool"}:
            continue
        content = text_content(row.get("content", row.get("parts", row.get("text", ""))))
        if content.strip():
            mid = str(row.get("id") or row.get("uuid") or f"message-{i}")
            unique[mid] = Message(
                id=mid,
                role=role,
                content=content,
                ts=row.get("ts", row.get("timestamp"))
                if isinstance(row.get("ts", row.get("timestamp")), str)
                else None,
            )
    stable = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
    return Session(
        session_id=str(data.get("session_id") or data.get("sessionId") or data.get("id") or stable),
        source_tool=source,
        project=project,
        title=data.get("title", "Imported conversation"),
        messages=list(unique.values()),
    )


def markdown_rows(text):
    rows, role, lines, fence = [], "user", [], False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        inline = (
            None
            if fence
            else re.fullmatch(
                r"\s*(?:#{1,6}\s*|\*\*)?(user|human|assistant|model|gemini|ai)(?:\*\*)?\s*:\s*(.*)",
                line,
                re.I,
            )
        )
        match = inline or (
            None
            if fence
            else re.fullmatch(
                r"\s*(?:#{1,6}\s*|\*\*)?(user|human|assistant|model|gemini|ai)(?:\*\*)?\s*:?\s*", line, re.I
            )
        )
        if match:
            if "\n".join(lines).strip():
                rows.append({"role": role, "content": "\n".join(lines)})
            role, lines = match.group(1).lower(), [inline.group(2)] if inline else []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        rows.append({"role": role, "content": "\n".join(lines)})
    return rows


def parse_universal(data, project, source="generic"):
    if "mapping" in data:
        # ChatGPT-style tree export: only the selected branch, never mutually exclusive siblings.
        nodes = data["mapping"]
        current = data.get("current_node")
        if not current:
            raise ValueError("Tree export needs current_node; export one selected conversation branch")
        chain, seen = [], set()
        while current:
            if current in seen or current not in nodes:
                raise ValueError("Invalid conversation branch")
            seen.add(current)
            node = nodes[current]
            message = node.get("message")
            if message and message.get("channel") != "analysis":
                chain.append(
                    {
                        "id": message.get("id", current),
                        "role": message.get("author", {}).get("role"),
                        "content": message.get("content", {}),
                    }
                )
            current = node.get("parent")
        rows = list(reversed(chain))
    elif "text" in data and not any(k in data for k in ("messages", "contents", "records")):
        rows = markdown_rows(data["text"])
    else:
        rows = data.get("messages", data.get("contents", data.get("records")))
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Export needs a messages/contents array with role and content, or Markdown/text")
        # Fail visibly for proprietary nested logs instead of importing empty/wrong fields.
        if any("payload" in r for r in rows):
            raise ValueError(
                "Nested log records need a native adapter; use --from codex or a plain transcript"
            )
    return make_session(data, project, source, rows)


def parse_claude(data, project):
    if "records" not in data:
        return parse_universal(data, project, "claude")
    records = [r for r in data["records"] if not r.get("isSidechain")]
    conversational = [r for r in records if r.get("type") in {"user", "assistant"} and "message" in r]
    if conversational and any("parentUuid" in r for r in conversational):
        nodes = {r["uuid"]: r for r in records if r.get("uuid")}
        current, chain, seen = conversational[-1], [], set()
        while current:
            uid = current.get("uuid")
            if uid in seen:
                raise ValueError("Claude transcript contains a parent cycle")
            seen.add(uid)
            chain.append(current)
            parent = current.get("parentUuid")
            if parent and parent not in nodes:
                raise ValueError("Claude branch references missing history; use a plain /export transcript")
            current = nodes.get(parent)
        conversational = [
            r for r in reversed(chain) if r.get("type") in {"user", "assistant"} and "message" in r
        ]
    rows = []
    for i, record in enumerate(conversational):
        message = record["message"]
        mid = str(record.get("uuid", f"message-{i}"))
        rows.append(dict(message, id=mid, timestamp=record.get("timestamp")))
        if isinstance(message.get("content"), list):
            for j, block in enumerate(message["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    rows.append(
                        {"id": f"{mid}-tool-{j}", "role": "tool", "content": block.get("content", "")}
                    )
    sid = next((r["sessionId"] for r in conversational if r.get("sessionId")), None)
    return make_session(dict(data, session_id=sid), project, "claude", rows)


def gemini_document(data):
    if "records" not in data:
        return data
    meta, messages = {}, {}
    for record in copy.deepcopy(data["records"]):
        if "$rewindTo" in record:
            keys = list(messages)
            cut = keys.index(record["$rewindTo"]) if record["$rewindTo"] in messages else 0
            messages = {k: messages[k] for k in keys[:cut]}
        elif "$patch" in record:
            patch = record["$patch"]
            for change in ([patch] if "id" in patch else []) + patch.get("updates", []):
                if change.get("id") in messages:
                    if "content" in change:
                        messages[change["id"]]["content"] = change["content"]
                    for tool in change.get("toolCalls", []):
                        for existing in messages[change["id"]].get("toolCalls", []):
                            if existing.get("id") == tool.get("id") and "result" in tool:
                                existing["result"] = tool["result"]
            for mid in patch.get("removeIds", []):
                messages.pop(mid, None)
            if "orderIds" in patch:
                messages = {mid: messages[mid] for mid in patch["orderIds"] if mid in messages}
        elif "$set" in record:
            update = record["$set"]
            if "messages" in update:
                messages = {m["id"]: m for m in update["messages"]}
            meta.update({k: v for k, v in update.items() if k != "messages"})
        elif "sessionId" in record and "projectHash" in record:
            meta.update(record)
        elif record.get("id") and record.get("type") in {"user", "gemini", "info", "error", "warning"}:
            messages[record["id"]] = record
    return dict(meta, messages=list(messages.values()))


def parse_gemini(data, project):
    data = gemini_document(data)
    if "text" in data:
        return parse_universal(data, project, "gemini")
    rows = []
    for i, message in enumerate(data.get("messages", data.get("contents", []))):
        role = message.get("type", message.get("role"))
        if role not in {"user", "gemini", "assistant", "model", "tool"}:
            continue
        rows.append(dict(message, role=role))
        for j, tool in enumerate(message.get("toolCalls", [])):
            result = text_content(tool.get("result"))
            if result:
                rows.append({"id": f"{message.get('id', i)}-tool-{j}", "role": "tool", "content": result})
    return make_session(data, project, "gemini", rows)


def parse_aider(data, project):
    if "text" not in data:
        return parse_universal(data, project, "aider")
    # Aider's #### heading is the user prompt; unquoted body text is the assistant reply.
    rows, lines, fence = [], [], False
    for line in data["text"].splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if not fence and line.startswith("#### "):
            if "\n".join(lines).strip():
                rows.append({"role": "assistant", "content": "\n".join(lines)})
            lines = []
            rows.append({"role": "user", "content": line[5:]})
        elif not fence and (line.startswith("> ") or line.startswith("# aider chat started")):
            continue
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        rows.append({"role": "assistant", "content": "\n".join(lines)})
    return make_session(data, project, "aider", rows)


def load_transcript(path: Path) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Transcript exceeds 8 MiB; split or export a smaller conversation")
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".md", ".txt"}:
        return {"session_id": str(path.resolve()), "title": path.name, "text": raw}
    if path.suffix.lower() == ".jsonl":
        return {"records": [json.loads(line) for line in raw.splitlines() if line.strip()]}
    data = json.loads(raw)
    if isinstance(data, list):
        if data and any(isinstance(r, dict) and "mapping" in r for r in data):
            if len(data) != 1:
                raise ValueError("Export contains multiple conversations; provide one conversation object")
            return data[0]
        return {"messages": data}
    if not isinstance(data, dict):
        raise ValueError("Transcript JSON must be a conversation object or message array")
    return data
