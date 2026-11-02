"""Exercise separate ingestion, AI and gateway processes over real localhost HTTP."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    processes = []
    with tempfile.TemporaryDirectory(prefix="membridge-http-") as temporary:
        directory = Path(temporary)
        ports = {service: free_port() for service in ("ingestion", "ai", "gateway")}
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRIDGE_")}
        env.update(
            MEMBRIDGE_HOME=str(directory / "home"),
            MEMBRIDGE_API_KEY="http-test-only-token",
            MEMBRIDGE_DATABASE_URL=f"sqlite:///{directory / 'http.db'}",
        )
        log_path = directory / "services.log"
        with log_path.open("w", encoding="utf-8") as logs:
            try:
                for service, port in ports.items():
                    service_env = dict(env)
                    args = [sys.executable, "-m", "membridge", "serve", service, "--port", str(port)]
                    if service == "gateway":
                        service_env.update(
                            MEMBRIDGE_INGESTION_URL=f"http://127.0.0.1:{ports['ingestion']}",
                            MEMBRIDGE_AI_URL=f"http://127.0.0.1:{ports['ai']}",
                        )
                        args.append("--with-worker")
                    processes.append(
                        subprocess.Popen(
                            args,
                            env=service_env,
                            cwd=ROOT,
                            stdout=logs,
                            stderr=logs,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                    )
                with httpx.Client(
                    timeout=10, headers={"Authorization": "Bearer http-test-only-token"}
                ) as client:
                    for service, port in ports.items():
                        deadline = time.monotonic() + 30
                        while True:
                            try:
                                response = client.get(f"http://127.0.0.1:{port}/health/ready")
                                if response.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if time.monotonic() > deadline:
                                raise RuntimeError(f"{service} did not become ready")
                            time.sleep(0.2)
                    env["MEMBRIDGE_SERVER"] = f"http://127.0.0.1:{ports['gateway']}"
                    project = directory / "project"
                    project.mkdir()
                    (project / "AGENTS.md").write_text("Preserve these instructions.\n", encoding="utf-8")

                    def cli(*args):
                        result = subprocess.run(
                            [sys.executable, "-m", "membridge", *args, "--project", str(project)],
                            cwd=ROOT,
                            env=env,
                            capture_output=True,
                            encoding="utf-8",
                            timeout=45,
                            check=True,
                        )
                        return result.stdout

                    cli("init")
                    job = json.loads(
                        cli(
                            "save",
                            "--from",
                            "opencode",
                            "--file",
                            str(ROOT / "data/samples/opencode/v2-session.json"),
                        )
                    )
                    assert job["state"] == "complete"
                    memories = json.loads(cli("search", "Postgres"))
                    assert memories and memories[0]["type"] == "decision"
                    source = json.loads(cli("evidence", memories[0]["id"]))
                    assert source["messages"][0]["id"] == "msg_decision"
                    cli("handoff", "--to", "codex")
                    content = (project / "AGENTS.md").read_text(encoding="utf-8")
                    assert content.startswith("Preserve these instructions.")
                    assert (
                        "Postgres" in content and "pagination" in content and "Source (extractive)" in content
                    )
                    cli("handoff", "--to", "codex")
                    assert (project / "AGENTS.md").read_text(encoding="utf-8") == content
                    reverse = json.loads(
                        cli(
                            "save",
                            "--from",
                            "codex",
                            "--file",
                            str(ROOT / "data/samples/codex/session.jsonl"),
                        )
                    )
                    assert reverse["state"] == "complete"
                    cli("handoff", "--to", "opencode")
                    updated = (project / "AGENTS.md").read_text(encoding="utf-8")
                    assert "codex:codex-demo" in updated and "expired cursor" in updated
                    assert updated.startswith("Preserve these instructions.")
                    # Exercise the expanded registry through the separate ingestion and AI services.
                    for source_tool, fixture, target, filename, expected in [
                        ("claude", "claude/session.jsonl", "gemini", "GEMINI.md", "claude:claude-demo"),
                        ("gemini", "gemini/session.jsonl", "claude", "CLAUDE.md", "gemini:gemini-jsonl-demo"),
                        ("aider", "aider/history.md", "aider", "MEMBRIDGE_HANDOFF.md", "whitespace-only"),
                        (
                            "deepseek",
                            "universal/deepseek.json",
                            "file",
                            "MEMBRIDGE_HANDOFF.md",
                            "deepseek:deepseek-demo",
                        ),
                    ]:
                        saved = json.loads(
                            cli("save", "--from", source_tool, "--file", str(ROOT / "data/samples" / fixture))
                        )
                        assert saved["state"] == "complete"
                        cli("handoff", "--to", target, "--limit", "100", "--max-chars", "32000")
                        assert expected in (project / filename).read_text(encoding="utf-8")
                    pasted = cli("handoff", "--to", "grok", "--limit", "100", "--max-chars", "32000")
                    assert "deepseek:deepseek-demo" in pasted
                    print(
                        "PASS: native OpenCode/Codex/Claude/Gemini/Aider and portable DeepSeek/Grok routes through separate HTTP services"
                    )
            except Exception:
                logs.flush()
                print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    if os.name == "nt":
                        # Windows venv python.exe can be a launcher; include its child interpreter.
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        process.terminate()
                for process in reversed(processes):
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    main()
