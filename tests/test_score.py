from realitydiff.models import Evidence
from realitydiff.score import normalize_kind, score_evidence


def item(kind: str, entity: str = "", statement: str = "fact") -> Evidence:
    slug = f"{kind}-{entity}-{statement}".replace(" ", "-")
    return Evidence.make(
        statement,
        source_url=f"https://example.com/{slug}",
        kind=kind,
        entity=entity,
    )


def test_no_evidence_stays_at_the_prior():
    card = score_evidence([], [])
    assert card.confidence == 50
    assert card.steps == []
    assert card.caps == []


def test_one_rumor_is_56():
    card = score_evidence([item("rumor")], [])
    assert card.confidence == 56
    assert card.steps[0].points == 6
    assert card.steps[0].label == "rumor"


def test_sibling_cadence_only_is_68():
    card = score_evidence([item("sibling_cadence", "lab")], [])
    assert card.confidence == 68


def test_primary_support_only_is_72():
    card = score_evidence([item("primary_support")], [])
    assert card.confidence == 72


def test_official_confirmation_clears_85_without_adjustment():
    card = score_evidence([item("official_confirmation", "apple")], [])
    assert card.confidence == 90
    assert card.adjustment == 0
    assert card.caps == []


def test_adjustment_cannot_break_the_85_cap():
    support = [
        item("primary_support", "a"),
        item("primary_support", "b"),
        item("primary_support", "c"),
    ]
    card = score_evidence(support, [], adjustment=100)
    assert card.adjustment == 8
    assert card.confidence == 85
    assert card.caps == ["cap 85"]


def test_rumor_plus_adjustment_stays_under_65():
    card = score_evidence([item("rumor")], [], adjustment=8, adjustment_why="Market lean.")
    assert card.confidence == 64
    assert card.adjustment_why == "Market lean."
    assert card.caps == []


def test_official_denial_lands_at_5():
    card = score_evidence([], [item("official_denial", "apple")])
    assert card.confidence == 5
    assert card.steps[0].points == -45


def test_same_entity_counts_once():
    support = [
        item("rumor", "CNN", "edition"),
        item("reputable_report", "cnn", "parent"),
    ]
    card = score_evidence(support, [])
    assert card.confidence == 62
    assert len(card.steps) == 1
    assert card.steps[0].kind == "reputable_report"


def test_missing_entity_does_not_merge():
    card = score_evidence([item("rumor", "", "one"), item("rumor", "", "two")], [])
    assert card.confidence == 59
    assert [step.points for step in card.steps] == [6, 3]


def test_absence_does_not_score():
    card = score_evidence([item("absence", "apple")], [item("absence", "wire")])
    assert card.confidence == 50
    assert card.steps == []


def test_sibling_floor_when_only_a_weak_objection():
    card = score_evidence(
        [item("sibling_cadence", "lab")],
        [item("rumor_against", "forum")],
        adjustment=-8,
    )
    assert card.confidence == 60
    assert card.caps == ["floor 60"]


def test_reputable_contradiction_blocks_the_sibling_floor():
    card = score_evidence(
        [item("sibling_cadence", "lab")],
        [item("reputable_contradiction", "wire")],
    )
    assert card.confidence == 56
    assert card.caps == []


def test_denial_ceiling_holds_when_support_is_added():
    card = score_evidence(
        [item("reputable_report", "wire")],
        [item("official_denial", "apple")],
    )
    assert card.confidence == 15
    assert card.caps == ["cap 15"]


def test_omitted_kind_cannot_skip_the_rumor_cap():
    assert normalize_kind("", side="for") == "rumor"
    assert normalize_kind("not a kind", side="against") == "rumor_against"
    assert normalize_kind("absence", side="against") is None
    bare = Evidence.make("Someone tweeted it", source_url="https://example.com/tweet")
    card = score_evidence([bare], [], adjustment=8)
    assert card.confidence == 64
