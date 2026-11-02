"""Launch a fresh interactive coding tool without constructing a shell command."""

import base64
import os
import shutil
import subprocess

from membridge.executables import command_for

PROMPT = (
    "Read the MemBridge handoff in AGENTS.md. Summarize the current task, reported progress and next step."
)


def launch_tool(target, project):
    if target not in {"codex", "opencode"}:
        raise ValueError("Launching is supported for Codex and OpenCode")
    executable = shutil.which(target)
    if not executable:
        raise ValueError(f"{target} is not on PATH. Memory is ready; start it manually in this project.")
    # OpenCode's initial prompt is a flag; Codex accepts a positional initial prompt.
    command = command_for(target)
    args = [*command, "--prompt", PROMPT] if target == "opencode" else [*command, PROMPT]
    return subprocess.run(args, cwd=project.resolve(), check=False).returncode


def open_tool_window(target, project):
    if target not in {"codex", "opencode"}:
        raise ValueError("Unsupported destination")
    executable = shutil.which(target + ".cmd") or shutil.which(target)
    if not executable:
        raise ValueError(f"{target} is not on PATH; the handoff is ready to open manually")
    if os.name != "nt":
        raise ValueError("Opening a separate terminal currently supports Windows; use --launch in a terminal")

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    invocation = "& " + " ".join(quote(arg) for arg in command_for(target)) + " "
    invocation += ("--prompt " if target == "opencode" else "") + quote(PROMPT)
    script = f"Set-Location -LiteralPath {quote(project.resolve())}\n{invocation}"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    child = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NoExit", "-EncodedCommand", encoded],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return {"pid": child.pid, "opened": target}
