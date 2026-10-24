"""OpenRouter API 동작 테스트: 텍스트 인식과 이미지 인식.

사용법:
    python test_api.py          # 텍스트, 이미지 둘 다
    python test_api.py text     # 텍스트만
    python test_api.py image    # 이미지만
"""

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import get_openrouter_key

API_URL = "https://openrouter.ai/api/v1/chat/completions"
TEXT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
# TEXT_MODEL이 실패하면 OpenRouter가 이 순서대로 대신 시도합니다(models 폴백).
TEXT_FALLBACKS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
]
IMAGE_MODEL = "google/gemma-4-26b-a4b-it:free"
# 이미지 입력을 지원하는 모델만 넣어야 합니다. 1순위와 같은 Google 공용 한도에 함께 걸리지 않도록
# 다른 제공자의 모델로 골랐습니다.
IMAGE_FALLBACKS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "thinkingmachines/inkling:free",
]
SAMPLE_IMAGE = Path(__file__).resolve().parent / "sample.png"


MAX_MODELS = 3  # OpenRouter models 배열 상한(1순위 포함)
RETRY_STATUS = {429, 500, 502, 503}
RETRY_DELAYS = [10, 20, 40]  # 무료 모델의 일시적 한도 초과/장애 대비


def chat(model: str, messages: list, fallbacks: list | None = None) -> dict:
    payload = {"messages": messages}
    if fallbacks:
        if 1 + len(fallbacks) > MAX_MODELS:
            raise ValueError(f"OpenRouter models 배열은 최대 {MAX_MODELS}개입니다(폴백 최대 {MAX_MODELS - 1}개).")
        payload["models"] = [model, *fallbacks]
    else:
        payload["model"] = model
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {get_openrouter_key()}",
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            print(f"  ...{delay}초 후 재시도 ({attempt}/{len(RETRY_DELAYS)})")
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in RETRY_STATUS and attempt < len(RETRY_DELAYS):
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    data["_elapsed"] = time.time() - started
    if "error" in data:
        raise RuntimeError(f"API 오류: {data['error']}")
    return data


def report(title: str, data: dict, requested: str) -> None:
    print(f"\n===== {title} =====")
    print(f"모델: {data.get('model')}  (provider: {data.get('provider', '-')})")
    if data.get("model") != requested:
        print(f"※ {requested} 응답 실패로 폴백 모델이 대신 응답했습니다.")
    print(f"소요 시간: {data['_elapsed']:.1f}초")
    usage = data.get("usage", {})
    print(f"토큰: 입력 {usage.get('prompt_tokens')} / 출력 {usage.get('completion_tokens')}")
    print("--- 응답 ---")
    print(data["choices"][0]["message"].get("content"))


def make_sample_image(path: Path = SAMPLE_IMAGE) -> Path:
    """정답을 알고 있는 테스트 이미지를 만듭니다: 도형 3개와 글자."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((40, 60, 200, 220), fill="red")
    draw.rectangle((240, 60, 400, 220), fill="blue")
    draw.polygon([(520, 60), (440, 220), (600, 220)], fill="green")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((140, 280), "HELLO 2026", fill="black", font=font)
    img.save(path)
    return path


def test_text() -> None:
    messages = [
        {
            "role": "user",
            "content": "다음 문장의 핵심 내용을 한 문장으로 요약하고, 감정(긍정/부정/중립)을 판별해 줘.\n\n"
            "\"새로 산 노트북은 배터리가 오래가고 화면도 선명하지만, 팬 소음이 조금 거슬린다.\"",
        }
    ]
    report(
        f"텍스트 인식 테스트 ({TEXT_MODEL})",
        chat(TEXT_MODEL, messages, fallbacks=TEXT_FALLBACKS),
        TEXT_MODEL,
    )


def test_image() -> None:
    path = SAMPLE_IMAGE if SAMPLE_IMAGE.exists() else make_sample_image()
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "이 이미지에 보이는 도형의 종류와 색, 그리고 적힌 글자를 한국어로 알려 줘.",
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }
    ]
    report(
        f"이미지 인식 테스트 ({IMAGE_MODEL})",
        chat(IMAGE_MODEL, messages, fallbacks=IMAGE_FALLBACKS),
        IMAGE_MODEL,
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    tests = {"text": [test_text], "image": [test_image], "all": [test_text, test_image]}[target]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as e:
            failed += 1
            print(f"\n[실패] {test.__name__}: {e}")
    sys.exit(1 if failed else 0)
