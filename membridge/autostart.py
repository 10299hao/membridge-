"""Optional current-user Windows login registration; never edits other startup entries."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def configure(project, enabled):
    if os.name != "nt":
        raise ValueError(
            "Login startup currently supports Windows; use sync resume with your OS supervisor elsewhere"
        )
    import winreg

    name = "MemBridge-" + hashlib.sha256(str(project.resolve()).casefold().encode()).hexdigest()[:16]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    args = [
        str(pythonw if pythonw.exists() else python),
        "-m",
        "membridge",
        "sync",
        "resume",
        "--project",
        str(project.resolve()),
    ]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    return {"enabled": enabled, "entry": name, "scope": "current user, this project only"}
