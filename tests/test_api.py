import threading

from fastapi.testclient import TestClient

from realitydiff import __version__
from realitydiff.api import create_app
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, WATCH_HIT, ScriptedModel


def test_http_git_surface(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT, WATCH_HIT]))
    with TestClient(create_app(engine, start_watcher=False), base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health").json()["version"] == __version__ == "0.4.5"
        created = client.post(
            "/api/claims", json={"statement": "Apple is going to replace Siri with an LLM."}
        )
        assert created.status_code == 200
        claim_id = created.json()["claim"]["id"]
        assert created.json()["commit"]["new_confidence"] == 56
        watched = client.post(f"/api/claims/{claim_id}/watch")
        assert watched.status_code == 200
        body = watched.json()
        assert body["changed"] is True
        assert body["commit"]["new_confidence"] == 72
        log = client.get(f"/api/claims/{claim_id}/log").json()
        assert len(log) == 2
        diff = client.get(
            f"/api/claims/{claim_id}/diff", params={"a": log[1]["id"], "b": log[0]["id"]}
        ).json()
        assert diff["previous_confidence"] == 56
        assert diff["new_confidence"] == 72
        blame = client.get(f"/api/claims/{claim_id}/blame").json()
        assert any(entry["path"] == "confidence" for entry in blame["entries"])
        reverted = client.post(f"/api/claims/{claim_id}/revert", json={"sha": log[1]["id"]})
        assert reverted.status_code == 200
        assert reverted.json()["new_confidence"] == 56
        home = client.get("/")
        assert home.status_code == 200
        assert b"Reality Diff" in home.content
        assert b"git for beliefs" in home.content
        assert b'id="watch-btn"' in home.content
        assert b'id="compare-view"' in home.content
        assert b'id="compare-run"' in home.content
        providers = client.get("/api/providers")
        assert providers.status_code == 200
        assert "openrouter" in providers.json()
        app_js = client.get("/static/app.js")
        assert app_js.status_code == 200
        assert b"browse-commit" in app_js.content
        tree = client.get(f"/api/claims/{claim_id}/tree")
        assert tree.status_code == 200
        assert tree.json()["nodes"]
        snapshot = client.get(f"/api/claims/{claim_id}/at/{log[0]['id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["browse"] is True
        assert snapshot.json()["commit"]["id"] == log[0]["id"]
        short = client.get(f"/api/claims/{claim_id}/diff", params={"a": log[1]["id"][:10], "b": log[0]["id"][:10]})
        assert short.status_code == 200
        assert short.json()["confidence_delta"] == 16


def test_activity_shows_background_research(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class Slow(ScriptedModel):
        def complete(self, prompt, *, search=False, **kwargs):
            if self.calls:
                started.set()
                assert release.wait(3)
            return super().complete(prompt, search=search, **kwargs)

    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=Slow([INIT, WATCH_HIT]))
    with TestClient(create_app(engine, start_watcher=False), base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/activity").json()["jobs"] == []
        created = client.post("/api/claims", json={"statement": "Apple is going to replace Siri with an LLM."})
        claim_id = created.json()["claim"]["id"]
        engine.model.models = ("grok-4.7",)
        engine.model.base_url = "https://api.x.ai/v1"
        engine.model.active_model = "grok-4.7"
        worker = threading.Thread(target=lambda: engine.watch(claim_id))
        worker.start()
        assert started.wait(3)
        jobs = client.get("/api/activity").json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["claim_id"] == claim_id
        assert jobs[0]["kind"] == "watch"
        assert "confidence" in jobs[0]["phase"]
        assert jobs[0]["model"] == "grok-4.7"
        assert jobs[0]["api"] == "api.x.ai"
        assert jobs[0]["elapsed_seconds"] >= 0
        release.set()
        worker.join(3)
        done = client.get("/api/activity").json()
        assert done["jobs"] == []
        assert done["model"] == "grok-4.7"
        assert done["api"] == "api.x.ai"
