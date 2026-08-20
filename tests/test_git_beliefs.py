from __future__ import annotations

from datetime import datetime, timezone

from realitydiff.models import ClaimState, Commit, Evidence, Prediction, Unknown
from realitydiff.store import BeliefStore


def ts(n: int) -> datetime:
    return datetime(2026, 8, 20, 12, n, tzinfo=timezone.utc)


def state(confidence: float, extra_for: list[Evidence] | None = None) -> ClaimState:
    return ClaimState(
        statement="Apple will replace Siri's architecture with an LLM",
        refined_statement="Apple will ship a foundation-model core that replaces Siri's classical NLU stack",
        confidence=confidence,
        summary="Living belief",
        evidence_for=[
            Evidence.make(
                "Apple hired models researchers for a Siri rewrite",
                source_url="https://example.com/hiring",
                source_title="Hiring",
                weight=0.4,
            ),
            *(extra_for or []),
        ],
        evidence_against=[
            Evidence.make(
                "Siri still ships as a classical assistant on current iOS",
                source_url="https://example.com/ios",
                source_title="iOS",
                weight=0.5,
            )
        ],
        unknowns=[Unknown.make("Will Apple keep the Siri brand?", "Naming vs architecture")],
        predictions=[
            Prediction.make("WWDC keynote describes a new Siri architecture", due="2027-06")
        ],
    )


def test_commit_diff_blame_revert(tmp_path):
    store = BeliefStore(tmp_path / "beliefs.sqlite")
    record = store.create_claim("Apple is going to replace Siri with an LLM.")
    s1 = state(62)
    c1 = Commit.create(
        claim_id=record.id,
        parent_id=None,
        state=s1,
        author="reasoner",
        message="Initial belief",
        reason="Mixed rumors, no official architecture replacement.",
        previous_confidence=None,
        created_at=ts(1),
    )
    store.append_commit(c1)

    from realitydiff.claim import Claim

    living = Claim(store, store.get_claim(record.id))
    apple_doc = Evidence.make(
        "New Apple documentation describes a foundation-model Siri runtime replacing the old dialog manager",
        source_url="https://developer.apple.com/documentation/siri/foundation-model",
        source_title="Apple Developer Documentation",
        weight=0.85,
        notes="Direct primary-source support for an architecture replacement.",
    )
    s2 = state(78, extra_for=[apple_doc])
    store.set_working_state(record.id, s2)
    c2 = living.commit("Watch update", author="watcher", reason="New evidence directly supports an architecture replacement.")

    assert c2.parent_id == c1.id
    assert c2.new_confidence == 78
    assert c1.new_confidence == 62

    diff = living.diff(c1.id, c2.id)
    assert diff.previous_confidence == 62
    assert diff.new_confidence == 78
    assert diff.confidence_delta == 16
    paths = {change.path for change in diff.changes}
    assert "confidence" in paths
    assert any(path.startswith("evidence_for/") for path in paths if "added" in [
        ch.kind for ch in diff.changes if ch.path == path
    ])
    added = [ch for ch in diff.changes if ch.kind == "added"]
    assert any("foundation-model" in str(ch.after) for ch in added)

    blame = living.blame()
    conf = next(entry for entry in blame.entries if entry.path == "confidence")
    assert conf.introduced_in == c1.id
    assert conf.last_changed_in == c2.id
    doc_entry = next(entry for entry in blame.entries if entry.item_id == apple_doc.id)
    assert doc_entry.introduced_in == c2.id

    c3 = living.revert(c1.id)
    assert c3.parent_id == c2.id
    assert living.head.state.confidence == 62
    assert living.head.state.tree_hash() == c1.state.tree_hash()
    assert living.head.id != c1.id

    log = living.log()
    assert [c.id for c in log] == [c3.id, c2.id, c1.id]
