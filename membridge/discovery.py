"""Local discovery for Claude, Gemini and Aider; never scan cloud accounts."""

import hashlib
import json
import os
from pathlib import Path

from membridge.config import project_key
from membridge.formats import load_transcript


class FileReader:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.paths = {}

    def remember(self, path, sid):
        if sid in self.paths and self.paths[sid] != path:
            raise ValueError("Duplicate session IDs in local history; select an explicit file instead")
        self.paths[sid] = path
        return {"id": sid, "directory": str(self.directory), "updated": path.stat().st_mtime}

    def export(self, session_id):
        self.paths.clear()
        self.list_sessions()
        if session_id not in self.paths:
            raise ValueError("Session does not match this project; select an explicit --file")
        return load_transcript(self.paths[session_id])


class ClaudeClient(FileReader):
    def list_sessions(self):
        self.paths.clear()
        root = Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"
        rows = []
        for path in root.glob("*/*.jsonl"):
            try:
                meta = None
                with path.open(encoding="utf-8-sig") as stream:
                    for _ in range(64):
                        line = stream.readline(1024 * 1024)
                        if not line:
                            break
                        record = json.loads(line)
                        if record.get("cwd"):
                            meta = record
                            break
                if (
                    not meta
                    or meta.get("isSidechain")
                    or project_key(meta["cwd"]) != project_key(self.directory)
                ):
                    continue
                rows.append(self.remember(path, meta.get("sessionId", path.stem)))
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda r: r["updated"], reverse=True)


class GeminiClient(FileReader):
    def list_sessions(self):
        self.paths.clear()
        root = Path(os.getenv("MEMBRIDGE_GEMINI_HOME", str(Path.home() / ".gemini")))
        temp = root / "tmp"
        identifiers = set()
        for spelling in {
            str(self.directory),
            project_key(self.directory),
            str(self.directory).replace("\\", "/"),
        }:
            identifiers.add(hashlib.sha256(spelling.encode()).hexdigest())
        registry = root / "projects.json"
        if registry.exists():
            try:
                for path, identifier in json.loads(registry.read_text()).get("projects", {}).items():
                    if project_key(path) == project_key(self.directory):
                        identifiers.add(identifier)
            except (OSError, ValueError):
                pass
        if not temp.exists():
            return []
        rows = []
        for directory in temp.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            marker = directory / ".project_root"
            try:
                # A marker takes precedence over inferred hashes, including on moved projects.
                root_text = marker.read_text().strip() if marker.exists() else None
                matched = (
                    bool(root_text) and project_key(root_text) == project_key(self.directory)
                    if marker.exists()
                    else directory.name in identifiers
                )
                if not matched:
                    continue
                for path in (directory / "chats").glob("session-*.*"):
                    if path.suffix not in {".json", ".jsonl"} or path.is_symlink():
                        continue
                    # Path stem is a stable local selector; parsing preserves the actual sessionId.
                    rows.append(self.remember(path, directory.name + ":" + path.stem))
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda r: r["updated"], reverse=True)


class AiderClient(FileReader):
    def list_sessions(self):
        self.paths.clear()
        path = self.directory / ".aider.chat.history.md"
        if not path.is_file() or path.is_symlink():
            return []
        return [self.remember(path, "chat-history")]
