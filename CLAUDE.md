# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

OpenRouter API로 냉장고 사진에서 식재료를 인식하고 레시피를 추천하는 Streamlit 앱을 만드는 학습 프로젝트입니다. 사용자와의 대화, 코드 주석, 출력 메시지는 모두 한국어로 씁니다.

- `docs/`: 단계별 요구 사항 `PRD_step1.md`(식재료 인식), `PRD_step2.md`(레시피 추천), `PRD_step3.md`(프로필·저장). 구현은 이 문서를 기준으로 합니다.
- `api_test/`: 앱 구현 전에 두 모델의 API 연결을 시험한 코드입니다.

- Python 3.12. HTTP 요청은 표준 라이브러리 `urllib`만 사용합니다(`requests`, `python-dotenv`는 설치되어 있지 않음).
- 앱 의존성은 `requirements.txt`에 있습니다. 구글 로그인(`st.login`)에는 Authlib와 httpx가 필요해서 `streamlit[auth]`로 설치합니다(httpx가 없으면 로그인 버튼을 누를 때 Internal Server Error).

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
- `recipe.py`(2단계): 식재료와 조건(종류·난이도·조리 시간·인원)으로 프롬프트를 만들고, 응답을 정리합니다. 제목과 조리 순서의 80% 이상이 한글이 아니면 `ValueError`로 재시도시킵니다. `extract_json`은 `vision.py`의 것을 같이 씁니다. 레시피는 응답이 길어서 시간 제한이 120초입니다.
- `app.py`: 단계(`st.session_state.step`) 단위의 마법사 화면입니다. 2단계에 들어오면 `recipes_pending` 플래그로 레시피 생성을 자동으로 시작합니다. 식재료 행은 고유 `_id`를 위젯 key로 써서 중간 행을 지워도 다른 행이 지워지지 않습니다. 실패하면 이전 결과를 먼저 지우고 실패를 `session_state`에 저장해서 다음 실행 때 그려 줍니다(`st.rerun()`으로 오류 문구가 사라지지 않게 하려는 것).
- `db.py`(3단계): `DATABASE_URL`이 있으면 Supabase(Postgres, psycopg), 없으면 `data/app.db`(SQLite)를 씁니다. SQL은 `%s`로 쓰고 SQLite에서 `?`로 바꿉니다. Postgres 연결에는 Transaction pooler용 `prepare_threshold=None`, 잠든 앱이 깨어날 때를 대비한 `tcp_user_timeout`과 실행 전 `SELECT 1` 생존 확인이 들어 있습니다. 표를 만들 때 RLS를 켭니다. 시각은 UTC로 저장하고 `to_kst_text()`로 바꿔 보여 줍니다.
  - 보안 결정: 구글 계정은 `google_sub`로만 찾고, 이메일이 같아도 비밀번호 계정과 합치지 않습니다(가입 시 이메일 인증이 없어서, 합치면 남이 미리 가입해 둔 계정으로 내 구글 계정에 들어올 수 있음). 그래서 구글 이메일은 `email`(UNIQUE)이 아니라 `google_email`에 둡니다.
- `auth.py`: PBKDF2-SHA256 비밀번호 해시와 가입 입력 검사입니다.
- `models.py`: OpenRouter `/api/v1/models`에서 입력·출력 가격이 모두 0인 모델을 골라 프로필의 모델 선택 표에 씁니다. `build_chain()`은 사용자가 체크한 모델 중 기본 순서(`IMAGE_MODELS`, `TEXT_MODELS`)에 있는 것을 먼저 두고, 하루 50회 한도를 생각해 최대 4개로 자릅니다. 선택은 `users.vision_models`·`text_models`(JSON)에 저장되고, 이 열은 `db.MIGRATIONS`로 기존 표에도 추가됩니다. 공식 API에는 주간 토큰·지연 시간 같은 지표가 없어 표에 넣지 않았습니다.
- `app.py`의 3단계 부분: `st.navigation`으로 레시피 찾기, 내 레시피, 프로필 페이지를 나눕니다. 사이드바는 페이지보다 먼저 그려지므로, 사용자 정보를 바꾼 뒤에는 `st.rerun()`으로 다시 그려야 합니다. `get_db(url)`은 연결 문자열을 키로 `st.cache_resource`에 캐시합니다. Secrets의 `DATABASE_URL`을 바꾸면 새로 연결되지만, `db.py` 코드를 고친 뒤에는 앱 서버를 다시 시작해야 반영됩니다. 구글 로그인은 `st.secrets["auth"]["client_id"]`가 있을 때만 버튼이 보입니다.
- 비밀 값 예시: `.env.example`(`OPENROUTER_API_KEY`, `DATABASE_URL`), `.streamlit/secrets.toml.example`(구글 로그인 `[auth]`).
- 각 단계의 요구 사항은 `docs/PRD_step*.md`에 있습니다. 단계를 마칠 때마다 `screenshot/<태그>/`에 화면 캡처를 남기고, `PRD_01_01`처럼 태그를 붙여 커밋합니다.

