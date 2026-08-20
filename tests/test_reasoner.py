from __future__ import annotations

from typing import Any

from realitydiff.reasoner import Reasoner
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore


class ScriptedModel:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt: str, *, search: bool = False) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "search": search})
        return {"text": self.payloads.pop(0), "citations": [], "model": "scripted"}


INIT = """```json
{
  "statement": "Apple will replace Siri's architecture with an LLM",
  "refined_statement": "Apple will replace Siri's classical NLU/dialog stack with a foundation-model core",
  "confidence": 62,
  "reason": "Leaks and hiring support a rewrite; no official architecture replacement yet.",
  "summary": "Directionally likely, not confirmed.",
  "evidence_for": [
    {
      "statement": "Multiple reports describe a Siri LLM overhaul",
      "source_url": "https://example.com/bloomberg",
      "source_title": "Bloomberg",
      "weight": 0.6,
      "notes": "Secondary reporting"
    }
  ],
  "evidence_against": [
    {
      "statement": "Current Siri still behaves like the classical product",
      "source_url": "https://example.com/ios",
      "source_title": "iOS",
      "weight": 0.5,
      "notes": "Shipping software contradicts a completed replacement"
    }
  ],
  "unknowns": [
    {"question": "Is this a new architecture or an LLM feature bolted on?", "why_it_matters": "The claim is about replacement"}
  ],
  "predictions": [
    {
      "statement": "Apple publishes developer docs for a foundation-model Siri runtime",
      "due": "2026-12",
      "status": "open",
      "how_to_falsify": "Docs never appear and Siri remains classical"
    }
  ]
}
```"""

WATCH_NOOP = """{"changed": false, "reason": "Only duplicate coverage of existing leaks."}"""

WATCH_HIT = """```json
{
  "changed": true,
  "reason": "New evidence directly supports X: Apple developer documentation now describes a foundation-model Siri runtime that replaces the old dialog manager.",
  "statement": "Apple will replace Siri's architecture with an LLM",
  "refined_statement": "Apple will replace Siri's classical NLU/dialog stack with a foundation-model core",
  "confidence": 78,
  "summary": "Primary documentation now corroborates an architecture replacement.",
  "evidence_for": [
    {
      "id": "keep-old",
      "statement": "Multiple reports describe a Siri LLM overhaul",
      "source_url": "https://example.com/bloomberg",
      "source_title": "Bloomberg",
      "weight": 0.6,
      "notes": "Secondary reporting"
    },
    {
      "statement": "Apple documentation describes a foundation-model Siri runtime replacing the dialog manager",
      "source_url": "https://developer.apple.com/documentation/siri/foundation-model",
      "source_title": "Apple Developer",
      "weight": 0.9,
      "notes": "Primary source"
    }
  ],
  "evidence_against": [
    {
      "statement": "Current Siri still behaves like the classical product",
      "source_url": "https://example.com/ios",
      "source_title": "iOS",
      "weight": 0.4,
      "notes": "Still true for the shipping build, weaker against the roadmap"
    }
  ],
  "unknowns": [
    {"question": "When does the replacement ship to all devices?", "why_it_matters": "Architecture vs availability"}
  ],
  "predictions": [
    {
      "statement": "Apple publishes developer docs for a foundation-model Siri runtime",
      "due": "2026-12",
      "status": "confirmed",
      "how_to_falsify": "Docs never appear and Siri remains classical"
    }
  ]
}
```"""


def test_reasoner_initialize_and_watch_noop():
    model = ScriptedModel([INIT, WATCH_NOOP])
    reasoner = Reasoner(model)
    state, reason = reasoner.initialize("Apple is going to replace Siri with an LLM.")
    assert state.confidence == 62
    assert "rewrite" in reason.lower() or "official" in reason.lower()
    updated, detail, changed = reasoner.update("Apple is going to replace Siri with an LLM.", state)
    assert changed is False
    assert updated.tree_hash() == state.tree_hash()
    assert "duplicate" in detail.lower()
    assert model.calls[0]["search"] is True


def test_engine_watch_commits_on_new_docs(tmp_path):
    model = ScriptedModel([INIT, WATCH_HIT])
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=model)
    living, first = engine.open_claim("Apple is going to replace Siri with an LLM.")
    assert first.new_confidence == 62
    result = engine.watch(living.id)
    assert result.changed is True
    assert result.commit is not None
    assert result.commit.new_confidence == 78
    assert result.diff is not None
    assert result.diff.confidence_delta == 16
    assert "documentation" in result.detail.lower()
    assert living.head.parent_id == first.id
