import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError

from membridge.checkpoint import build_checkpoint, task_key
from membridge.models import Session
from membridge.reconcile import decision_subject, decision_value
from membridge.repository import FILE_PATTERN

metadata = MetaData()
sessions = Table(
    "sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project", String, index=True),
    Column("digest", String),
    Column("payload", JSON),
    Column("updated", Float),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, index=True),
    Column("digest", String),
    Column("project", String, index=True),
    Column("payload", JSON),
    Column("state", String, index=True),
    Column("attempts", Integer),
    Column("created", Float),
    Column("updated", Float),
    Column("error", String),
)
memories = Table(
    "memories",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, index=True),
    Column("project", String, index=True),
    Column("source_tool", String),
    Column("type", String),
    Column("text", Text),
    Column("status", String),
    Column("extraction_mode", String),
    Column("evidence", JSON),
    Column("source_messages", JSON),
    Column("source_session", String),
    Column("embedding", JSON),
    Column("embedding_model", String),
    Column("created", Float),
)
commits = Table(
    "session_commits",
    metadata,
    Column("session_id", String, primary_key=True),
    Column("project", String, index=True),
    Column("digest", String),
    Column("payload", JSON),
    Column("signature", Text),
    Column("checkpoint", JSON),
    Column("updated", Float),
)
resolutions = Table(
    "memory_resolutions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project", String, index=True),
    Column("memory_id", String),
    Column("replacement_id", String),
    Column("action", String),
    Column("created", Float),
)
details = Table(
    "memory_details",
    metadata,
    Column("memory_id", String, primary_key=True),
    Column("project", String, index=True),
    Column("attributes", JSON),
)
forgotten = Table(
    "forgotten_sources",
    metadata,
    Column("id", String, primary_key=True),
    Column("project", String, index=True),
    Column("digest", String),
    Column("reason", String),
)
policies = Table(
    "project_policies",
    metadata,
    Column("project", String, primary_key=True),
    Column("retention_days", Integer),
)


guards = Table("project_guards", metadata, Column("project", String, primary_key=True))


def tokens(value):
    return re.findall(r"[\w]+", value.casefold())


