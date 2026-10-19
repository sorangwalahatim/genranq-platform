from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import access, ai, auth, backlinks, growth, hub, local, monitor, platform, seo, social, spec, studio, work
from .routers import checklists, auditskills, errorcenter, clientportal, linksintel

S = get_settings()
app = FastAPI(title="Genranq API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=S.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, platform.router, work.router, seo.router, hub.router, spec.router, monitor.router, studio.router,
          backlinks.router, local.router, ai.router, social.router, growth.router, access.router,
          checklists.router, auditskills.router, errorcenter.router, clientportal.router, linksintel.router):
    app.include_router(r, prefix="/api")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.url.path.startswith("/f/"):  # public forms are embeddable by design (iframe snippet)
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors *")
    else:
        resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


@app.get("/api/health")
def health():
    from sqlalchemy import text
    from .db import app_engine
    with app_engine.connect() as c:
        c.execute(text("select 1"))
    return {"ok": True}


# Serve the built SPA when present (single-process deploy).
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")


if os.environ.get("EMBEDDED_WORKER") == "1":
    from .worker import start_background_thread

    @app.on_event("startup")
    def _start_worker():
        start_background_thread()
