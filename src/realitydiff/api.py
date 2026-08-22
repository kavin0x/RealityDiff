from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from realitydiff import __version__
from realitydiff.models import BlameReport, ClaimDiff, ClaimRecord, Commit, WatchResult
from realitydiff.service import RealityDiff, WatchBusy
from realitydiff.watcher import WatchLoop
from realitydiff.xai import XAIError


STATIC = Path(__file__).parent / "static"
DEFAULT_DB = os.environ.get("REALITYDIFF_DB", "data/realitydiff.sqlite")
log = logging.getLogger("realitydiff.api")


class CreateClaimBody(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    author: str = Field(default="reasoner", max_length=80)


class CommitBody(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    author: str = Field(default="user", max_length=80)
    reason: str = Field(default="", max_length=4000)


class RevertBody(BaseModel):
    sha: str = Field(min_length=7, max_length=64)
    author: str = Field(default="user", max_length=80)


class WatchConfigBody(BaseModel):
    watching: bool = True
    interval_seconds: int | None = Field(default=None, ge=30, le=86_400)


def create_app(
    engine: RealityDiff | None = None,
    *,
    start_watcher: bool = True,
) -> FastAPI:
    engine = engine or RealityDiff.open(DEFAULT_DB)
    watcher = WatchLoop(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_watcher:
            watcher.start()
        yield
        watcher.stop()

    app = FastAPI(title="Reality Diff", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.watcher = watcher

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if not isinstance(detail, str):
            detail = str(detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @app.get("/api/health")
    def health() -> dict[str, str]:
        try:
            engine.store.list_claims()
            db = "ok"
        except Exception as exc:
            db = f"error:{exc}"
        status = "ok" if db == "ok" else "degraded"
        return {"status": status, "name": "realitydiff", "version": __version__, "db": db}

    @app.get("/api/claims")
    def list_claims() -> list[dict]:
        out = []
        for record in engine.store.list_claims():
            head = engine.store.head_commit(record.id)
            history = engine.store.log(record.id)
            out.append(
                {
                    **record.model_dump(mode="json"),
                    "confidence": head.state.confidence if head else None,
                    "summary": head.state.summary if head else "",
                    "commit_count": len(history),
                    "spark": [c.new_confidence for c in reversed(history[-24:])],
                }
            )
        return out

    @app.post("/api/claims")
    def create_claim(body: CreateClaimBody) -> dict:
        try:
            living, commit = engine.open_claim(body.statement, author=body.author)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except XAIError as exc:
            raise HTTPException(502, f"Reasoner failed: {exc}") from exc
        except Exception as exc:
            log.exception("open_claim failed")
            raise HTTPException(502, f"Reasoner failed: {exc}") from exc
        return {
            "claim": living.refresh().model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    @app.get("/api/claims/{claim_id}")
    def get_claim(claim_id: str) -> dict:
        living = _claim(engine, claim_id)
        head = living.head
        working = living.working
        return {
            "claim": living.refresh().model_dump(mode="json"),
            "head": head.model_dump(mode="json") if head else None,
            "working": working.model_dump(mode="json") if working else None,
            "dirty": bool(head and working and head.state.tree_hash() != working.tree_hash()),
        }

    @app.get("/api/claims/{claim_id}/log")
    def claim_log(claim_id: str) -> list[Commit]:
        return _claim(engine, claim_id).log()

    @app.get("/api/claims/{claim_id}/diff")
    def claim_diff(
        claim_id: str,
        a: str | None = Query(default=None),
        b: str | None = Query(default=None),
    ) -> ClaimDiff:
        try:
            return _claim(engine, claim_id).diff(a, b)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/claims/{claim_id}/blame")
    def claim_blame(claim_id: str) -> BlameReport:
        return _claim(engine, claim_id).blame()

    @app.post("/api/claims/{claim_id}/commit")
    def claim_commit(claim_id: str, body: CommitBody) -> Commit:
        try:
            return _claim(engine, claim_id).commit(body.message, author=body.author, reason=body.reason)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/claims/{claim_id}/revert")
    def claim_revert(claim_id: str, body: RevertBody) -> Commit:
        try:
            return _claim(engine, claim_id).revert(body.sha, author=body.author)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/claims/{claim_id}/watch")
    def claim_watch(claim_id: str) -> WatchResult:
        _claim(engine, claim_id)
        try:
            return engine.watch(claim_id, author="watcher")
        except WatchBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except XAIError as exc:
            raise HTTPException(502, f"Watch failed: {exc}") from exc
        except Exception as exc:
            log.exception("watch failed")
            raise HTTPException(502, f"Watch failed: {exc}") from exc

    @app.post("/api/claims/{claim_id}/watching")
    def set_watching(claim_id: str, body: WatchConfigBody) -> ClaimRecord:
        _claim(engine, claim_id)
        engine.store.set_watching(claim_id, body.watching, body.interval_seconds)
        record = engine.store.get_claim(claim_id)
        assert record is not None
        return record

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC / "index.html")

        @app.get("/favicon.svg")
        def favicon() -> FileResponse:
            path = STATIC / "favicon.svg"
            if not path.exists():
                raise HTTPException(404, "no favicon")
            return FileResponse(path, media_type="image/svg+xml")

    return app


def _claim(engine: RealityDiff, claim_id: str):
    try:
        return engine.claim(claim_id)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown claim {claim_id}") from exc
