from realitydiff.xai import parse_json_object, XAIError
from realitydiff.reasoner import _coerce_number
from realitydiff.service import RealityDiff, WatchBusy
from realitydiff.store import BeliefStore
from tests.test_git_beliefs import state, ts
from realitydiff.models import Commit


def test_parse_json_repairs_fence_and_trailing_comma():
    data = parse_json_object('Sure.\n```json\n{"confidence": 62, "ok": true,}\n```\n')
    assert data["confidence"] == 62
    assert data["ok"] is True


def test_parse_json_picks_object_from_prose():
    data = parse_json_object('noise {"statement": "x", "confidence": 40} trailing')
    assert data["statement"] == "x"


def test_parse_json_empty():
    try:
        parse_json_object("   ")
        assert False
    except XAIError:
        pass


def test_coerce_percent_strings():
    assert _coerce_number("78%", default=50) == 78
    assert _coerce_number("nope", default=50) == 50
    assert _coerce_number(0.9, default=0.5, lo=0, hi=1) == 0.9


def test_short_sha_and_open_rollback(tmp_path):
    store = BeliefStore(tmp_path / "db.sqlite")
    record = store.create_claim("Apple is going to replace Siri with an LLM.")
    c1 = Commit.create(
        claim_id=record.id,
        parent_id=None,
        state=state(62),
        author="reasoner",
        message="Initial belief",
        reason="mix",
        created_at=ts(1),
    )
    store.append_commit(c1)
    found = store.get_commit(c1.id[:10], claim_id=record.id)
    assert found is not None and found.id == c1.id

    class Boom:
        def complete(self, prompt, *, search=False):
            raise RuntimeError("nope")

    engine = RealityDiff(BeliefStore(tmp_path / "db2.sqlite"), model=Boom())
    try:
        engine.open_claim("This claim should not persist.")
        assert False
    except RuntimeError:
        pass
    assert engine.store.list_claims() == []


def test_watch_busy(tmp_path):
    store = BeliefStore(tmp_path / "db.sqlite")
    engine = RealityDiff(store, model=object())
    lock = engine._lock_for("cl_test")
    assert lock.acquire(blocking=False)
    try:
        try:
            engine.watch("cl_test")
            assert False
        except WatchBusy:
            pass
    finally:
        lock.release()
