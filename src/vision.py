"""1단계: 냉장고 사진에서 식재료 인식(사진 축소, 프롬프트, 응답 해석, 중복 병합)."""

import base64
import io
import json
import re
from typing import Callable

from PIL import Image, ImageOps

from openrouter import AIResult, call_with_fallback

# 1순위는 수업 지정 모델, 나머지는 이미지 입력을 지원하는 다른 제공자의 무료 모델입니다.
IMAGE_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
]
MAX_SIDE = 1024
JPEG_QUALITY = 85

CATEGORIES = ["채소", "과일", "육류", "해산물", "유제품/달걀", "양념/소스", "음료", "가공식품", "기타"]
UNITS = ["개", "병", "봉지", "팩", "단", "통", "g", "kg", "ml", "L", "기타"]

PROMPT = f"""이 냉장고 사진에서 먹을 수 있는 식재료를 모두 찾아 줘.

규칙:
- 식품만 고르고, 용기·그릇·비닐·선반 같은 비식품은 빼.
- 달걀, 과일, 채소처럼 낱개로 셀 수 있는 것은 실제로 보이는 개수를 세어 quantity에 적어.
- 이름은 한국어로 써.
- unit은 {", ".join(UNITS)} 가운데 하나로 써.
- category는 {", ".join(CATEGORIES)} 가운데 하나로 써.
- 설명 없이 아래 형식의 JSON 하나로만 답해.

{{"ingredients": [{{"name": "달걀", "quantity": 6, "unit": "개", "category": "유제품/달걀"}}]}}"""

_HANGUL = re.compile(r"[가-힣]")
_LATIN_RUN = re.compile(r"[A-Za-z]{3,}")


def prepare_image(raw: bytes) -> tuple[bytes, dict]:
    """회전을 바로잡고, 긴 변을 MAX_SIDE 이하로 줄여 JPEG로 다시 압축합니다."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    original_size = img.size
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = out.getvalue()
    info = {
        "original_bytes": len(raw),
        "original_size": original_size,
        "bytes": len(data),
        "size": img.size,
    }
    return data, info


def build_messages(jpeg: bytes) -> list:
    b64 = base64.b64encode(jpeg).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]


def extract_json(text: str) -> dict:
    """코드 블록이나 앞뒤 설명이 붙은 응답에서 JSON 객체 하나를 꺼냅니다."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("JSON을 찾을 수 없습니다")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 해석 실패: {e.msg}") from None
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return data


def _clean_name(value) -> str | None:
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not name or len(name) > 30:
        return None
    # "달strand"처럼 한글과 영문이 섞여 깨진 이름은 버립니다.
    if _HANGUL.search(name) and _LATIN_RUN.search(name):
        return None
    return name


def _clean_quantity(value) -> int:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return 1
    return min(max(number, 1), 9999)


def parse_ingredients(text: str) -> list[dict]:
    data = extract_json(text)
    items = data.get("ingredients")
    if not isinstance(items, list):
        raise ValueError("ingredients 목록이 없습니다")
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"))
        if not name:
            continue
        unit = item.get("unit") if item.get("unit") in UNITS else "개"
        category = item.get("category") if item.get("category") in CATEGORIES else "기타"
        result.append(
            {"name": name, "quantity": _clean_quantity(item.get("quantity")), "unit": unit, "category": category}
        )
    if items and not result:
        raise ValueError("쓸 수 있는 식재료 이름이 없습니다")
    return merge_duplicates(result)


def merge_duplicates(items: list[dict]) -> list[dict]:
    """이름(공백 무시)과 단위가 같은 재료를 하나로 합치고 수량을 더합니다."""
    merged: dict[tuple[str, str], dict] = {}
    for item in items:
        key = (item["name"].replace(" ", ""), item["unit"])
        if key in merged:
            merged[key]["quantity"] += item["quantity"]
        else:
            merged[key] = dict(item)
    return list(merged.values())


def recognize(
    jpeg: bytes, on_status: Callable[[str], None] = lambda _: None, models: list[str] | None = None
) -> AIResult:
    return call_with_fallback(models or IMAGE_MODELS, build_messages(jpeg), parse_ingredients, on_status)
