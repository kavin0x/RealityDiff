from realitydiff.compress import compact_belief, is_material_change
from realitydiff.models import Evidence
from tests.test_git_beliefs import state


def test_cosmetic_rewrite_is_not_material():
    before = state(62)
    after = before.model_copy(update={"summary": "Same facts, prettier prose."})
    assert is_material_change(before, after) is False
    assert compact_belief(before)["confidence"] == 62


def test_new_url_is_material():
    before = state(62)
    extra = Evidence.make(
        "New filing",
        source_url="https://example.com/sec",
        source_title="SEC",
        weight=0.8,
    )
    after = state(62, extra_for=[extra])
    assert is_material_change(before, after) is True
