import hmac
import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from membridge.adapters import adapter_catalog, parse
from membridge.config import Settings
from membridge.engine import Engine
from membridge.intelligence import Intelligence
from membridge.models import (
    CorrectionRequest,
    ForgetRequest,
    HandoffRequest,
    ImportRequest,
    ResolveRequest,
    RetentionRequest,
    SearchRequest,
    Session,
)
from membridge.privacy import redact


def create_app(service="gateway", settings=None, with_worker=False):
    settings = settings or Settings.load()
    runtime = {}

    @asynccontextmanager
    async def lifespan(app):
        stop, thread = threading.Event(), None
        if service == "gateway":
            runtime["engine"] = Engine(settings)
            if with_worker:
                thread = threading.Thread(target=runtime["engine"].run, args=(stop,), daemon=True)
                thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread:
                thread.join(timeout=5)
            if "engine" in runtime:
                runtime["engine"].store.engine.dispose()

    app = FastAPI(title=f"MemBridge {service}", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def body_limit(request, call_next):
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Maximum request size is 8 MiB"})
            chunks.append(chunk)
        request._body = b"".join(chunks)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic input values can contain raw transcripts. Return locations, never echoed input.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(e["loc"]), "type": e["type"], "msg": "Invalid field value"}
                    for e in exc.errors()
                ]
            },
        )

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": "Invalid session or memory data"})

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Upstream service unavailable"})

    def auth(request: Request):
        if settings.api_key and not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + settings.api_key
        ):
            raise HTTPException(401, "Invalid bearer token")

    @app.get("/health/live")
    def live():
        return {"status": "ok", "service": service}

    @app.get("/health/ready")
    def ready():
        if service == "gateway":
            try:
                runtime["engine"].store.health()
            except Exception:
                raise HTTPException(503, "Database unavailable") from None
        return {"status": "ready", "mode": Intelligence(settings).mode}

    secured = [Depends(auth)]
    if service == "ingestion":

        @app.post("/normalize", dependencies=secured)
        def normalize(body: ImportRequest):
            return parse(body.data, body.source_tool, body.project, body.input_format)

        return app

    if service == "ai":
        local_settings = Settings(**dict(vars(settings), ai_url=""))
        intelligence = Intelligence(local_settings)

        class EmbedRequest(BaseModel):
            texts: list[str] = Field(max_length=500)

        @app.post("/extract", dependencies=secured)
        def extract(session: Session):
            session = Session.model_validate(redact(session.model_dump()))
            return {"memories": intelligence.extract(session), "mode": intelligence.mode}

        @app.post("/embed", dependencies=secured)
        def embed(body: EmbedRequest):
            model, vectors = intelligence.embed([redact(t) for t in body.texts])
            return {"model": model, "vectors": vectors}

        return app

    if service != "gateway":
        raise ValueError("Unknown service")

    def engine():
        return runtime["engine"]

    @app.get("/adapters", dependencies=secured)
    def adapters():
        return adapter_catalog()

    @app.post("/imports", status_code=202, dependencies=secured)
    def ingest(body: ImportRequest):
        if settings.ingestion_url:
            with httpx.Client(timeout=60) as client:
                r = client.post(
                    settings.ingestion_url + "/normalize",
                    json=redact(body.model_dump()),
                    headers=engine().ai.internal_headers(),
                )
                r.raise_for_status()
                session = Session.model_validate(r.json())
        else:
            session = parse(body.data, body.source_tool, body.project, body.input_format)
        if body.repository:
            session.metadata["repository"] = body.repository
        return engine().store.enqueue(session, refresh=body.refresh)

    @app.get("/jobs", dependencies=secured)
    def list_jobs(project: str):
        return engine().store.list_jobs(project)

    @app.get("/jobs/{jid}", dependencies=secured)
    def get_job(jid: str, project: str):
        job = engine().store.job(jid, project)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.post("/jobs/{jid}/retry", dependencies=secured)
    def retry(jid: str, project: str):
        if not engine().store.retry(jid, project):
            raise HTTPException(409, "Job is absent or not failed")
        return engine().store.job(jid, project)

    @app.post("/search", dependencies=secured)
    def search(body: SearchRequest):
        return {"memories": engine().search(body.project, body.query, body.limit)}

    @app.post("/handoff", dependencies=secured)
    def handoff(body: HandoffRequest):
        try:
            return engine().handoff(body.project, body.query, body.limit, body.max_chars, body.source_job)
        except ValueError:
            raise HTTPException(404, "No matching memories; save a session or broaden your query")

    @app.get("/checkpoint", dependencies=secured)
    def checkpoint(project: str):
        return engine().store.checkpoint(project)

    @app.post("/resolve", dependencies=secured)
    def resolve(body: ResolveRequest):
        return engine().store.resolve(body.project, body.memory_id, body.action, body.replacement_id)

    @app.get("/history", dependencies=secured)
    def history(project: str):
        return engine().store.search(project, "", limit=100, history=True)

    @app.get("/contexts", dependencies=secured)
    def contexts(project: str):
        return engine().store.contexts(project)

    @app.get("/saved-sessions", dependencies=secured)
    def saved_sessions(project: str):
        return engine().store.session_list(project)

    @app.post("/forget", dependencies=secured)
    def forget(body: ForgetRequest):
        return engine().store.forget(body.project, body.session_id, body.apply)

    @app.post("/restore", dependencies=secured)
    def restore(body: ForgetRequest):
        return engine().store.restore(body.project, body.session_id)

    @app.post("/retention", dependencies=secured)
    def retention(body: RetentionRequest):
        return engine().store.retention(body.project, body.days, body.apply)

    @app.post("/correct", dependencies=secured)
    def correct(body: CorrectionRequest):
        return engine().store.correct(body.project, body.memory_id, body.text)

    @app.get("/memories/{mid}/evidence", dependencies=secured)
    def evidence(mid: str, project: str):
        result = engine().store.evidence(mid, project)
        if not result:
            raise HTTPException(404, "Memory not found")
        return result

    return app
