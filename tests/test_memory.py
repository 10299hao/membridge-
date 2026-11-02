import time

from sqlalchemy import update

from membridge.adapters import parse
from membridge.engine import Engine
from membridge.store import jobs


def process(engine, job):
    assert engine.process_one(job["id"])
    assert engine.store.job(job["id"], job["project"])["state"] == "complete"


def test_save_search_evidence_idempotency_and_project_isolation(settings, export):
    engine = Engine(settings)
    session = parse(export, "opencode", "a")
    job = engine.store.enqueue(session)
    process(engine, job)
    duplicate = engine.store.enqueue(session)
    assert duplicate["id"] == job["id"]
    assert duplicate["state"] == "complete"
    assert not engine.process_one(job["id"])
    rows = engine.search("a", "Postgres")
    assert len(rows) == 1
    assert rows[0]["type"] == "decision"
    assert engine.search("b", "Postgres") == []
    evidence = engine.store.evidence(rows[0]["id"], "a")
    assert evidence["messages"][0]["id"] == "msg_decision"
    assert engine.store.evidence(rows[0]["id"], "b") is None


def test_new_revision_replaces_memories_and_keeps_old_evidence_until_commit(settings, export):
    engine = Engine(settings)
    original = parse(export, "opencode", "a")
    process(engine, engine.store.enqueue(original))
    row = engine.search("a", "Postgres")[0]
    revised = original.model_copy(deep=True)
    revised.messages[1].content = "We chose SQLite for a local demo."
    job = engine.store.enqueue(revised)
    assert "Postgres" in engine.store.evidence(row["id"], "a")["messages"][0]["content"]
    process(engine, job)
    assert not engine.search("a", "Postgres")
    assert engine.search("a", "SQLite")


def test_obsolete_job_cannot_overwrite_newer_session(settings, export):
    engine = Engine(settings)
    original = parse(export, "opencode", "a")
    job = engine.store.enqueue(original)
    revised = original.model_copy(deep=True)
    revised.messages[1].content = "We chose SQLite instead."
    newer = engine.store.enqueue(revised)
    assert engine.process_one(job["id"])
    assert engine.store.job(job["id"], "a")["state"] == "superseded"
    process(engine, newer)
    assert not engine.search("a", "Postgres")


def test_provider_failure_is_durable_bounded_and_retryable(settings, export):
    class FailingAI:
        def extract(self, session):
            raise RuntimeError("SECRET: must not appear in job error")

    engine = Engine(settings, intelligence=FailingAI())
    job = engine.store.enqueue(parse(export, "opencode", "a"))
    for _ in range(3):
        assert engine.process_one(job["id"])
    state = engine.store.job(job["id"], "a")
    assert state["state"] == "failed" and state["attempts"] == 3
    assert "SECRET" not in state["error"]
    assert not engine.process_one(job["id"])
    assert engine.store.retry(job["id"], "a")
    assert engine.store.job(job["id"], "a")["state"] == "pending"


def test_expired_worker_lease_is_reclaimed_and_old_worker_is_fenced(settings, export):
    engine = Engine(settings)
    job = engine.store.enqueue(parse(export, "opencode", "a"))
    first = engine.store.claim(job["id"])
    with engine.store.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(updated=time.time() - 200))
    second = engine.store.claim(job["id"])
    assert second["attempts"] == 2
    engine.store.finish(first, [], "lexical", [])
    assert engine.store.job(job["id"], "a")["state"] == "processing"
    engine.store.finish(second, [], "lexical", [])
    assert engine.store.job(job["id"], "a")["state"] == "complete"


def test_lease_exhaustion_becomes_failed(settings, export):
    engine = Engine(settings)
    job = engine.store.enqueue(parse(export, "opencode", "a"))
    engine.store.claim(job["id"])
    with engine.store.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(attempts=3, updated=time.time() - 200))
    assert not engine.store.claim(job["id"])
    assert engine.store.job(job["id"], "a")["state"] == "failed"


def test_refresh_reextracts_without_duplicating_or_interrupting_active_job(settings, export):
    engine = Engine(settings)
    session = parse(export, "opencode", "a")
    job = engine.store.enqueue(session)
    process(engine, job)
    before = len(engine.search("a", limit=100))
    refreshed = engine.store.enqueue(session, refresh=True)
    assert refreshed["state"] == "pending" and refreshed["id"] == job["id"]
    claimed = engine.store.claim(job["id"])
    running = engine.store.enqueue(session, refresh=True)
    assert running["state"] == "processing" and running["attempts"] == 1
    memories = engine.ai.extract(session)
    model, vectors = engine.ai.embed([m.text for m in memories])
    engine.store.finish(claimed, memories, model, vectors)
    assert len(engine.search("a", limit=100)) == before
