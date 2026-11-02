"""Opt-in real CLI evaluation. Uses only invented facts in isolated project folders."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from membridge.executables import command_for


def assistant_text(events):
    result = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "text" and event.get("part", {}).get("type") == "text":
            result.append(event["part"].get("text", ""))
    return "\n".join(result)


def run_live(output, timeout=90, opencode_model=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new evaluation directory")
    output.mkdir(parents=True)
    project = output / "project"
    project.mkdir()
    # A real repository boundary prevents ancestor AGENTS.md files from contaminating a controlled trial.
    subprocess.run(["git", "init", "--quiet", str(project)], capture_output=True, check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRIDGE_")}
    env["MEMBRIDGE_HOME"] = str(output / "memory")
    report = {
        "kind": "real-cli-handoff",
        "project": str(project),
        "stages": [],
        "round_trip": "not_completed",
    }
    report["opencode_model"] = opencode_model or "installed default"
    report["continuation"] = "saved-session resume after source CLI exit"

    def stage(label, args, prompt=None):
        start = time.monotonic()
        try:
            result = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=project,
                env=env,
                timeout=timeout,
                check=False,
            )
            (output / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
            (output / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            item = {
                "stage": label,
                "returncode": result.returncode,
                "seconds": time.monotonic() - start,
                "state": "passed" if result.returncode == 0 else "failed",
            }
            report["stages"].append(item)
            return result.stdout if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                for channel in ("stdout", "stderr"):
                    value = getattr(exc, channel, None) or ""
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    (output / f"{label}.{channel}.txt").write_text(value, encoding="utf-8")
            report["stages"].append(
                {
                    "stage": label,
                    "state": "unavailable",
                    "reason": type(exc).__name__,
                    "seconds": time.monotonic() - start,
                }
            )
            return None

    opencode = shutil.which("opencode.cmd") or shutil.which("opencode")
    codex = shutil.which("codex.cmd") or shutil.which("codex")
    if not opencode or not codex:
        report["reason"] = "OpenCode and Codex must both be on PATH"
    else:
        source_prompt = (
            "This is a synthetic MemBridge test. Do not use tools or create files. Acknowledge these exact records:\n"
            "Current task: Build the fictional lantern API.\nDecision: The project codeword is cobalt-maple-7391.\n"
            "TODO: Add the silver cursor test.\nNext step: Add the silver cursor test."
        )
        opencode_command, codex_command = command_for("opencode"), command_for("codex")
        model_flags = ["--model", opencode_model] if opencode_model else []
        source = stage(
            "01-opencode-source",
            [*opencode_command, "run", "--standalone", "--format", "json", *model_flags, source_prompt],
        )
        if source is not None:
            saved = stage("02-switch-to-codex", [sys.executable, "-m", "membridge", "switch", "codex"])
            if saved is not None:
                saved = stage(
                    "02b-resume-saved-opencode", [sys.executable, "-m", "membridge", "resume", "codex"]
                )
            if saved is not None:
                answer_file = output / "codex-answer.txt"
                prompt = (
                    "Read AGENTS.md in this folder only. Do not change files. State the fictional project's codeword "
                    "and pending test from the handoff. Then record these new synthetic facts in your response:\n"
                    "Decision: The return codeword is amber-cedar-4826.\nTODO: Test bronze pagination."
                )
                answer = stage(
                    "03-codex-receiver",
                    [
                        *codex_command,
                        "exec",
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "--json",
                        "--output-last-message",
                        str(answer_file),
                        "-",
                    ],
                    prompt,
                )
                if answer is not None and answer_file.exists():
                    text = answer_file.read_text(encoding="utf-8")
                    report["codex_recall"] = all(
                        p in text.casefold() for p in ("cobalt-maple-7391", "silver cursor")
                    )
                    saved = stage(
                        "04-switch-to-opencode", [sys.executable, "-m", "membridge", "switch", "opencode"]
                    )
                    if saved is not None:
                        listing = stage(
                            "04b-list-saved",
                            [sys.executable, "-m", "membridge", "resume", "opencode", "--list"],
                        )
                        selected = next(
                            (row for row in json.loads(listing or "[]") if row["source_tool"] == "codex"),
                            None,
                        )
                        saved = (
                            stage(
                                "04c-resume-saved-codex",
                                [
                                    sys.executable,
                                    "-m",
                                    "membridge",
                                    "resume",
                                    "opencode",
                                    "--session",
                                    selected["id"],
                                ],
                            )
                            if selected
                            else None
                        )
                    if saved is not None:
                        prompt = (
                            "Read AGENTS.md only. Do not modify files. What is the return codeword "
                            "and pending pagination test from the latest Codex handoff?"
                        )
                        back = stage(
                            "05-opencode-receiver",
                            [
                                *opencode_command,
                                "run",
                                "--standalone",
                                "--format",
                                "json",
                                *model_flags,
                                prompt,
                            ],
                        )
                        if back is not None:
                            report["opencode_recall"] = all(
                                p in assistant_text(back).casefold()
                                for p in ("amber-cedar-4826", "bronze pagination")
                            )
                            report["round_trip"] = (
                                "passed"
                                if report.get("codex_recall") and report["opencode_recall"]
                                else "failed"
                            )
        else:
            report["reason"] = (
                "OpenCode source could not run; inspect the isolated stage logs for authentication/provider errors."
            )
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
