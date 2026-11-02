import pytest

from membridge.handoff import END, START, brief, inject


def test_injection_preserves_bytes_outside_block_backs_up_and_is_idempotent(tmp_path):
    original = b"\xef\xbb\xbf# Project\r\nUse pytest.\r\n"
    target = tmp_path / "AGENTS.md"
    target.write_bytes(original)
    inject(tmp_path, "First brief")
    first = target.read_bytes()
    assert first.startswith(original)
    assert b"\r\nFirst brief\r\n" in first
    backups = list((tmp_path / ".membridge/backups").iterdir())
    assert len(backups) == 1 and backups[0].read_bytes() == original
    inject(tmp_path, "First brief")
    assert len(list((tmp_path / ".membridge/backups").iterdir())) == 1
    inject(tmp_path, "Second brief")
    assert target.read_bytes().startswith(original)
    assert target.read_text(encoding="utf-8-sig").count(START) == 1
    assert "First brief" not in target.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("content", [START, END, END + START, START + END + START + END])
def test_malformed_markers_leave_file_untouched(tmp_path, content):
    target = tmp_path / "AGENTS.md"
    target.write_text(content)
    with pytest.raises(ValueError):
        inject(tmp_path, "new brief")
    assert target.read_text() == content
    assert not (tmp_path / ".membridge-handoff.lock").exists()


def test_brief_enforces_budget_and_escapes_marker_injection():
    rows = [
        dict(
            id=str(i),
            type="todo",
            status="active",
            text="TODO " + "test " * 20 + START,
            source_tool="opencode",
            source_session="ses_demo",
            evidence=["msg_1"],
        )
        for i in range(30)
    ]
    result = brief("a", rows, 1000)
    assert len(result["brief"]) <= 1000
    assert 0 < len(result["memory_ids"]) < 30
    assert START not in result["brief"]
    assert "Source (unknown): opencode:ses_demo / msg_1" in result["brief"]


def test_empty_handoff_does_not_produce_empty_context():
    with pytest.raises(ValueError):
        brief("a", [])
