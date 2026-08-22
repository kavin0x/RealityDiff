from realitydiff.xai import _build_payload, _request_plans


def test_cache_plans_prefer_previous_response_then_fallback():
    plans = _request_plans(previous_response_id="resp_1", compaction={"encrypted_content": "blob"})
    assert plans[0]["previous_response_id"] == "resp_1"
    assert plans[0]["store"] is True
    assert plans[-1]["store"] is False
    assert plans[-1]["previous_response_id"] is None


def test_payload_keeps_system_prefix_for_cache():
    payload = _build_payload(
        model="grok-4.3",
        prompt="CLAIM: x",
        system="You are Reality Diff",
        search=True,
        cache_key="realitydiff-init-v2",
        previous_response_id=None,
        compaction=None,
        store=True,
    )
    assert payload["model"] == "grok-4.3"
    assert payload["prompt_cache_key"] == "realitydiff-init-v2"
    assert payload["input"][0]["role"] == "system"
    assert payload["input"][1]["role"] == "user"
    assert payload["tools"] == [{"type": "web_search"}, {"type": "x_search"}]
