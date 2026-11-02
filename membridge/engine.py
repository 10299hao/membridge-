import logging
import threading

from membridge.config import Settings
from membridge.handoff import brief
from membridge.intelligence import Intelligence
from membridge.models import Session
from membridge.store import Store

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings: Settings, store=None, intelligence=None):
        self.settings = settings
        self.store = store or Store(settings.database_url)
        self.ai = intelligence or Intelligence(settings)

    def process_one(self, jid=None):
        job = self.store.claim(jid)
        if not job:
            return False
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(20):
                try:
                    self.store.heartbeat(job)
                except Exception:
                    log.warning("Job heartbeat failed for %s", job["id"])

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            session = Session.model_validate(job["payload"])
            signature = getattr(self.ai, "signature", "")
            base = self.store.incremental_base(job, signature) if signature else None
            extraction_session = session
            if base:
                extraction_session = session.model_copy(
                    update={
                        "messages": session.messages[len(base["payload"]["messages"]) :],
                        "metadata": dict(
                            session.metadata,
                            extraction_context=[
                                dict(m, content=m["content"][:500]) for m in base["payload"]["messages"][-4:]
                            ],
                        ),
                    }
                )
            extracted = self.ai.extract(extraction_session)
            valid_ids = {m.id for m in session.messages}
            if any(not set(m.evidence).issubset(valid_ids) for m in extracted):
                raise ValueError("Extraction contains invalid evidence")
            model, vectors = self.ai.embed([m.text for m in extracted])
            self.store.finish(
                job,
                extracted,
                model,
                vectors,
                mode=self.ai.mode,
                signature=signature,
                base_digest=base["digest"] if base else None,
            )
        except Exception as exc:
            # Do not store exception bodies: upstream errors can echo prompts or credentials.
            self.store.fail(
                job, f"{type(exc).__name__}: processing failed; check provider configuration and availability"
            )
            log.warning("Processing failed for %s (%s)", job["id"], type(exc).__name__)
        finally:
            stop.set()
            thread.join(timeout=2)
        return True

    def search(self, project, query="", limit=12):
        model, vectors = self.ai.embed([query]) if query else ("lexical", [[]])
        return self.store.search(project, query, limit, model, vectors[0])

    def handoff(self, project, query="", limit=30, max_chars=6000, source_job=None):
        checkpoint = self.store.checkpoint(project, source_job) if source_job or not query else None
        if source_job:
            rows = self.store.search(project, "", limit, session_id=checkpoint["session_key"])
            rows = (self.store.corrections(project, checkpoint["session_key"]) + rows)[:limit]
        else:
            rows = self.search(project, query, limit)
        from membridge.repository import verify

        verification = verify(project, rows, self.store.contexts(project))
        result = brief(project, rows, max_chars, checkpoint=checkpoint, verification=verification)
        result["verification"] = verification
        return result

    def run(self, stop=None):
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                worked = self.process_one()
            except Exception as exc:
                log.warning("Worker unavailable (%s)", type(exc).__name__)
                worked = False
            stop.wait(0.2 if worked else 2)