def cosine(a, b):
    if not a or len(a) != len(b):
        return 0.0
    divisor = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / divisor if divisor else 0.0


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        self.postgres = self.engine.dialect.name == "postgresql"
        if self.engine.dialect.name == "sqlite":
            with self.engine.begin() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=10000"))
        if self.postgres:
            with self.engine.begin() as conn:
                # Serialize first-run DDL across gateway/worker replicas.
                conn.execute(text("SELECT pg_advisory_xact_lock(713478129)"))
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                metadata.create_all(conn)
                # Unconstrained dimension permits compatible endpoints with different dimensions.
                conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS vector_embedding vector"))
        else:
            metadata.create_all(self.engine)

    def health(self):
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def _gate(self, conn, project):
        # Serialize imports with forgetting, including brand-new sessions captured concurrently.
        if self.postgres:
            key = int.from_bytes(hashlib.sha256(project.encode()).digest()[:8], "big", signed=True)
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        elif self.engine.dialect.name == "sqlite":
            conn.exec_driver_sql("INSERT OR IGNORE INTO project_guards(project) VALUES (?)", (project,))

    def enqueue(self, session: Session, refresh=False):
        payload = session.model_dump()
        identity_payload = dict(
            payload, metadata={k: v for k, v in payload["metadata"].items() if k != "repository"}
        )
        digest = hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()
        sid = hashlib.sha256(
            f"{session.project}|{session.source_tool}|{session.session_id}".encode()
        ).hexdigest()
        jid = hashlib.sha256(f"{sid}|{digest}".encode()).hexdigest()
        now = time.time()
        try:
            with self.engine.begin() as conn:
                self._gate(conn, session.project)
                blocked = (
                    conn.execute(
                        select(forgotten).where(
                            forgotten.c.project == session.project,
                            forgotten.c.id.in_([sid, "project:" + session.project]),
                        )
                    )
                    .mappings()
                    .all()
                )
                for marker in blocked:
                    if marker["reason"] == "retention" and marker["digest"] != digest:
                        conn.execute(delete(forgotten).where(forgotten.c.id == marker["id"]))
                    else:
                        raise ValueError(
                            "Source was forgotten or expired; restore it explicitly before importing"
                        )
                old = (
                    conn.execute(select(sessions).where(sessions.c.id == sid).with_for_update())
                    .mappings()
                    .first()
                )
                existing = conn.execute(select(jobs).where(jobs.c.id == jid)).mappings().first()
                if existing and old and old["digest"] == digest:
                    needs_checkpoint = (
                        existing["state"] == "complete"
                        and not conn.execute(
                            select(commits.c.session_id).where(commits.c.session_id == sid)
                        ).first()
                    )
                    if (not refresh and not needs_checkpoint) or existing["state"] in {
                        "pending",
                        "processing",
                    }:
                        return self._job_public(existing)
                if existing:
                    # Re-importing a previous revision restores it deliberately.
                    conn.execute(
                        update(jobs)
                        .where(jobs.c.id == jid)
                        .values(state="pending", attempts=0, error="", updated=now)
                    )
                else:
                    conn.execute(
                        insert(jobs).values(
                            id=jid,
                            session_id=sid,
                            project=session.project,
                            digest=digest,
                            payload=payload,
                            state="pending",
                            attempts=0,
                            created=now,
                            updated=now,
                            error="",
                        )
                    )
                values = dict(project=session.project, digest=digest, payload=payload, updated=now)
                if old:
                    conn.execute(update(sessions).where(sessions.c.id == sid).values(**values))
                else:
                    conn.execute(insert(sessions).values(id=sid, **values))
        except IntegrityError:
            existing = self.job(jid, session.project)
            if existing:
                return existing
            raise
        return self.job(jid, session.project)

    @staticmethod
    def _job_public(row):
        return {k: row[k] for k in ("id", "project", "state", "attempts", "created", "updated", "error")}

    def job(self, jid, project):
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(jobs).where(jobs.c.id == jid, jobs.c.project == project))
                .mappings()
                .first()
            )
            return self._job_public(row) if row else None

    def list_jobs(self, project):
        with self.engine.connect() as conn:
            return [
                self._job_public(r)
                for r in conn.execute(
                    select(jobs).where(jobs.c.project == project).order_by(jobs.c.created.desc()).limit(100)
                ).mappings()
            ]

    def retry(self, jid, project):
        with self.engine.begin() as conn:
            result = conn.execute(
                update(jobs)
                .where(jobs.c.id == jid, jobs.c.project == project, jobs.c.state == "failed")
                .values(state="pending", attempts=0, error="", updated=time.time())
            )
            return result.rowcount == 1

    def claim(self, jid=None):
        now = time.time()
        with self.engine.begin() as conn:
            # Expired leases are reclaimed; long AI work renews through heartbeat().
            conn.execute(
                update(jobs)
                .where(jobs.c.state == "processing", jobs.c.updated < now - 120, jobs.c.attempts >= 3)
                .values(state="failed", error="Worker lease expired", updated=now)
            )
            condition = (jobs.c.state == "pending") | (
                (jobs.c.state == "processing") & (jobs.c.updated < now - 120)
            )
            query = select(jobs).where(condition, jobs.c.attempts < 3)
            if jid:
                query = query.where(jobs.c.id == jid)
            row = (
                conn.execute(query.order_by(jobs.c.created).with_for_update(skip_locked=True).limit(1))
                .mappings()
                .first()
            )
            if not row:
                return None
            attempt = row["attempts"] + 1
            result = conn.execute(
                update(jobs)
                .where(jobs.c.id == row["id"], jobs.c.state == row["state"], jobs.c.updated == row["updated"])
                .values(state="processing", attempts=attempt, updated=now)
            )
            return dict(row, attempts=attempt) if result.rowcount == 1 else None

    def heartbeat(self, job):
        with self.engine.begin() as conn:
            conn.execute(
                update(jobs)
                .where(
                    jobs.c.id == job["id"], jobs.c.state == "processing", jobs.c.attempts == job["attempts"]
                )
                .values(updated=time.time())
            )

    def incremental_base(self, job, signature):
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(commits).where(commits.c.session_id == job["session_id"]))
                .mappings()
                .first()
            )
        if not row or row["signature"] != signature:
            return None
        old, new = row["payload"]["messages"], job["payload"]["messages"]
        if len(new) > len(old) and new[: len(old)] == old:
            return dict(row)
        return None

    def finish(self, job, extracted, model, vectors, mode="extractive", signature="", base_digest=None):
        if len(extracted) != len(vectors):
            raise ValueError("Memory and embedding counts differ")
        with self.engine.begin() as conn:
            self._gate(conn, job["project"])
            current = (
                conn.execute(select(sessions).where(sessions.c.id == job["session_id"]).with_for_update())
                .mappings()
                .one()
            )
            lease = (
                conn.execute(select(jobs).where(jobs.c.id == job["id"]).with_for_update()).mappings().one()
            )
            if lease["state"] != "processing" or lease["attempts"] != job["attempts"]:
                return
            if current["digest"] != job["digest"]:
                conn.execute(
                    update(jobs).where(jobs.c.id == job["id"]).values(state="superseded", updated=time.time())
                )
                return
            previous = (
                conn.execute(select(commits).where(commits.c.session_id == job["session_id"]))
                .mappings()
                .first()
            )
            if base_digest and (not previous or previous["digest"] != base_digest):
                raise ValueError("Incremental extraction base changed; retry the job")
            # Manual lifecycle choices survive a model refresh or full-session re-extraction.
            overrides = {}
            for old in conn.execute(
                select(memories, resolutions.c.action)
                .join(resolutions, resolutions.c.memory_id == memories.c.id)
                .where(memories.c.session_id == job["session_id"])
                .order_by(resolutions.c.created)
            ).mappings():
                overrides[(old["type"], old["text"].casefold())] = (
                    "superseded" if old["action"].endswith("supersedes") else old["action"]
                )
            if not base_digest:
                # Keep source evidence/history addressable; exclude replaced revisions from normal retrieval.
                conn.execute(
                    update(memories)
                    .where(memories.c.session_id == job["session_id"], memories.c.status != "superseded")
                    .values(status="superseded")
                )
            for memory, vector in zip(extracted, vectors):
                mid = str(uuid.uuid4())
                effective_status = overrides.get((memory.type, memory.text.casefold()), memory.status)
                subject = decision_subject(memory.text, memory.subject) if memory.type == "decision" else None
                value = decision_value(memory.text, subject) if subject else None
                baseline = job["payload"].get("metadata", {}).get("repository", {})
                if subject and value and effective_status == "active":
                    candidates = (
                        conn.execute(
                            select(memories, details.c.attributes)
                            .join(details, details.c.memory_id == memories.c.id)
                            .where(
                                memories.c.project == job["project"],
                                memories.c.type == "decision",
                                memories.c.status == "active",
                            )
                        )
                        .mappings()
                        .all()
                    )
                    for old in candidates:
                        attrs = old["attributes"] or {}
                        if (
                            attrs.get("subject") == subject
                            and attrs.get("value") != value
                            and attrs.get("repository", {}).get("branch") == baseline.get("branch")
                        ):
                            conn.execute(
                                update(memories).where(memories.c.id == old["id"]).values(status="superseded")
                            )
                            # The replacement ID is attached after insertion below.
                            conn.execute(
                                insert(resolutions).values(
                                    id=str(uuid.uuid4()),
                                    project=job["project"],
                                    memory_id=old["id"],
                                    replacement_id=mid,
                                    action="automatic_supersedes",
                                    created=time.time(),
                                )
                            )
                if memory.type == "todo" and memory.status == "completed":
                    existing = (
                        conn.execute(
                            select(memories).where(
                                memories.c.project == job["project"],
                                memories.c.type == "todo",
                                memories.c.status == "active",
                            )
                        )
                        .mappings()
                        .all()
                    )
                    for old in existing:
                        if task_key(old["text"]) == task_key(memory.text):
                            conn.execute(
                                update(memories).where(memories.c.id == old["id"]).values(status="completed")
                            )
                conn.execute(
                    insert(memories).values(
                        id=mid,
                        session_id=job["session_id"],
                        project=job["project"],
                        source_tool=job["payload"]["source_tool"],
                        type=memory.type,
                        text=memory.text,
                        status=effective_status,
                        extraction_mode=mode,
                        evidence=memory.evidence,
                        embedding=vector,
                        source_session=job["payload"]["session_id"],
                        source_messages=[m for m in job["payload"]["messages"] if m["id"] in memory.evidence],
                        embedding_model=model,
                        created=time.time(),
                    )
                )
                if self.postgres and vector:
                    conn.execute(
                        text("UPDATE memories SET vector_embedding=CAST(:v AS vector) WHERE id=:id"),
                        {"v": json.dumps(vector), "id": mid},
                    )
                conn.execute(
                    insert(details).values(
                        memory_id=mid,
                        project=job["project"],
                        attributes={
                            "repository": baseline,
                            "subject": subject,
                            "value": value,
                            "files": sorted(
                                set(
                                    FILE_PATTERN.findall(memory.text)
                                    + [
                                        name
                                        for message in job["payload"]["messages"]
                                        if message["id"] in memory.evidence
                                        for name in FILE_PATTERN.findall(message["content"])
                                    ]
                                )
                            )[:30],
                        },
                    )
                )
            values = dict(
                project=job["project"],
                digest=job["digest"],
                payload=job["payload"],
                signature=signature,
                checkpoint=build_checkpoint(Session.model_validate(job["payload"]), extracted),
                updated=time.time(),
            )
            if previous:
                conn.execute(
                    update(commits).where(commits.c.session_id == job["session_id"]).values(**values)
                )
            else:
                conn.execute(insert(commits).values(session_id=job["session_id"], **values))
            conn.execute(
                update(jobs)
                .where(jobs.c.id == job["id"])
                .values(state="complete", error="", updated=time.time())
            )

    def fail(self, job, reason):
        with self.engine.begin() as conn:
            conn.execute(
                update(jobs)
                .where(
                    jobs.c.id == job["id"], jobs.c.state == "processing", jobs.c.attempts == job["attempts"]
                )
                .values(
                    state="failed" if job["attempts"] >= 3 else "pending", error=reason, updated=time.time()
                )
            )

    def search(self, project, query, limit=12, model="lexical", vector=None, session_id=None, history=False):
        with self.engine.connect() as conn:
            selection = select(memories).where(memories.c.project == project)
            if not history:
                selection = selection.where(memories.c.status != "superseded")
            if session_id:
                selection = selection.where(memories.c.session_id == session_id)
            rows = [dict(r) for r in conn.execute(selection.order_by(memories.c.created.desc())).mappings()]
            # pgvector performs cosine distance in Postgres; local SQLite uses the same scoring in Python.
            semantic = {}
            if self.postgres and vector:
                result = conn.execute(
                    text(
                        "SELECT id, 1-(vector_embedding <=> CAST(:v AS vector)) AS score "
                        "FROM memories WHERE project=:p AND embedding_model=:m AND vector_embedding IS NOT NULL "
                        "AND vector_dims(vector_embedding)=:d"
                    ),
                    {"v": json.dumps(vector), "p": project, "m": model, "d": len(vector)},
                )
                semantic = {r.id: float(r.score or 0) for r in result}
        terms = set(tokens(query))
        for row in rows:
            counts = Counter(tokens(row["text"]))
            lexical = sum(1 for t in terms if counts[t]) / max(1, len(terms))
            similarity = (
                semantic.get(row["id"], cosine(vector or [], row["embedding"]))
                if row["embedding_model"] == model
                else 0
            )
            age = max(0, time.time() - row["created"]) / 86400
            row["score"] = (
                (lexical + max(0, similarity))
                if terms
                else 1 / (1 + age / 30) + (0.1 if row["type"] == "todo" and row["status"] == "active" else 0)
            )
        if terms:
            rows = [r for r in rows if r["score"] > 0.05]
        rows.sort(key=lambda r: (r["created"],) if history else (r["score"], r["created"]), reverse=True)
        # Remove duplicate facts across sessions while retaining evidence from the chosen source.
        unique = {}
        for row in rows:
            unique.setdefault(row["id"] if history else (row["type"], row["text"].casefold()), row)
        return [
            {k: v for k, v in r.items() if k not in {"embedding", "embedding_model", "source_messages"}}
            for r in list(unique.values())[:limit]
        ]

    def checkpoint(self, project, source_job=None):
        with self.engine.connect() as conn:
            query = select(commits).where(commits.c.project == project)
            if source_job:
                job = (
                    conn.execute(
                        select(jobs).where(
                            jobs.c.id == source_job, jobs.c.project == project, jobs.c.state == "complete"
                        )
                    )
                    .mappings()
                    .first()
                )
                if not job:
                    raise ValueError("Completed source job not found in this project")
                query = query.where(
                    commits.c.session_id == job["session_id"], commits.c.digest == job["digest"]
                )
            row = conn.execute(query.order_by(commits.c.updated.desc()).limit(1)).mappings().first()
            lifecycle = (
                conn.execute(
                    select(memories).where(
                        memories.c.project == project,
                        memories.c.session_id == (row["session_id"] if row else ""),
                    )
                )
                .mappings()
                .all()
            )
            tasks = (
                conn.execute(
                    select(memories)
                    .where(memories.c.project == project, memories.c.type == "todo")
                    .order_by(memories.c.created.desc())
                )
                .mappings()
                .all()
            )
        if source_job and not row:
            raise ValueError("Source revision changed; save the latest session before switching")
        if not row:
            return None
        checkpoint = json.loads(json.dumps(row["checkpoint"]))
        current = [memory for memory in lifecycle if memory["status"] not in {"superseded", "disputed"}]
        inactive = [memory for memory in lifecycle if memory["status"] in {"superseded", "disputed"}]
        active_ids = {mid for memory in current for mid in memory["evidence"]}
        inactive_ids = {mid for memory in inactive for mid in memory["evidence"]}
        inactive_texts = {m["text"] for m in inactive} - {m["text"] for m in current}
        for field, items in checkpoint["fields"].items():
            retained = []
            for item in items:
                evidence = set(item["evidence"])
                if evidence & inactive_ids and not evidence & active_ids:
                    continue
                text = item["text"]
                for old_text in inactive_texts:
                    text = text.replace(old_text, "")
                if text.strip():
                    retained.append(dict(item, text=text.strip()))
            checkpoint["fields"][field] = retained
        task_states = {}
        for task in tasks:
            task_states.setdefault(task_key(task["text"]), task["status"])
        checkpoint["fields"]["next_steps"] = [
            item
            for item in checkpoint["fields"]["next_steps"]
            if task_states.get(task_key(item["text"]), "active") == "active"
        ]
        return dict(checkpoint, session_key=row["session_id"], digest=row["digest"])

    def resolve(self, project, memory_id, action, replacement_id=None):
        if action not in {"supersedes", "completed", "disputed"}:
            raise ValueError("Unknown resolution action")
        with self.engine.begin() as conn:
            self._gate(conn, project)
            old = (
                conn.execute(
                    select(memories).where(memories.c.id == memory_id, memories.c.project == project)
                )
                .mappings()
                .first()
            )
            if not old:
                raise ValueError("Memory not found in this project")
            if action == "completed" and old["type"] != "todo":
                raise ValueError("Only a TODO can be marked completed")
            if action == "supersedes":
                new = (
                    conn.execute(
                        select(memories).where(
                            memories.c.id == replacement_id,
                            memories.c.project == project,
                            memories.c.status == "active",
                        )
                    )
                    .mappings()
                    .first()
                )
                if not new or new["id"] == old["id"] or new["type"] != old["type"]:
                    raise ValueError(
                        "Replacement must be a different active memory of the same type in this project"
                    )
            conn.execute(
                update(memories)
                .where(memories.c.id == memory_id)
                .values(status="superseded" if action == "supersedes" else action)
            )
            conn.execute(
                insert(resolutions).values(
                    id=str(uuid.uuid4()),
                    project=project,
                    memory_id=memory_id,
                    replacement_id=replacement_id if action == "supersedes" else None,
                    action=action,
                    created=time.time(),
                )
            )
        return {"memory_id": memory_id, "action": action, "replacement_id": replacement_id}

    def evidence(self, mid, project):
        with self.engine.connect() as conn:
            memory = (
                conn.execute(select(memories).where(memories.c.id == mid, memories.c.project == project))
                .mappings()
                .first()
            )
            if not memory:
                return None
            return {
                "memory_id": mid,
                "source_session": memory["source_session"],
                "source_tool": memory["source_tool"],
                "messages": memory["source_messages"],
            }

    def contexts(self, project):
        with self.engine.connect() as conn:
            return {
                row.memory_id: row.attributes
                for row in conn.execute(select(details).where(details.c.project == project))
            }

    def saved_projects(self):
        with self.engine.connect() as conn:
            return sorted(set(conn.execute(select(commits.c.project)).scalars()))

    def resumable(self, project):
        """Only committed revisions; never infer a source from the destination tool."""
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(commits, jobs.c.id.label("job_id"))
                    .join(
                        jobs,
                        (jobs.c.session_id == commits.c.session_id) & (jobs.c.digest == commits.c.digest),
                    )
                    .where(
                        commits.c.project == project, jobs.c.project == project, jobs.c.state == "complete"
                    )
                    .order_by(commits.c.updated.desc(), jobs.c.updated.desc())
                )
                .mappings()
                .all()
            )
        result, seen = [], set()
        for row in rows:
            payload = row["payload"]
            if row["session_id"] in seen or payload["source_tool"] == "membridge":
                continue
            seen.add(row["session_id"])
            result.append(
                {
                    "id": row["session_id"],
                    "job_id": row["job_id"],
                    "source_tool": payload["source_tool"],
                    "source_session": payload["session_id"],
                    "title": payload.get("title", ""),
                    "updated": row["updated"],
                    "task": " ".join(
                        item["text"] for item in row["checkpoint"].get("fields", {}).get("current_task", [])
                    )[:240],
                }
            )
        return result

    def session_list(self, project):
        with self.engine.connect() as conn:
            return [
                {
                    "id": r["id"],
                    "source_session": r["payload"]["session_id"],
                    "source_tool": r["payload"]["source_tool"],
                    "title": r["payload"].get("title", ""),
                    "updated": r["updated"],
                }
                for r in conn.execute(
                    select(sessions).where(sessions.c.project == project).order_by(sessions.c.updated.desc())
                ).mappings()
            ]

    def corrections(self, project, origin_session):
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(memories, details.c.attributes)
                    .join(details, details.c.memory_id == memories.c.id)
                    .where(
                        memories.c.project == project,
                        memories.c.source_tool == "membridge",
                        memories.c.status != "superseded",
                    )
                    .order_by(memories.c.created.desc())
                )
                .mappings()
                .all()
            )
        return [
            {
                k: v
                for k, v in row.items()
                if k not in {"attributes", "embedding", "embedding_model", "source_messages"}
            }
            for row in rows
            if row["attributes"].get("origin_session") == origin_session
        ]

    def forget(self, project, session_id=None, apply=False, reason="forgotten"):
        with self.engine.begin() as conn:
            if apply:
                self._gate(conn, project)
            query = select(sessions).where(sessions.c.project == project)
            if session_id:
                query = query.where(sessions.c.id == session_id)
            selected = conn.execute(query).mappings().all()
            ids = [row["id"] for row in selected]
            if session_id and reason != "retention":
                derived = [
                    row.session_id
                    for row in conn.execute(
                        select(memories.c.session_id, details.c.attributes)
                        .join(details, details.c.memory_id == memories.c.id)
                        .where(memories.c.project == project)
                    )
                    if row.attributes.get("origin_session") in ids
                ]
                additional = (
                    conn.execute(
                        select(sessions).where(
                            sessions.c.project == project, sessions.c.id.in_(set(derived) - set(ids))
                        )
                    )
                    .mappings()
                    .all()
                )
                selected = [*selected, *additional]
                ids = [row["id"] for row in selected]
            mids = list(
                conn.execute(
                    select(memories.c.id).where(memories.c.project == project, memories.c.session_id.in_(ids))
                ).scalars()
            )
            result = {"sessions": len(ids), "memories": len(mids), "applied": apply}
            if not apply:
                return result
            if session_id and not selected:
                raise ValueError("Session not found in this project")
            markers = [(r["id"], r["digest"]) for r in selected]
            if not session_id:
                markers.append(("project:" + project, ""))
            for marker, digest in markers:
                conn.execute(delete(forgotten).where(forgotten.c.id == marker))
                conn.execute(
                    insert(forgotten).values(id=marker, project=project, digest=digest, reason=reason)
                )
            for table, column in ((details, details.c.memory_id), (resolutions, resolutions.c.memory_id)):
                conn.execute(delete(table).where(table.c.project == project, column.in_(mids)))
            conn.execute(
                delete(resolutions).where(
                    resolutions.c.project == project, resolutions.c.replacement_id.in_(mids)
                )
            )
            for table in (memories, jobs, commits):
                conn.execute(delete(table).where(table.c.project == project, table.c.session_id.in_(ids)))
            conn.execute(delete(sessions).where(sessions.c.project == project, sessions.c.id.in_(ids)))
            if not session_id:
                conn.execute(delete(policies).where(policies.c.project == project))
            return result

    def restore(self, project, session_id=None):
        with self.engine.begin() as conn:
            self._gate(conn, project)
            query = delete(forgotten).where(forgotten.c.project == project)
            if session_id:
                query = query.where(forgotten.c.id == session_id)
            return {
                "markers_removed": conn.execute(query).rowcount,
                "note": "Erased content is not recovered; explicit reimport is now permitted.",
            }

    def retention(self, project, days=None, apply=False):
        with self.engine.begin() as conn:
            policy = conn.execute(select(policies).where(policies.c.project == project)).mappings().first()
            if days is not None:
                if not 0 <= days <= 36500:
                    raise ValueError("Retention days must be 0 (disabled) through 36500")
                if apply:
                    conn.execute(delete(policies).where(policies.c.project == project))
                    conn.execute(insert(policies).values(project=project, retention_days=days))
            else:
                days = policy["retention_days"] if policy else 0
        expired = [
            s for s in self.session_list(project) if days and s["updated"] < time.time() - days * 86400
        ]
        result = {"retention_days": days, "expired_sessions": len(expired), "applied": apply}
        if apply:
            for row in expired:
                self.forget(project, row["id"], apply=True, reason="retention")
        return result

    def correct(self, project, memory_id, content):
        from membridge.models import Message
        from membridge.privacy import redact_text

        content = redact_text(content.strip())
        if not 1 <= len(content) <= 2000:
            raise ValueError("Correction must contain 1–2000 characters")
        with self.engine.begin() as conn:
            self._gate(conn, project)
            old = (
                conn.execute(
                    select(memories).where(memories.c.project == project, memories.c.id == memory_id)
                )
                .mappings()
                .first()
            )
            if not old:
                raise ValueError("Memory not found in this project")
            old_details = (
                conn.execute(
                    select(details.c.attributes).where(
                        details.c.memory_id == memory_id, details.c.project == project
                    )
                ).scalar()
                or {}
            )
            sid, mid = str(uuid.uuid4()), str(uuid.uuid4())
            source = Session(
                session_id=sid,
                source_tool="membridge",
                project=project,
                messages=[Message(id="correction", role="user", content=content)],
            )
            payload = source.model_dump()
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            now = time.time()
            conn.execute(
                insert(sessions).values(id=sid, project=project, payload=payload, digest=digest, updated=now)
            )
            conn.execute(
                insert(memories).values(
                    id=mid,
                    session_id=sid,
                    project=project,
                    source_tool="membridge",
                    source_session=source.session_id,
                    type=old["type"],
                    text=content,
                    status="active",
                    extraction_mode="user-correction",
                    evidence=["correction"],
                    source_messages=[m.model_dump() for m in source.messages],
                    embedding=[],
                    embedding_model="lexical",
                    created=now,
                )
            )
            conn.execute(update(memories).where(memories.c.id == memory_id).values(status="superseded"))
            conn.execute(
                insert(details).values(
                    memory_id=mid,
                    project=project,
                    attributes={
                        "origin_session": old_details.get("origin_session", old["session_id"]),
                        "repository": {},
                        "files": [],
                    },
                )
            )
            conn.execute(
                insert(resolutions).values(
                    id=str(uuid.uuid4()),
                    project=project,
                    memory_id=memory_id,
                    replacement_id=mid,
                    action="supersedes",
                    created=now,
                )
            )
            return {"memory_id": mid, "replaces": memory_id, "status": "active"}
