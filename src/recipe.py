"""2단계: 식재료와 조건으로 한국어 레시피 생성(프롬프트, 응답 해석)."""

import json
import re
from typing import Callable

from openrouter import AIResult, call_with_fallback
from vision import extract_json

# 1순위는 수업 지정 모델, 나머지는 텍스트 무료 예비 모델입니다.
TEXT_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
]
RECIPE_COUNT = 3
TIMEOUT = 120  # 레시피는 응답이 길어서 인식보다 오래 기다립니다.

CUISINE_OPTIONS = ["한식", "양식", "중식", "일식"]
DIFFICULTY_OPTIONS = ["초급", "중급", "고급"]
TIME_OPTIONS = ["15분 이내", "30분 이내", "30분 이상"]
SERVINGS_OPTIONS = ["1인분", "2인분", "3인분", "4인분"]
DEFAULT_OPTIONS = {"cuisine": "한식", "difficulty": "초급", "time": "30분 이내", "servings": "2인분"}

_HANGUL = re.compile(r"[가-힣]")


def format_ingredients(ingredients: list[dict]) -> str:
    return ", ".join(f"{i['name']} {i['quantity']}{i['unit']}" for i in ingredients)


def build_messages(ingredients: list[dict], options: dict) -> list:
    prompt = f"""냉장고에 있는 재료: {format_ingredients(ingredients)}

조건:
- 요리 종류: {options['cuisine']}
- 난이도: {options['difficulty']}
- 조리 시간: {options['time']}
- 인원: {options['servings']}

위 재료를 최대한 활용하고 조건에 맞는 레시피 {RECIPE_COUNT}개를 추천해 줘.

규칙:
- 제목, 소개, 재료, 조리 순서, 팁까지 모든 내용을 한국어로만 써. 영어 문장을 쓰지 마.
- 냉장고에 있는 재료는 used_ingredients에, 냉장고에 없어서 따로 사야 하는 재료는 missing_ingredients에 나눠 적어.
- 소금, 후추, 식용유 같은 기본 양념도 냉장고 재료에 없으면 missing_ingredients에 적어.
- 설명 없이 아래 형식의 JSON 하나로만 답해.

{{"recipes": [{{"title": "레시피 이름", "summary": "한 줄 소개", "cuisine": "{options['cuisine']}",
"difficulty": "{options['difficulty']}", "time_minutes": 20, "servings": 2,
"used_ingredients": [{{"name": "재료", "amount": "양"}}],
"missing_ingredients": [{{"name": "재료", "amount": "양"}}],
"steps": ["조리 순서"], "tips": "팁"}}]}}"""
    return [{"role": "user", "content": prompt}]


def _ingredient_list(value) -> list[dict]:
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            result.append({"name": str(item["name"]).strip(), "amount": str(item.get("amount", "")).strip()})
        elif isinstance(item, str) and item.strip():
            result.append({"name": item.strip(), "amount": ""})
    return result


def _to_int(value, default: int) -> int:
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else default


def _is_korean(recipe: dict) -> bool:
    """제목과 조리 순서 대부분이 한글이어야 한국어 레시피로 봅니다."""
    texts = [recipe["title"], *recipe["steps"]]
    korean = sum(1 for t in texts if _HANGUL.search(t))
    return korean >= len(texts) * 0.8


def parse_recipes(text: str) -> list[dict]:
    data = extract_json(text)
    items = data.get("recipes")
    if not isinstance(items, list):
        raise ValueError("recipes 목록이 없습니다")
    recipes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        steps = [str(s).strip() for s in item.get("steps") or [] if str(s).strip()]
        if not title or not steps:
            continue
        recipe = {
            "title": title,
            "summary": str(item.get("summary", "")).strip(),
            "cuisine": str(item.get("cuisine", "")).strip(),
            "difficulty": str(item.get("difficulty", "")).strip(),
            "time_minutes": _to_int(item.get("time_minutes"), 0),
            "servings": _to_int(item.get("servings"), 0),
            "used_ingredients": _ingredient_list(item.get("used_ingredients")),
            "missing_ingredients": _ingredient_list(item.get("missing_ingredients")),
            "steps": steps,
            "tips": str(item.get("tips", "") or "").strip(),
        }
        if not _is_korean(recipe):
            raise ValueError("한국어가 아닌 레시피가 섞여 있습니다")
        recipes.append(recipe)
    if not recipes:
        raise ValueError("쓸 수 있는 레시피가 없습니다")
    return recipes


def generate(
    ingredients: list[dict],
    options: dict,
    on_status: Callable[[str], None] = lambda _: None,
    models: list[str] | None = None,
) -> AIResult:
    return call_with_fallback(
        models or TEXT_MODELS, build_messages(ingredients, options), parse_recipes, on_status, timeout=TIMEOUT
    )


def to_json(recipe: dict) -> str:
    return json.dumps(recipe, ensure_ascii=False)
