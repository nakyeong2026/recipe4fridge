# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

OpenRouter API로 냉장고 사진에서 식재료를 인식하고 레시피를 추천하는 Streamlit 앱을 만드는 학습 프로젝트입니다. 사용자와의 대화, 코드 주석, 출력 메시지는 모두 한국어로 씁니다.

- `docs/`: 단계별 요구 사항 `PRD_step1.md`(식재료 인식), `PRD_step2.md`(레시피 추천), `PRD_step3.md`(프로필·저장). 구현은 이 문서를 기준으로 합니다.
- `api_test/`: 앱 구현 전에 두 모델의 API 연결을 시험한 코드입니다.

- Python 3.12. HTTP 요청은 표준 라이브러리 `urllib`만 사용합니다(`requests`, `python-dotenv`는 설치되어 있지 않음).
- 외부 패키지는 테스트 이미지를 만들 때 쓰는 Pillow 하나뿐입니다. 의존성 파일(requirements 등)은 아직 없습니다.

## 명령어

앱과 시험은 프로젝트의 가상 환경(`.venv`)으로 실행합니다. 배포 환경(Streamlit Community Cloud)은 `requirements.txt`를 읽습니다.

```bash
.venv/Scripts/python -m streamlit run src/app.py        # 앱 실행 (http://localhost:8501)
.venv/Scripts/python -m pytest -q                       # 자동 시험 전체 (pytest.ini가 src를 import 경로에 넣음)
.venv/Scripts/python -m pytest tests/test_vision.py -k merge   # 시험 하나만
```

내장 브라우저로 앱을 시험할 때는 `.claude/launch.json`의 `recipe-app` 설정으로 띄웁니다. 이 설정은 정적 파일 제공을 켜 두므로, `src/static/`(git 제외)에 둔 시험 사진을 `/app/static/파일명`에서 fetch해서 DataTransfer로 업로드 칸에 넣을 수 있습니다. 시험 사진 원본은 `test_photos/`(git 제외)에 있습니다. 무료 호출은 하루 50회이므로 실제 AI 호출 시험은 아껴 씁니다.

API 연결만 따로 시험하는 스크립트:

```bash
python api_test/test_api.py          # 텍스트·이미지 테스트 모두 실행
python api_test/test_api.py text     # 텍스트 인식만
python api_test/test_api.py image    # 이미지 인식만
```

테스트가 하나라도 실패하면 종료 코드가 1입니다. lint, 빌드, 단위 테스트 프레임워크는 없습니다.

## 구조

### 앱(`src/`)
화면 코드와 AI 호출 로직을 분리해, 로직은 화면 없이 pytest로 시험합니다. Streamlit은 실행한 스크립트의 폴더(`src/`)를 import 경로에 넣으므로 모듈끼리 `import vision`처럼 가져옵니다.

- `settings.py`: `get_secret()`이 환경 변수 → 루트 `.env` → `st.secrets` 순서로 값을 찾습니다. 로컬과 배포 환경에서 같은 코드가 동작하게 하는 지점입니다.
- `openrouter.py`: 모든 AI 호출이 지나가는 곳입니다.
  - `call_with_fallback()`은 1순위 모델을 최대 3번(2초, 4초 대기) 시도한 뒤 예비 모델로 넘어갑니다. 서버 쪽 `models` 폴백이 아니라 앱이 직접 순서대로 호출하는 방식이라서, 시도마다 `on_status`로 진행 상황을 화면에 보여 줄 수 있습니다.
  - 오류는 `AIError.kind`로 나뉩니다. `daily_limit`·`auth`·`config`는 바로 멈추고(무료 일일 한도는 모든 무료 모델이 함께 쓰므로 다른 모델로 바꿔도 소용없음), `not_found`·`forbidden`·`bad_request`·`timeout`은 같은 모델을 다시 시도하지 않고 다음 모델로 넘어갑니다.
  - `parse`가 `ValueError`를 내면(빈 응답, 깨진 JSON) 실패로 보고 다시 시도합니다.
  - OpenRouter는 처리 중에 연결 유지용 공백을 보내서 소켓 timeout이 끝나지 않으므로, `chat_once()`가 `read1()`로 조금씩 읽으면서 전체 경과 시간(60초)으로 끊습니다.
- `vision.py`(1단계): 사진 축소(긴 변 1024px, JPEG), 프롬프트, JSON 추출, 이름 정리(한글과 영문이 섞여 깨진 이름 제거), 같은 이름·단위 병합을 담당합니다. 모델 순서는 `IMAGE_MODELS`에 있습니다.
- `app.py`: 단계(`st.session_state.step`) 단위의 마법사 화면입니다. 식재료 행은 고유 `_id`를 위젯 key로 써서 중간 행을 지워도 다른 행이 지워지지 않습니다. 실패하면 이전 결과를 먼저 지우고 실패를 `session_state`에 저장해서 다음 실행 때 그려 줍니다(`st.rerun()`으로 오류 문구가 사라지지 않게 하려는 것).
- 각 단계의 요구 사항은 `docs/PRD_step*.md`에 있습니다. 단계를 마칠 때마다 `screenshot/<태그>/`에 화면 캡처를 남기고, `PRD_01_01`처럼 태그를 붙여 커밋합니다.

