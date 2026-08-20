from fastapi.testclient import TestClient

from realitydiff.api import create_app
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, WATCH_HIT, ScriptedModel


def test_http_git_surface(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT, WATCH_HIT]))
    with TestClient(create_app(engine, start_watcher=False)) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health").json()["version"]
        created = client.post(
            "/api/claims", json={"statement": "Apple is going to replace Siri with an LLM."}
        )
        assert created.status_code == 200
        claim_id = created.json()["claim"]["id"]
        assert created.json()["commit"]["new_confidence"] == 62
        watched = client.post(f"/api/claims/{claim_id}/watch")
        assert watched.status_code == 200
        body = watched.json()
        assert body["changed"] is True
        assert body["commit"]["new_confidence"] == 78
        log = client.get(f"/api/claims/{claim_id}/log").json()
        assert len(log) == 2
        diff = client.get(
            f"/api/claims/{claim_id}/diff", params={"a": log[1]["id"], "b": log[0]["id"]}
        ).json()
        assert diff["previous_confidence"] == 62
        assert diff["new_confidence"] == 78
        blame = client.get(f"/api/claims/{claim_id}/blame").json()
        assert any(entry["path"] == "confidence" for entry in blame["entries"])
        reverted = client.post(f"/api/claims/{claim_id}/revert", json={"sha": log[1]["id"]})
        assert reverted.status_code == 200
        assert reverted.json()["new_confidence"] == 62
        home = client.get("/")
        assert home.status_code == 200
        assert b"Reality Diff" in home.content
        assert b"claim.watch()" in home.content
        short = client.get(f"/api/claims/{claim_id}/diff", params={"a": log[1]["id"][:10], "b": log[0]["id"][:10]})
        assert short.status_code == 200
        assert short.json()["confidence_delta"] == 16
