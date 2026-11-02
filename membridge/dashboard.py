"""Read-only project monitoring over authenticated loopback HTTP."""

import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse


def create_dashboard(project, token, client=None):
    from membridge.cli import Client

    project = Path(project).resolve()
    client = client or Client(project)
    if client.server:
        raise ValueError(
            "The dashboard currently requires local storage; use the gateway API for remote projects"
        )
    engine = client.engine
    assets = Path(__file__).parent / "web"
    app = FastAPI(title="MemBridge dashboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 16000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 16000:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def auth(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "Open the dashboard using the private URL printed in your terminal")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin requests are not allowed")

    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(assets / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    def style():
        return FileResponse(assets / "style.css", media_type="text/css")

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        from membridge.repository import verify
        from membridge.sync import status

        rows = engine.store.search(client.project, "", limit=100)
        return {
            "project": project.name,
            "read_only": True,
            "directory": str(project),
            "checkpoint": engine.store.checkpoint(client.project),
            "memories": rows,
            "history": engine.store.search(client.project, "", limit=100, history=True),
            "sessions": engine.store.session_list(client.project),
            "jobs": engine.store.list_jobs(client.project),
            "verification": verify(project, rows, engine.store.contexts(client.project)),
            "sync": {source: status(project, source) for source in ("opencode", "codex")},
            "retention": engine.store.retention(client.project),
        }

    @app.get("/api/evidence/{memory_id}", dependencies=[Depends(auth)])
    def evidence(memory_id: str):
        value = engine.store.evidence(memory_id, client.project)
        if value is None:
            raise HTTPException(404, "Memory not found in this project")
        return value

    @app.api_route("/api/action", methods=["POST", "PUT", "PATCH", "DELETE"], dependencies=[Depends(auth)])
    def read_only():
        # Reject older dashboards as well as direct requests; never dispatch mutations.
        raise HTTPException(405, "This dashboard is read-only. Use the MemBridge CLI to make changes.")

    return app
