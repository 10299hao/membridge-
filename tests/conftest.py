import json
from pathlib import Path

import pytest

from membridge.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def export():
    return json.loads((ROOT / "data/samples/opencode/v2-session.json").read_text())


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
