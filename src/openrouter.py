"""OpenRouter 호출, 재시도, 예비 모델 전환.

화면(Streamlit)과 분리된 순수 모듈입니다. 진행 상황은 on_status 콜백으로 알립니다.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from settings import get_secret

API_URL = "https://openrouter.ai/api/v1/chat/completions"
KST = timezone(timedelta(hours=9))
REQUEST_TIMEOUT = 60
RETRY_WAITS = [2, 4]  # 1→2차, 2→3차 시도 사이 대기(초)

# 이 종류의 오류는 다른 모델로 바꿔도 소용없으므로 바로 멈춥니다.
FATAL_KINDS = {"daily_limit", "auth", "config"}
# 이 종류의 오류는 같은 모델로 다시 시도해도 소용없으므로 다음 모델로 넘어갑니다.
SKIP_MODEL_KINDS = {"not_found", "forbidden", "bad_request", "timeout"}


class AIError(Exception):
    def __init__(self, kind: str, message: str, reset_at: datetime | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.reset_at = reset_at
        self.attempts: list[Attempt] = []


@dataclass
class Attempt:
    model: str
    number: int
    ok: bool
    error: str = ""


@dataclass
class AIResult:
    data: Any
    model: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def used_fallback(self) -> bool:
        return bool(self.attempts) and self.attempts[0].model != self.model


def _reset_time(value: Any) -> datetime | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=KST)


def classify_http_error(status: int, body: dict, headers: dict | None = None) -> AIError:
    """HTTP 오류 응답을 원인별로 나눕니다."""
    error = body.get("error", {}) if isinstance(body, dict) else {}
    message = str(error.get("message", ""))
    meta = error.get("metadata") or {}
    raw = str(meta.get("raw", ""))
    if status == 429:
        limit_headers = {**(headers or {}), **(meta.get("headers") or {})}
        if "per-day" in message or "per-day" in raw:
            reset = _reset_time(limit_headers.get("X-RateLimit-Reset"))
            return AIError("daily_limit", "오늘 쓸 수 있는 무료 호출 횟수를 모두 사용했습니다.", reset)
        return AIError("rate_limit", "모델이 잠시 혼잡합니다(요청 제한 429).")
    if status == 401:
        return AIError("auth", "API 키가 올바르지 않습니다(401).")
    if status == 402:
        return AIError("auth", "크레딧이 없어 이 모델을 쓸 수 없습니다(402).")
    if status == 403:
        return AIError("forbidden", "이 모델은 지금 계정으로 쓸 수 없습니다(403).")
    if status == 404:
        return AIError("not_found", "모델 제공자에 배포된 모델이 없습니다(404).")
    if status in (400, 413):
        return AIError("bad_request", f"요청이 거절되었습니다({status}): {message[:120]}")
    return AIError("provider", f"모델 제공자 오류({status}): {message[:120]}")


def chat_once(model: str, messages: list, timeout: int = REQUEST_TIMEOUT) -> tuple[str, str]:
    """모델을 한 번 호출해서 (응답 본문, 실제 응답한 모델)을 돌려줍니다."""
    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        raise AIError("config", "OPENROUTER_API_KEY가 설정되지 않았습니다(.env 또는 Streamlit Secrets).")
    req = urllib.request.Request(
        API_URL,
        data=json.dumps({"model": model, "messages": messages}).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    # OpenRouter는 처리 중에 연결 유지용 공백을 보내므로, 소켓 timeout만으로는 끝나지 않습니다.
    # 조금씩 읽으면서 전체 경과 시간으로 끊습니다.
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            chunks = []
            while chunk := resp.read1(65536):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise AIError("timeout", f"{timeout}초 안에 응답이 끝나지 않았습니다.")
            data = json.loads(b"".join(chunks))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", errors="replace"))
        except ValueError:
            body = {}
        raise classify_http_error(e.code, body, dict(e.headers or {})) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("network", f"응답 시간 초과 또는 네트워크 오류: {e}") from None
    except ValueError:
        raise AIError("bad_response", "응답 본문이 JSON이 아닙니다.") from None

    # OpenRouter는 HTTP 200 안에 오류를 담아 보내기도 합니다.
    if isinstance(data.get("error"), dict):
        code = data["error"].get("code")
        raise classify_http_error(code if isinstance(code, int) else 502, data)
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content:
        raise AIError("bad_response", "모델이 빈 응답을 보냈습니다(choices 없음).")
    return content, data.get("model", model)


def call_with_fallback(
    models: list[str],
    messages: list,
    parse: Callable[[str], Any],
    on_status: Callable[[str], None] = lambda _: None,
    primary_attempts: int = 3,
    fallback_attempts: int = 1,
) -> AIResult:
    """1순위 모델을 최대 primary_attempts번 시도하고, 실패하면 예비 모델로 넘어갑니다.

    parse는 응답 본문을 해석하고, 쓸 수 없는 응답이면 ValueError를 냅니다.
    """
    attempts: list[Attempt] = []
    for index, model in enumerate(models):
        tries = primary_attempts if index == 0 else fallback_attempts
        label = "1순위 모델" if index == 0 else f"예비 모델 {index}"
        for number in range(1, tries + 1):
            on_status(f"{label} {number}차 시도: {model}")
            try:
                content, actual = chat_once(model, messages)
                data = parse(content)
            except AIError as e:
                attempts.append(Attempt(model, number, False, e.message))
                if e.kind in FATAL_KINDS:
                    e.attempts = attempts
                    raise
                if e.kind in SKIP_MODEL_KINDS:
                    break
            except ValueError as e:
                attempts.append(Attempt(model, number, False, f"응답 형식 오류: {e}"))
            else:
                attempts.append(Attempt(model, number, True))
                return AIResult(data, actual, attempts)
            if number < tries:
                time.sleep(RETRY_WAITS[min(number - 1, len(RETRY_WAITS) - 1)])
    error = AIError("all_failed", f"모델 {len(models)}개에 모두 실패했습니다.")
    error.attempts = attempts
    raise error


def describe_error(e: AIError) -> str:
    """화면에 보여 줄 실패 안내 문구."""
    if e.kind == "daily_limit":
        when = e.reset_at.strftime("%m월 %d일 %H시 %M분") if e.reset_at else "한국 시각 오전 9시"
        return f"{e.message} {when}에 초기화되며, 그때까지는 다시 시도해도 같은 오류가 납니다."
    if e.kind == "all_failed":
        return f"{e.message} 무료 모델이 혼잡할 수 있으니 1~2분 뒤 다시 시도해 주세요."
    return e.message
