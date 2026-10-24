"""환경 변수(.env) 로더.

외부 패키지 없이 .env를 읽어 os.environ에 채우고, OpenRouter API 키를 꺼내 쓰는 함수를 제공합니다.
이미 시스템 환경 변수로 설정된 값은 덮어쓰지 않습니다.
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"  # 프로젝트 루트의 .env


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


def get_openrouter_key() -> str:
    load_env()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY가 없습니다. .env.example을 참고해 .env에 키를 넣어 주세요."
        )
    return key


def mask(secret: str) -> str:
    """로그에 남길 때 앞뒤 일부만 보여 주는 마스킹 문자열."""
    if len(secret) <= 12:
        return "*" * len(secret)
    return f"{secret[:8]}...{secret[-4:]}"
