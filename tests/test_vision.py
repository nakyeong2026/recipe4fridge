import io

import pytest
from PIL import Image

import vision


def test_extract_json_handles_code_fence_and_text():
    text = '설명입니다.\n```json\n{"ingredients": []}\n```\n끝'
    assert vision.extract_json(text) == {"ingredients": []}


def test_extract_json_rejects_non_json():
    with pytest.raises(ValueError):
        vision.extract_json("JSON이 아닙니다")


def test_parse_ingredients_cleans_values():
    text = """{"ingredients": [
        {"name": " 달걀 ", "quantity": "6", "unit": "개", "category": "유제품/달걀"},
        {"name": "우유", "quantity": 0, "unit": "통통", "category": "음료수"},
        {"name": "달strand", "quantity": 2, "unit": "개", "category": "기타"},
        {"name": "", "quantity": 1}
    ]}"""
    items = vision.parse_ingredients(text)
    assert items == [
        {"name": "달걀", "quantity": 6, "unit": "개", "category": "유제품/달걀"},
        {"name": "우유", "quantity": 1, "unit": "개", "category": "기타"},
    ]


def test_parse_ingredients_rejects_all_garbage():
    with pytest.raises(ValueError):
        vision.parse_ingredients('{"ingredients": [{"name": "달strand"}]}')


def test_parse_ingredients_allows_empty_list():
    assert vision.parse_ingredients('{"ingredients": []}') == []


def test_merge_duplicates_sums_same_name_and_unit():
    items = [
        {"name": "대파", "quantity": 1, "unit": "단", "category": "채소"},
        {"name": "대 파", "quantity": 2, "unit": "단", "category": "채소"},
        {"name": "대파", "quantity": 100, "unit": "g", "category": "채소"},
    ]
    merged = vision.merge_duplicates(items)
    assert [(m["quantity"], m["unit"]) for m in merged] == [(3, "단"), (100, "g")]


def test_prepare_image_shrinks_and_converts_to_jpeg():
    buf = io.BytesIO()
    Image.new("RGBA", (3000, 1500), (255, 0, 0, 255)).save(buf, format="PNG")
    data, info = vision.prepare_image(buf.getvalue())
    assert info["original_size"] == (3000, 1500)
    assert max(info["size"]) == vision.MAX_SIDE
    assert Image.open(io.BytesIO(data)).format == "JPEG"