### API 시험 코드(`api_test/`)
- `api_test/config.py`: 프로젝트 루트의 `.env`를 직접 파싱해서 `os.environ.setdefault`로 채웁니다. 시스템 환경 변수에 이미 있는 값이 `.env`보다 우선합니다. API 키는 항상 `get_openrouter_key()`로 가져오고, 로그에 남겨야 하면 `mask()`를 씁니다.
- `api_test/test_api.py`: 공통 요청 함수 `chat()`가 OpenRouter `/api/v1/chat/completions`를 호출합니다. 429와 5xx 오류는 10초, 20초, 40초 간격으로 재시도하고, 404 같은 그 밖의 오류는 바로 `RuntimeError`로 올립니다. 모델 ID는 파일 위쪽 상수에 있습니다.
  - 텍스트 모델 `TEXT_MODEL` = `nvidia/nemotron-3-super-120b-a12b:free`, 대체 모델은 `TEXT_FALLBACKS`.
  - 이미지 모델 `IMAGE_MODEL` = `google/gemma-4-26b-a4b-it:free`, 대체 모델은 `IMAGE_FALLBACKS`. 이미지는 base64 data URL로 첨부합니다. 대체 모델도 반드시 이미지 입력을 지원해야 하고, 1순위와 같은 Google 공용 한도에 함께 걸리지 않도록 다른 제공자의 모델로 골랐습니다.
  - 폴백 동작: `chat()`에 `fallbacks`를 넘기면 요청이 `model` 대신 `models` 배열로 나가고, 1순위 모델이 실패하면 OpenRouter가 서버 쪽에서 대체 모델을 차례로 시도합니다. **`models` 배열은 1순위를 포함해 최대 3개**이고(넘으면 HTTP 400), `chat()`이 요청 전에 개수를 확인합니다. 모든 모델이 실패했을 때만 위의 재시도 로직이 동작합니다. 실제로 응답한 모델은 응답의 `model` 필드에 있고, `report()`는 그 값이 요청 모델과 다르면 폴백되었다고 표시합니다.
  - 폴백을 강제로 검증하려면 1순위를 다운된 모델로 임시 지정합니다(`api_test/`에서 실행): `python -c "import test_api as t; t.IMAGE_MODEL='nvidia/nemotron-3-super-120b-a12b:free'; t.test_image()"`
- `api_test/sample.png`: 정답을 미리 아는 테스트 이미지입니다(빨간 원, 파란 사각형, 초록 삼각형, 글자 "HELLO 2026"). 파일이 없으면 `make_sample_image()`가 다시 만듭니다. 그림을 바꾸면 이미지 테스트의 정답도 같이 바뀝니다.

## API 키 다루기

- `.env`에는 `OPENROUTER_API_KEY` 한 줄만 있습니다. 내용을 확인할 때도 값은 출력하지 말고 `sed -E 's/=.*/=<redacted>/' .env`처럼 가려서 보세요.
- `.gitignore`가 `.env`와 `.env.*`를 제외하고 `.env.example`만 허용합니다. 커밋하기 전에는 `git grep --cached -nE 'sk-or-v1-[0-9a-f]{20,}'`로 스테이징된 내용에 실제 키가 섞이지 않았는지 확인하세요.

## 실패 원인 진단

무료(`:free`) 모델은 업스트림 제공자 쪽 문제로 자주 실패합니다. 키를 의심하기 전에 아래 두 가지로 원인을 나눠 보세요. 둘 다 크레딧을 쓰지 않습니다.

- 키 유효성: `GET https://openrouter.ai/api/v1/key` → HTTP 200이면 키는 정상입니다.
- 모델 제공자 상태: `GET https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints` → 응답의 `status`, `uptime_last_30m`을 봅니다.

자주 보는 오류는 이렇습니다.
- **HTTP 429** (`upstream_provider_shared_pool`): 무료 사용자 공용 한도가 찬 것입니다. 시간이 지나면 풀립니다.
- **HTTP 404** ("Provider returned error"): OpenRouter는 요청을 받았지만 뒤쪽 제공자에 해당 모델 배포가 없는 것입니다. 재시도해도 해결되지 않습니다. 2026-10-24~25 기준 텍스트 모델의 무료 NVIDIA 엔드포인트가 이 상태(`status -5`, 가동률 0%)여서, 텍스트 테스트는 폴백 모델이 대신 응답하고 있습니다. 같은 모델의 유료 버전(`:free`를 뗀 ID)은 다른 제공자들에서 정상이지만, 크레딧이 필요합니다.

## Windows 환경

셸은 PowerShell과 Git Bash입니다. 콘솔 기본 인코딩 때문에 한국어 출력이 깨질 수 있습니다. `test_api.py`는 직접 stdout을 UTF-8로 바꾸지만, `python -c ...`로 바로 실행하는 코드는 `PYTHONIOENCODING=utf-8`을 앞에 붙여 실행하세요. `python -I`는 `PYTHON*` 환경 변수를 무시하므로 이 방법이 통하지 않습니다. 이때는 코드 안에서 `sys.stdout.reconfigure(encoding="utf-8")`을 호출하세요.
