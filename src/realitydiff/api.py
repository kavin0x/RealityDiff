from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from realitydiff.models import BlameReport, ClaimDiff, ClaimRecord, Commit, WatchResult
from realitydiff.service import RealityDiff
from realitydiff.watcher import WatchLoop


STATIC = Path(__file__).parent / "static"
DEFAULT_DB = os.environ.get("REALITYDIFF_DB", "data/realitydiff.sqlite")


class CreateClaimBody(BaseModel):
    statement: str = Field(min_length=3)
    author: str = "reasoner"


class CommitBody(BaseModel):
    message: str = Field(min_length=1)
    author: str = "user"
    reason: str = ""


class RevertBody(BaseModel):
    sha: str
    author: str = "user"


class WatchConfigBody(BaseModel):
    watching: bool = True
    interval_seconds: int | None = Field(default=None, ge=30)


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

    app = FastAPI(title="Reality Diff", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.watcher = watcher

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "name": "realitydiff"}

    @app.get("/api/claims")
    def list_claims() -> list[dict]:
        out = []
        for record in engine.store.list_claims():
            head = engine.store.head_commit(record.id)
            out.append(
                {
                    **record.model_dump(mode="json"),
                    "confidence": head.state.confidence if head else None,
                    "summary": head.state.summary if head else "",
                    "commit_count": len(engine.store.log(record.id)),
                }
            )
        return out

    @app.post("/api/claims")
    def create_claim(body: CreateClaimBody) -> dict:
        try:
            living, commit = engine.open_claim(body.statement, author=body.author)
        except Exception as exc:
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
    def claim_diff(claim_id: str, a: str | None = None, b: str | None = None) -> ClaimDiff:
        return _claim(engine, claim_id).diff(a, b)

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
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/claims/{claim_id}/watch")
    def claim_watch(claim_id: str) -> WatchResult:
        try:
            return engine.watch(claim_id, author="watcher")
        except Exception as exc:
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

    return app


def _claim(engine: RealityDiff, claim_id: str):
    try:
        return engine.claim(claim_id)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown claim {claim_id}") from exc
