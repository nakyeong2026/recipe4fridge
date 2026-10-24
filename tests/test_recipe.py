import json

import pytest

import recipe

INGREDIENTS = [
    {"name": "달걀", "quantity": 10, "unit": "개"},
    {"name": "토마토", "quantity": 4, "unit": "개"},
]


def make_recipe(**overrides):
    base = {
        "title": "토마토 달걀볶음",
        "summary": "간단한 볶음 요리",
        "time_minutes": "15분",
        "servings": 2,
        "used_ingredients": [{"name": "달걀", "amount": "3개"}, "토마토"],
        "missing_ingredients": [{"name": "식용유", "amount": "1큰술"}],
        "steps": ["달걀을 푼다", "토마토를 썬다", "함께 볶는다"],
    }
    return {**base, **overrides}


def test_build_messages_includes_ingredients_and_options():
    options = {"cuisine": "중식", "difficulty": "중급", "time": "15분 이내", "servings": "1인분"}
    content = recipe.build_messages(INGREDIENTS, options)[0]["content"]
    assert "달걀 10개, 토마토 4개" in content
    for value in options.values():
        assert value in content
    assert "한국어로만" in content


def test_parse_recipes_normalizes_fields():
    text = "```json\n" + json.dumps({"recipes": [make_recipe()]}, ensure_ascii=False) + "\n```"
    [r] = recipe.parse_recipes(text)
    assert r["time_minutes"] == 15
    assert r["used_ingredients"][1] == {"name": "토마토", "amount": ""}
    assert r["missing_ingredients"] == [{"name": "식용유", "amount": "1큰술"}]
    assert r["tips"] == ""


def test_parse_recipes_rejects_english():
    english = make_recipe(title="Tomato Egg Stir-fry", steps=["Beat eggs", "Slice tomatoes", "Stir-fry"])
    with pytest.raises(ValueError):
        recipe.parse_recipes(json.dumps({"recipes": [english]}))


def test_parse_recipes_skips_incomplete_and_requires_one():
    with pytest.raises(ValueError):
        recipe.parse_recipes(json.dumps({"recipes": [{"title": "제목만"}]}, ensure_ascii=False))
