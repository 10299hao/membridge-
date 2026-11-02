"""Isolated synthetic dashboard preview for browser verification."""

import os
from pathlib import Path

import uvicorn

from membridge.cli import Client
from membridge.dashboard import create_dashboard
from membridge.models import Message, Session
from membridge.repository import snapshot

root = Path(__file__).resolve().parents[1] / "outputs" / "dashboard-review"
project = root / "project"
project.mkdir(parents=True, exist_ok=True)
for key in tuple(os.environ):
    if key.startswith("MEMBRIDGE_"):
        del os.environ[key]
os.environ["MEMBRIDGE_HOME"] = str(root / "memory")
(project / "src").mkdir(exist_ok=True)
(project / "src/tasks.py").write_text("# Initial synthetic task API\n", encoding="utf-8")
client = Client(project)
source = Session(
    session_id="demo-opencode",
    source_tool="opencode",
    project=client.project,
    title="Task API · pagination",
    messages=[
        Message(
            id="goal",
            role="user",
            content="Current task: Add cursor pagination to the task API.\nDecision: We chose PostgreSQL.\nPreference: Use pytest for backend tests.",
        ),
        Message(
            id="progress",
            role="assistant",
            content="Updated src/tasks.py with cursor validation.\nCompleted: Add request logging.\nTests: 8 passed; 1 failed.\nBlocker: Duplicate task IDs fail validation.\nNext step: Fix duplicate task ID handling.\nTODO: Add a regression test for expired cursors.",
        ),
    ],
)
source.metadata["repository"] = snapshot(project, source.messages)
job = client.engine.store.enqueue(source)
client.engine.process_one(job["id"])
(project / "src/tasks.py").write_text("# Changed after the saved session\n", encoding="utf-8")
print("http://127.0.0.1:8765/#token=local-dashboard-review", flush=True)
uvicorn.run(
    create_dashboard(project, "local-dashboard-review", client),
    host="127.0.0.1",
    port=8765,
    log_level="warning",
)
