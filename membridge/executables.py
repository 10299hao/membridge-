"""Resolve known npm launchers to direct executables so Windows cmd cannot split prompts."""

import os
import shutil
from pathlib import Path


def command_for(tool):
    executable = shutil.which(tool + ".cmd") or shutil.which(tool)
    if not executable:
        raise ValueError(f"{tool} is not on PATH")
    if os.name == "nt" and Path(executable).suffix.casefold() == ".cmd":
        base = Path(executable).parent
        if tool == "opencode":
            native = base / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe"
            if native.is_file():
                return [str(native)]
        if tool == "codex":
            script = base / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            node = shutil.which("node")
            if script.is_file() and node:
                return [node, str(script)]
    return [executable]