### API 시험 코드(`api_test/`)
- `api_test/config.py`: 프로젝트 루트의 `.env`를 직접 파싱해서 `os.environ.setdefault`로 채웁니다. 시스템 환경 변수에 이미 있는 값이 `.env`보다 우선합니다. API 키는 항상 `get_openrouter_key()`로 가져오고, 로그에 남겨야 하면 `mask()`를 씁니다.
- `api_test/test_api.py`: 공통 요청 함수 `chat()`가 OpenRouter `/api/v1/chat/completions`를 호출합니다. 429와 5xx 오류는 10초, 20초, 40초 간격으로 재시도하고, 404 같은 그 밖의 오류는 바로 `RuntimeError`로 올립니다. 모델 ID는 파일 위쪽 상수에 있습니다.
  - 텍스트 모델 `TEXT_MODEL` = `nvidia/nemotron-3-super-120b-a12b:free`, 대체 모델은 `TEXT_FALLBACKS`.
  - 이미지 모델 `IMAGE_MODEL` = `google/gemma-4-26b-a4b-it:free`, 대체 모델은 `IMAGE_FALLBACKS`. 이미지는 base64 data URL로 첨부합니다. 대체 모델도 반드시 이미지 입력을 지원해야 하고, 1순위와 같은 Google 공용 한도에 함께 걸리지 않도록 다른 제공자의 모델로 골랐습니다.
  - 폴백 동작: `chat()`에 `fallbacks`를 넘기면 요청이 `model` 대신 `models` 배열로 나가고, 1순위 모델이 실패하면 OpenRouter가 서버 쪽에서 대체 모델을 차례로 시도합니다. **`models` 배열은 1순위를 포함해 최대 3개**이고(넘으면 HTTP 400), `chat()`이 요청 전에 개수를 확인합니다. 모든 모델이 실패했을 때만 위의 재시도 로직이 동작합니다. 실제로 응답한 모델은 응답의 `model` 필드에 있고, `report()`는 그 값이 요청 모델과 다르면 폴백되었다고 표시합니다.
  - 폴백을 강제로 검증하려면 1순위를 다운된 모델로 임시 지정합니다(`api_test/`에서 실행): `python -c "import test_api as t; t.IMAGE_MODEL='nvidia/nemotron-3-super-120b-a12b:free'; t.test_image()"`
- `api_test/sample.png`: 정답을 미리 아는 테스트 이미지입니다(빨간 원, 파란 사각형, 초록 삼각형, 글자 "HELLO 2026"). 파일이 없으면 `make_sample_image()`가 다시 만듭니다. 그림을 바꾸면 이미지 테스트의 정답도 같이 바뀝니다.

## 배포

- 배포 앱: https://recipe4fridge-rmvcfl7qjnzc4tu6xs63kw.streamlit.app (Streamlit Community Cloud, 저장소 `nakyeong2026/recipe4fridge`의 `main`, 앱 파일 `src/app.py`)
- `main`에 푸시하면 배포 앱에 자동으로 반영됩니다. 깃허브의 코드가 맞는데도 배포 앱만 ImportError 같은 오류를 내면, 대시보드의 ⋮ → Reboot app으로 다시 시작해 봅니다.
- 배포 앱의 비밀 값은 대시보드의 Settings → Secrets에 있습니다. 최상위 키(`OPENROUTER_API_KEY`, `DATABASE_URL`)를 먼저 쓰고 `[auth]` 블록을 맨 뒤에 둡니다.
- DB는 Supabase 프로젝트(서울 `ap-northeast-2`, Transaction pooler 6543)입니다. 데이터 API는 쓰지 않습니다(앱이 Postgres에 직접 연결).
- 구글 로그인은 Google Cloud 프로젝트 `recipe4fridge`의 OAuth 클라이언트(웹 애플리케이션)를 씁니다. 동의 화면 대상이 "외부"이고 테스트 모드라서, Google 인증 플랫폼 → 대상 → 테스트 사용자에 등록한 계정만 로그인할 수 있습니다. 승인된 리디렉션 URI는 `http://localhost:8501/oauth2callback`과 배포 주소 + `/oauth2callback` 두 개입니다.
- 배포 앱은 내장 브라우저에서 바깥 페이지 안의 iframe(같은 출처)으로 열리므로, 자동 조작은 `document.querySelector('iframe').contentDocument`에서 합니다. 시험 사진은 raw.githubusercontent.com에서 fetch해 업로드 칸에 넣을 수 있습니다.

## 실행 시 확인 사항

OpenRouter API를 이용해서 실제 AI 모델이 냉장고 사진 이미지를 인식하고 레시피를 생성하게 한다. 앱을 실행해 시험할 때마다 API가 정확하게 작동했는지, AI 모델이 문제 없이 실행되었는지(응답한 모델, 몇 차 시도, 예비 모델 전환 여부)를 확인하고, 문제가 있었다면 어떤 문제였는지 보고한다.

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
