from realitydiff.compress import is_material_change
from realitydiff.diffing import diff_states
from realitydiff.models import ClaimOutcome
from realitydiff.reasoner import apply_close_claim
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from realitydiff.xai import (
    CLOSE_CLAIM_TOOL,
    agent_tools,
    execute_agent_tool,
    extract_function_calls,
    parse_close_claim,
)
from tests.test_git_beliefs import state
from tests.test_reasoner import INIT, ScriptedModel, WATCH_HIT


WATCH_CLOSE = {
    "text": """```json
{
  "changed": true,
  "reason": "Apple shipped a foundation-model Siri runtime that replaces the old dialog manager.",
  "statement": "Apple will replace Siri's architecture with an LLM",
  "refined_statement": "Apple will replace Siri's classical NLU/dialog stack with a foundation-model core",
  "confidence": 98,
  "summary": "The architecture replacement shipped.",
  "evidence_for": [
    {
      "statement": "Apple documentation describes a foundation-model Siri runtime replacing the dialog manager",
      "source_url": "https://developer.apple.com/documentation/siri/foundation-model",
      "source_title": "Apple Developer",
      "weight": 1.0,
      "notes": "Primary source for the outcome"
    }
  ],
  "evidence_against": [],
  "unknowns": [],
  "predictions": [
    {
      "statement": "Apple publishes developer docs for a foundation-model Siri runtime",
      "due": "2026-12",
      "status": "confirmed",
      "how_to_falsify": "Docs never appear and Siri remains classical"
    }
  ]
}
```""",
    "close_claim": {
        "ok": True,
        "closed": True,
        "verdict": "confirmed",
        "outcome": "Apple shipped a foundation-model Siri runtime that replaces the old dialog manager.",
        "confidence": 100,
        "source_url": "https://developer.apple.com/documentation/siri/foundation-model",
        "source_title": "Apple Developer",
    },
}


def test_close_claim_tool_is_registered_with_search():
    tools = agent_tools(search=True)
    assert tools[0] == CLOSE_CLAIM_TOOL
    assert tools[0]["name"] == "close_claim"
    assert {"type": "web_search"} in tools
    assert {"type": "x_search"} in tools


def test_parse_close_claim_accepts_json_arguments():
    parsed = parse_close_claim(
        '{"verdict":"falsified","outcome":"The product never shipped and was cancelled.","source_url":"https://example.com/cancel"}'
    )
    assert parsed["ok"] is True
    assert parsed["verdict"] == "falsified"
    assert parsed["source_url"] == "https://example.com/cancel"


def test_parse_close_claim_rejects_rumor_shape():
    assert parse_close_claim({"verdict": "maybe", "outcome": "still a rumor"})["ok"] is False
    assert parse_close_claim({"verdict": "confirmed", "outcome": "nope"})["ok"] is False


def test_extract_and_execute_close_claim_function_call():
    body = {
        "output": [
            {
                "type": "function_call",
                "name": "close_claim",
                "call_id": "call_1",
                "arguments": '{"verdict":"settled","outcome":"The case ended in a consent decree, not a binary yes or no."}',
            }
        ]
    }
    calls = extract_function_calls(body)
    assert calls[0]["name"] == "close_claim"
    executed = execute_agent_tool(calls[0]["name"], calls[0]["arguments"])
    assert executed["ok"] is True
    assert executed["verdict"] == "settled"
    assert execute_agent_tool("other_tool", {})["ok"] is False


def test_apply_close_claim_marks_outcome_and_confidence():
    before = state(62)
    after = apply_close_claim(
        before,
        {
            "ok": True,
            "verdict": "confirmed",
            "outcome": "Official docs confirm the architecture replacement.",
            "source_url": "https://developer.apple.com/siri",
            "source_title": "Apple",
        },
    )
    assert after.outcome is not None
    assert after.outcome.verdict == "confirmed"
    assert after.confidence == 100
    assert after.evidence_for[0].source_url == "https://developer.apple.com/siri"
    assert is_material_change(before, after) is True
    diff = diff_states(before, after)
    assert any(change.path == "outcome" for change in diff.changes)


def test_watch_closes_claim_and_stops_watching(tmp_path):
    model = ScriptedModel([INIT, WATCH_CLOSE])
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=model)
    living, first = engine.open_claim("Apple is going to replace Siri with an LLM.")
    assert living.refresh().closed is False
    result = engine.watch(living.id)
    assert result.changed is True
    assert result.closed is True
    assert result.commit is not None
    assert result.commit.message == "Claim closed"
    assert result.commit.state.outcome is not None
    assert result.commit.state.outcome.verdict == "confirmed"
    assert result.commit.state.confidence == 100
    record = living.refresh()
    assert record.closed is True
    assert record.watching is False
    assert record.outcome_verdict == "confirmed"
    assert record.head != first.id
    again = engine.watch(living.id)
    assert again.closed is True
    assert again.changed is False
    assert again.detail == "Claim already closed."
    assert engine.watch_due() == []


def test_open_can_close_on_first_survey(tmp_path):
    model = ScriptedModel(
        [
            {
                "text": INIT,
                "close_claim": {
                    "ok": True,
                    "verdict": "falsified",
                    "outcome": "Apple kept classical Siri and cancelled the rewrite.",
                    "source_url": "https://example.com/cancel",
                },
            }
        ]
    )
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=model)
    living, commit = engine.open_claim("Apple is going to replace Siri with an LLM.")
    record = living.refresh()
    assert record.closed is True
    assert record.watching is False
    assert record.outcome_verdict == "falsified"
    assert commit.state.outcome is not None
    assert commit.state.confidence == 0


def test_http_watch_returns_closed(tmp_path):
    from fastapi.testclient import TestClient
    from realitydiff.api import create_app

    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT, WATCH_CLOSE]))
    with TestClient(create_app(engine, start_watcher=False), base_url="http://127.0.0.1:8000") as client:
        created = client.post("/api/claims", json={"statement": "Apple is going to replace Siri with an LLM."})
        claim_id = created.json()["claim"]["id"]
        watched = client.post(f"/api/claims/{claim_id}/watch")
        assert watched.status_code == 200
        body = watched.json()
        assert body["closed"] is True
        assert body["changed"] is True
        listed = client.get("/api/claims").json()
        assert listed[0]["closed"] is True
        assert listed[0]["outcome_verdict"] == "confirmed"
        shown = client.get(f"/api/claims/{claim_id}").json()
        assert shown["claim"]["closed"] is True
        assert shown["head"]["state"]["outcome"]["verdict"] == "confirmed"
        resume = client.post(f"/api/claims/{claim_id}/watching", json={"watching": True})
        assert resume.status_code == 409
        html = client.get("/").text
        assert 'id="outcome-banner"' in html
        assert 'id="closed-chip"' in html


def test_normal_watch_still_commits_without_closing(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT, WATCH_HIT]))
    living, _first = engine.open_claim("Apple is going to replace Siri with an LLM.")
    result = engine.watch(living.id)
    assert result.changed is True
    assert result.closed is False
    assert living.refresh().closed is False
    assert living.refresh().watching is True
