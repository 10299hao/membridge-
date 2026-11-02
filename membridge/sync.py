"""Explicitly enabled, project-scoped polling. A worker never installs a startup service."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from membridge.lease import FileLease


def paths(project, source):
    from membridge.tools import tool_name

    source = tool_name(source)
    folder = Path(project).resolve() / ".membridge"
    if folder.is_symlink():
        raise ValueError("Refusing symlinked project configuration directory")
    folder.mkdir(exist_ok=True)
    return folder / f"watch-{source}.lock", folder / f"watch-{source}.stop", folder / f"watch-{source}.log"


def check_permission(client, allow_model):
    settings = client.settings
    if not allow_model and (
        client.server
        or settings.ai_url
        or (settings.llm_url and settings.llm_model)
        or (settings.llm_url and settings.embedding_model)
    ):
        raise ValueError(
            "Continuous sync may send content to configured services/models. Add --allow-model to opt in."
        )


def watch(project, source, interval=5, once=False, allow_model=False, managed=False):
    from membridge.cli import READERS, Client, check_job, save_session

    if source not in READERS:
        raise ValueError("Continuous sync requires a native source reader")
    client = Client(project)
    check_permission(client, allow_model)
    lock, stop, _ = paths(project, source)
    if lock.is_symlink() or stop.is_symlink():
        raise ValueError("Refusing symlinked watcher state")
    lease = FileLease(lock.with_suffix(".mutex"))
    try:
        if lock.exists():
            names = {p.name for p in lock.iterdir()}
            if names - {"state.json", "state.tmp"} or (not names and time.time() - lock.stat().st_mtime < 10):
                raise ValueError("Watcher lock has unknown or initializing state; try again shortly")
            for filename in names:
                candidate = lock / filename
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError("Unsafe stale watcher state")
                candidate.unlink()
            lock.rmdir()
        lock.mkdir()
    except Exception:
        lease.close()
        raise
    state = {"source": source, "pid": os.getpid(), "state": "running", "processed": 0, "errors": 0}
    seen = set()
    try:
        while not stop.exists():
            # Recheck environment/config permission on every pass, not only at startup.
            check_permission(Client(project), allow_model)
            try:
                if client.engine:
                    expired = client.engine.store.retention(client.project, apply=True)
                    if expired["expired_sessions"]:
                        from membridge.handoff import clear_generated

                        clear_generated(project)
                available = READERS[source](project).list_sessions()
                # Native readers return newest first; commit oldest first so the latest task wins.
                for row in reversed(available):
                    if stop.exists():
                        break
                    try:
                        job = save_session(project, source, None, row["id"], True, False, 300, "auto")
                        check_job(job)
                        if job["id"] not in seen:
                            state["processed"] += 1
                            seen.add(job["id"])
                    except ValueError as exc:
                        if "forgotten or expired" in str(exc):
                            state["skipped_forgotten"] = state.get("skipped_forgotten", 0) + 1
                        else:
                            state["errors"] += 1
                            state["last_error"] = "session_import_failed"
                    except Exception:
                        # Never persist provider errors or raw transcripts in watcher logs.
                        state["errors"] += 1
                        state["last_error"] = "session_import_failed"
            except Exception:
                state["errors"] += 1
                state["last_error"] = "session_discovery_failed"
            state["heartbeat"] = time.time()
            temporary = lock / "state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            os.replace(temporary, lock / "state.json")
            if once:
                break
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline and not stop.exists():
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        return state
    finally:
        for filename in ("state.tmp", "state.json"):
            (lock / filename).unlink(missing_ok=True)
        lock.rmdir()
        lease.close()
        if not managed:
            stop.unlink(missing_ok=True)


def start(project, source, interval, allow_model):
    from membridge.cli import READERS, Client

    if source not in READERS:
        raise ValueError("Continuous sync requires a native source reader")
    check_permission(Client(project), allow_model)
    lock, stop, log = paths(project, source)
    if stop.is_symlink() or log.is_symlink():
        raise ValueError("Unsafe watcher state")
    try:
        with FileLease(lock.with_suffix(".supervisor-mutex")):
            pass
    except ValueError:
        return status(project, source)
    try:
        with FileLease(lock.with_suffix(".mutex")):
            pass
    except ValueError:
        return status(project, source)
    stop.unlink(missing_ok=True)
    args = [
        sys.executable,
        "-m",
        "membridge",
        "supervise",
        "--project",
        str(project.resolve()),
        "--from",
        source,
        "--interval",
        str(interval),
    ]
    if allow_model:
        args.append("--allow-model")
    desired = lock.parent / "sync-config.json"
    config = json.loads(desired.read_text(encoding="utf-8")) if desired.exists() else {}
    config[source] = {"enabled": True, "interval": interval, "allow_model": allow_model}
    _write_json(desired, config)
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    )
    with log.open("ab") as output:
        process = subprocess.Popen(
            args, cwd=project.resolve(), stdin=subprocess.DEVNULL, stdout=output, stderr=output, **options
        )
    for _ in range(30):
        if process.poll() is not None:
            raise ValueError("Watcher exited during startup; inspect its project-local log")
        state_file = lock / "state.json"
        if state_file.exists():
            return json.loads(state_file.read_text(encoding="utf-8"))
        time.sleep(0.1)
    return {"pid": process.pid, "state": "starting", "source": source}


def status(project, source):
    lock, stop_file, _ = paths(project, source)
    if lock.is_symlink():
        raise ValueError("Refusing symlinked watcher state")
    if not lock.exists():
        if stop_file.exists():
            return {"source": source, "state": "stopped"}
        try:
            with FileLease(lock.with_suffix(".supervisor-mutex")):
                return {"source": source, "state": "stopped"}
        except ValueError:
            pass
    state_file = lock / "state.json"
    if not state_file.is_file():
        return {"source": source, "state": "starting-or-stale"}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": source, "state": "recovering-or-stopped"}
    try:
        with FileLease(lock.with_suffix(".mutex")):
            state["state"] = "recovering-or-stopped"
    except ValueError:
        pass
    if time.time() - state.get("heartbeat", 0) > 360:
        state["state"] = "stale-or-busy"
    return state


def stop(project, source):
    lock, stop_file, _ = paths(project, source)
    desired = lock.parent / "sync-config.json"
    if desired.exists():
        config = json.loads(desired.read_text(encoding="utf-8"))
        if source in config:
            config[source]["enabled"] = False
        _write_json(desired, config)
    if not lock.exists():
        try:
            with FileLease(lock.with_suffix(".supervisor-mutex")):
                return {"source": source, "state": "stopped"}
        except ValueError:
            pass
    if stop_file.is_symlink():
        raise ValueError("Refusing symlinked stop request")
    stop_file.touch()
    return {"source": source, "state": "stop-requested"}


def _write_json(path, value):
    import uuid

    if path.is_symlink():
        raise ValueError("Refusing symlinked configuration")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def supervise(project, source, interval, allow_model):
    lock, stop_file, log = paths(project, source)
    with FileLease(lock.with_suffix(".supervisor-mutex")):
        restart = 0
        while not stop_file.exists():
            args = [
                sys.executable,
                "-m",
                "membridge",
                "watch",
                "--managed",
                "--project",
                str(project.resolve()),
                "--from",
                source,
                "--interval",
                str(interval),
            ]
            if allow_model:
                args.append("--allow-model")
            with log.open("ab") as stream:
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                child = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, **options
                )
                _write_json(
                    lock.with_suffix(".supervisor.json"),
                    {"pid": os.getpid(), "worker_pid": child.pid, "restarts": restart},
                )
                while child.poll() is None:
                    time.sleep(0.2)
                if child.returncode == 0 or stop_file.exists():
                    break
            restart += 1
            deadline = time.monotonic() + min(30, 2 ** min(restart, 5))
            while not stop_file.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
        lock.with_suffix(".supervisor.json").unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)


def resume(project):
    lock, _, _ = paths(project, "opencode")
    desired = lock.parent / "sync-config.json"
    config = json.loads(desired.read_text(encoding="utf-8")) if desired.exists() else {}
    return {
        source: start(project, source, item["interval"], item["allow_model"])
        for source, item in config.items()
        if item.get("enabled")
    }
