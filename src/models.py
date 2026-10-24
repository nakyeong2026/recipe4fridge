"""OpenRouter 무료 모델 목록과, 사용자가 고른 모델로 시도 순서 만들기."""

import json
import urllib.request
from datetime import datetime, timezone

MODELS_URL = "https://openrouter.ai/api/v1/models"
# 1순위 3번 + 예비 모델마다 1번씩 시도하므로, 하루 50회 무료 한도를 생각해 순서를 4개로 제한합니다.
MAX_CHAIN = 4


def is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    return str(pricing.get("prompt")) == "0" and str(pricing.get("completion")) == "0"


def summarize(raw_models: list[dict]) -> list[dict]:
    """입력·출력 가격이 모두 0인 텍스트 출력 모델만 골라 화면에 필요한 값만 남깁니다."""
    result = []
    for m in raw_models:
        arch = m.get("architecture") or {}
        if not is_free(m) or "text" not in (arch.get("output_modalities") or ["text"]):
            continue
        created = m.get("created")
        result.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "context": m.get("context_length") or 0,
            "created": datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d") if created else "",
            "vision": "image" in (arch.get("input_modalities") or []),
        })
    return sorted(result, key=lambda x: x["created"], reverse=True)


def fetch_free_models(timeout: int = 20) -> list[dict]:
    with urllib.request.urlopen(MODELS_URL, timeout=timeout) as resp:
        return summarize(json.load(resp).get("data", []))


def build_chain(selected: list[str] | None, defaults: list[str]) -> list[str]:
    """고른 모델이 없으면 기본 순서를, 있으면 기본 순서에 든 것을 먼저 두고 나머지를 이어 붙입니다."""
    if not selected:
        return list(defaults)
    chosen = set(selected)
    chain = [m for m in defaults if m in chosen] + [m for m in selected if m not in defaults]
    return chain[:MAX_CHAIN]
