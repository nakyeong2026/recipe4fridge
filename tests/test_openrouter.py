import pytest

import openrouter
from openrouter import AIError, call_with_fallback, classify_http_error


def test_classify_daily_limit_with_reset_time():
    body = {"error": {"message": "Rate limit exceeded: free-models-per-day.",
                      "metadata": {"headers": {"X-RateLimit-Reset": "1761436800000"}}}}
    e = classify_http_error(429, body)
    assert e.kind == "daily_limit"
    assert e.reset_at is not None and e.reset_at.hour == 9


def test_classify_upstream_rate_limit():
    body = {"error": {"message": "Provider returned error",
                      "metadata": {"raw": "temporarily rate-limited upstream"}}}
    assert classify_http_error(429, body).kind == "rate_limit"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (402, "auth"), (403, "forbidden"), (404, "not_found"), (503, "provider")])
def test_classify_other_status(status, kind):
    assert classify_http_error(status, {}).kind == kind


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(openrouter.time, "sleep", lambda _: None)


def fake_chat(script):
    """모델별로 미리 정한 결과(예외 또는 응답)를 차례로 돌려주는 가짜 chat_once."""
    calls = []

    def chat(model, messages):
        calls.append(model)
        outcome = script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, model

    return chat, calls


def test_retries_primary_then_succeeds(monkeypatch, no_sleep):
    chat, calls = fake_chat({"a": [AIError("rate_limit", "429"), "ok"]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    result = call_with_fallback(["a", "b"], [], parse=str.upper)
    assert result.data == "OK" and result.model == "a" and calls == ["a", "a"]
    assert not result.used_fallback


def test_falls_back_after_three_failures(monkeypatch, no_sleep):
    chat, calls = fake_chat({"a": [AIError("rate_limit", "429")] * 3, "b": ["ok"]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    result = call_with_fallback(["a", "b"], [], parse=str)
    assert calls == ["a", "a", "a", "b"] and result.used_fallback


def test_not_found_skips_to_next_model_immediately(monkeypatch, no_sleep):
    chat, calls = fake_chat({"a": [AIError("not_found", "404")], "b": ["ok"]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    call_with_fallback(["a", "b"], [], parse=str)
    assert calls == ["a", "b"]


def test_bad_json_is_retried(monkeypatch, no_sleep):
    def parse(text):
        if text == "bad":
            raise ValueError("JSON 아님")
        return text

    chat, calls = fake_chat({"a": ["bad", "good"]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    assert call_with_fallback(["a"], [], parse=parse).data == "good"


def test_daily_limit_stops_without_fallback(monkeypatch, no_sleep):
    chat, calls = fake_chat({"a": [AIError("daily_limit", "한도")], "b": ["ok"]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    with pytest.raises(AIError) as info:
        call_with_fallback(["a", "b"], [], parse=str)
    assert info.value.kind == "daily_limit" and calls == ["a"]


def test_all_failed_keeps_attempt_log(monkeypatch, no_sleep):
    chat, _ = fake_chat({"a": [AIError("provider", "x")] * 3, "b": [AIError("provider", "y")]})
    monkeypatch.setattr(openrouter, "chat_once", chat)
    with pytest.raises(AIError) as info:
        call_with_fallback(["a", "b"], [], parse=str)
    assert info.value.kind == "all_failed" and len(info.value.attempts) == 4
