"""Bounded local repository observations. Never execute commands found in a transcript."""

import hashlib
import re
import subprocess
from pathlib import Path

FILE_PATTERN = re.compile(r"(?<![\w:/\\])((?:[\w.-]+[/\\])+[\w.-]+\.[A-Za-z0-9]{1,12})")


def git_value(project, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def file_state(project, name):
    root = Path(project).resolve()
    candidate = root / name
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or candidate.is_symlink():
            return {"state": "excluded"}
        if not resolved.is_file():
            return {"state": "missing"}
        if resolved.stat().st_size > 2_000_000:
            return {"state": "too_large"}
        return {"state": "present", "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}
    except OSError:
        return {"state": "unavailable"}


def snapshot(project, messages):
    project = Path(project).resolve()
    if not project.is_dir():
        return {}
    names = []
    for message in messages:
        text = message.content if hasattr(message, "content") else message["content"]
        for name in FILE_PATTERN.findall(text):
            name = name.replace("\\", "/")
            if name not in names and not any(part.startswith(".") for part in name.split("/")):
                names.append(name)
    # No observation timestamp: repeated saves of identical conversation data must remain idempotent.
    return {
        "branch": git_value(project, "symbolic-ref", "--short", "HEAD"),
        "commit": git_value(project, "rev-parse", "--verify", "HEAD"),
        "files": {name: file_state(project, name) for name in names[:30]},
    }


def verify(project, rows, contexts):
    root = Path(project)
    if not root.is_dir():
        return {"available": False, "reason": "project_directory_unavailable", "memories": []}
    if not any(contexts.get(row["id"], {}).get("repository") for row in rows):
        return {
            "available": True,
            "note": "No saved repository observations are available.",
            "memories": [{"memory_id": row["id"], "state": "not_observed", "issues": []} for row in rows],
        }
    branch = git_value(root, "symbolic-ref", "--short", "HEAD")
    commit = git_value(root, "rev-parse", "--verify", "HEAD")
    checked = []
    for row in rows:
        baseline = contexts.get(row["id"], {}).get("repository", {})
        issues = []
        if baseline.get("branch") and branch and branch != baseline["branch"]:
            issues.append({"code": "branch_changed", "saved": baseline["branch"], "current": branch})
        if baseline.get("commit") and commit and commit != baseline["commit"]:
            issues.append({"code": "commit_changed", "saved": baseline["commit"], "current": commit})
        relevant = set(contexts.get(row["id"], {}).get("files", FILE_PATTERN.findall(row["text"])))
        for name in relevant:
            name = name.replace("\\", "/")
            if name not in baseline.get("files", {}):
                continue
            before, now = baseline["files"][name], file_state(root, name)
            if now["state"] == "missing":
                issues.append({"code": "missing_file", "file": name})
            elif before.get("sha256") and now.get("sha256") and before["sha256"] != now["sha256"]:
                issues.append({"code": "changed_file", "file": name})
            elif now["state"] in {"excluded", "unavailable", "too_large"}:
                issues.append({"code": "file_unverified", "file": name})
        checked.append(
            {
                "memory_id": row["id"],
                "state": "needs_review"
                if issues
                else "observations_unchanged"
                if baseline
                else "not_observed",
                "issues": issues,
            }
        )
    return {
        "available": True,
        "note": "File/branch observations do not prove a claim or test result correct.",
        "memories": checked,
    }
