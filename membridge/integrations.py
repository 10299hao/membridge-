"""Install only project-scoped, owned command assets; preserve user changes on uninstall."""

import hashlib
import json
import sys
from pathlib import Path


def assets(project):
    executable = str(Path(sys.executable).resolve())
    script = project / ".membridge" / "host_switch.py"

    def ps_quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    command = f"& {ps_quote(executable)} {ps_quote(script)}"
    helper = """import sys
from pathlib import Path
from membridge.cli import app
source, target = sys.argv[1:]
if source not in {"codex", "opencode"} or target not in {"codex", "opencode"} or source == target:
    raise SystemExit("Choose the other supported tool: codex or opencode")
app(["switch", target, "--from", source, "--project", str(Path(__file__).resolve().parents[1]), "--open-window"])
"""
    opencode = (
        "---\ndescription: Save this conversation and open the other coding tool with its handoff\n---\n\n"
        "Switch this project's context to $ARGUMENTS. Accept exactly `codex`; reject other arguments. "
        "Use the shell tool to run this fixed command in PowerShell:\n\n```powershell\n"
        + command
        + " opencode codex\n```\n\nReport success only if it succeeds. "
        "The new terminal opens separately; do not terminate this session automatically.\n"
    )
    skill = (
        "---\nname: membridge-switch\ndescription: Save this coding conversation and open OpenCode with the project handoff.\n---\n\n"
        "When asked to switch to OpenCode, run the following fixed PowerShell command. "
        "Accept only `opencode` as the destination; do not interpolate other supplied arguments.\n\n```powershell\n"
        + command
        + " codex opencode\n```\n\nReport the command result. It opens a separate terminal; "
        "do not end this Codex process automatically. Ordinary host tool permissions still apply.\n"
    )
    return {
        ".membridge/host_switch.py": helper,
        ".opencode/commands/switch.md": opencode,
        ".agents/skills/membridge-switch/SKILL.md": skill,
    }


def install(project):
    project = Path(project).resolve()
    desired = assets(project)
    manifest = project / ".membridge" / "integrations.json"
    old = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    for name, content in desired.items():
        target = project / name
        if any(parent.is_symlink() for parent in [target, *target.parents] if parent != project.parent):
            raise ValueError("Refusing a symlinked integration path")
        if target.exists() and target.read_text(encoding="utf-8") != content:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if old.get(name) != digest:
                raise ValueError(f"Existing or user-modified integration was preserved: {name}")
    records = {}
    for name, content in desired.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        records[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return {"installed": list(records), "opencode": "/switch codex", "codex": "$membridge-switch opencode"}


def uninstall(project):
    project = Path(project).resolve()
    manifest = project / ".membridge" / "integrations.json"
    if not manifest.exists():
        return {"removed": [], "preserved": []}
    records = json.loads(manifest.read_text(encoding="utf-8"))
    removed, preserved = [], []
    for name, digest in records.items():
        target = project / name
        if name not in assets(project) or not target.resolve().is_relative_to(project) or target.is_symlink():
            preserved.append(name)
        elif target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            target.unlink()
            removed.append(name)
        elif target.exists():
            preserved.append(name)
    if not preserved:
        manifest.unlink()
    return {"removed": removed, "preserved": preserved}
