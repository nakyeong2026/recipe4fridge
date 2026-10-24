# PRD 3단계: 사용자 프로필과 레시피 저장

## 1. 목표
사용자가 로그인해서 선호 조건을 프로필에 저장하고, 마음에 드는 레시피를 저장해 두었다가 다시 볼 수 있게 한다. 이 단계에서는 AI 모델을 쓰지 않는다.

## 2. 기술 결정
| 항목 | 결정 | 이유 |
|---|---|---|
| DB | Supabase(Postgres) | 앱 밖에 있어서, Streamlit Cloud 앱을 재부팅해도 데이터가 남음 |
| DB 연결 | `psycopg` + Supabase Transaction pooler(포트 6543) 연결 문자열 | IPv4 환경과 짧은 연결에 맞음 |
| 로그인 | 이메일·비밀번호 로그인과 구글 로그인(Streamlit 내장 `st.login`) | |
| 연결 문자열 없을 때 | 로컬 SQLite 파일로 대신 저장 | DB 설정 없이도 개발과 시험이 가능 |

## 3. 비밀 값 위치
| 값 | 로컬 | 배포 |
|---|---|---|
| `OPENROUTER_API_KEY`, `DATABASE_URL` | `.env` | Streamlit Secrets(최상위 키) |
| 구글 로그인(`redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`) | `.streamlit/secrets.toml`의 `[auth]` | Streamlit Secrets의 `[auth]` |

- `.env`와 `.streamlit/secrets.toml`은 깃에 올리지 않는다. 저장소에는 값이 빠진 예시 파일만 둔다.
- Secrets에서는 `[auth]` 아래 줄이 모두 `[auth]`에 속하므로, 최상위 키를 먼저 쓰고 `[auth]`를 맨 뒤에 둔다.

## 4. 기능 요구
### 4.1 회원과 로그인
- 이메일·비밀번호로 가입하고 로그인한다. 비밀번호는 솔트를 붙인 PBKDF2-SHA256 해시로만 저장한다.
- 구글 로그인 회원은 구글이 준 고유 id(`sub`)로 구분한다.
- **보안**: 구글 로그인을 이메일이 같다는 이유만으로 기존 비밀번호 계정에 자동 연결하지 않는다. 가입할 때 이메일 인증이 없으므로, 남이 내 이메일로 먼저 가입해 두면 내 구글 계정에 그 사람이 비밀번호로 들어올 수 있기 때문이다.
- 로그아웃하면 사진, 식재료, 레시피 등 진행 상태를 모두 지운다.
- 로그인하지 않아도 1·2단계는 쓸 수 있고, 저장 기능만 로그인을 요구한다.

### 4.2 프로필
- 표시 이름과 선호 조건(종류, 난이도, 조리 시간, 인원)을 저장한다.
- 2단계 조건 드롭다운의 기본값으로 쓰고, 화면에서 바꿀 수 있다.

### 4.3 레시피 저장
- 2단계 레시피 카드마다 "저장" 버튼을 둔다.
- "내 레시피" 화면에서 저장한 레시피를 최신순으로 보고, 펼쳐 보고, 삭제한다.
- 레시피는 JSON 그대로 저장하고, 그때 쓴 식재료 목록과 저장 시각을 함께 남긴다.

### 4.4 DB 구조
```sql
users(id, email UNIQUE NULL, password_hash NULL, google_sub UNIQUE NULL,
      display_name, pref_cuisine, pref_difficulty, pref_time, pref_servings, created_at)
saved_recipes(id, user_id → users.id ON DELETE CASCADE, title, recipe_json, ingredients_json, created_at)
```
- 앱이 처음 연결될 때 표가 없으면 만든다.
- Supabase에서는 RLS(행 수준 보안)를 켠다. 앱은 서버에서 DB 소유자 권한으로 연결하므로 동작에 영향이 없고, 공개 anon 키로는 표를 읽을 수 없게 된다.
- Streamlit Cloud 앱이 잠들었다 깨면 끊긴 연결이 남을 수 있으므로, 연결을 쓰기 전에 살아 있는지 확인하고 끊겼으면 다시 연결한다.

## 5. 완료 기준
- 이메일 가입, 로그인, 로그아웃이 동작한다.
- 구글 로그인 설정이 없어도 앱이 멈추지 않고 안내 문구를 보인다.
- 프로필 조건이 2단계 기본값에 반영된다.
- 레시피 저장, 조회, 삭제가 동작하고, 앱을 다시 시작해도 남아 있다(Supabase 사용 시).
- DB 계층 자동 시험이 SQLite로 통과한다.
