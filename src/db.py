"""3단계 저장소: DATABASE_URL이 있으면 Supabase(Postgres), 없으면 로컬 SQLite.

SQL은 %s 자리 표시자로 쓰고, SQLite에서는 ?로 바꿔 실행합니다.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = ROOT / "data" / "app.db"

PREF_KEYS = ("cuisine", "difficulty", "time", "servings")
KST = timezone(timedelta(hours=9))


def to_kst_text(value) -> str:
    """DB 시각(SQLite는 UTC 문자열, Postgres는 시간대 있는 datetime)을 한국 시각 문자열로 바꿉니다."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M")

SCHEMA = {
    "postgres": [
        """CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT UNIQUE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            google_email TEXT,
            display_name TEXT NOT NULL DEFAULT '',
            pref_cuisine TEXT, pref_difficulty TEXT, pref_time TEXT, pref_servings TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
        """CREATE TABLE IF NOT EXISTS saved_recipes (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            recipe_json TEXT NOT NULL,
            ingredients_json TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
        # 공개 anon 키로 표를 읽지 못하게 막습니다. 앱은 표 소유자로 연결하므로 영향이 없습니다.
        "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE saved_recipes ENABLE ROW LEVEL SECURITY",
    ],
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            google_email TEXT,
            display_name TEXT NOT NULL DEFAULT '',
            pref_cuisine TEXT, pref_difficulty TEXT, pref_time TEXT, pref_servings TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS saved_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            recipe_json TEXT NOT NULL,
            ingredients_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    ],
}

USER_COLUMNS = "id, email, google_email, display_name, pref_cuisine, pref_difficulty, pref_time, pref_servings"


class DuplicateEmail(Exception):
    pass


class Database:
    def __init__(self, url: str | None = None, sqlite_path: Path = SQLITE_PATH):
        self.kind = "postgres" if url else "sqlite"
        self._url = url
        self._sqlite_path = sqlite_path
        self._lock = threading.Lock()
        self._conn = None
        with self._lock:
            for sql in SCHEMA[self.kind]:
                self._execute(sql)

    # ---------- 연결 ----------

    def _connect(self):
        if self.kind == "sqlite":
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._sqlite_path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        import psycopg

        return psycopg.connect(
            self._url,
            autocommit=True,
            connect_timeout=10,
            # Supabase Transaction pooler(6543)는 준비된 문장을 지원하지 않습니다.
            prepare_threshold=None,
            # 앱이 잠들었다 깬 뒤 끊긴 연결을 10분이 아니라 8초 안에 알아차리게 합니다.
            tcp_user_timeout=8000,
            keepalives=1,
            keepalives_idle=30,
        )

    def _alive(self) -> bool:
        if self._conn is None:
            return False
        if self.kind == "sqlite":
            return True
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _execute(self, sql: str, params: tuple = ()):
        """연결이 죽었으면 새로 연결하고 실행합니다. 호출하는 쪽이 lock을 잡습니다."""
        if not self._alive():
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = self._connect()
        if self.kind == "sqlite":
            sql = sql.replace("%s", "?")
        return self._conn.execute(sql, params)

    def _query(self, sql: str, params: tuple = (), one: bool = False):
        with self._lock:
            cur = self._execute(sql, params)
            if cur.description is None:
                return None
            names = [d[0] for d in cur.description]
            rows = [dict(zip(names, row)) for row in cur.fetchall()]
        return (rows[0] if rows else None) if one else rows

    # ---------- 회원 ----------

    def create_user(self, email: str, password: str) -> dict:
        email = email.strip().lower()
        if self._query("SELECT id FROM users WHERE email = %s", (email,), one=True):
            raise DuplicateEmail(email)
        return self._query(
            f"INSERT INTO users (email, password_hash, display_name) VALUES (%s, %s, %s) RETURNING {USER_COLUMNS}",
            (email, auth.hash_password(password), email.split("@")[0]),
            one=True,
        )

    def login(self, email: str, password: str) -> dict | None:
        row = self._query(
            f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE email = %s", (email.strip().lower(),), one=True
        )
        if row and auth.verify_password(password, row.pop("password_hash")):
            return row
        return None

    def google_user(self, sub: str, email: str, name: str) -> dict:
        """구글 계정은 sub로만 찾습니다. 이메일이 같아도 비밀번호 계정과 자동으로 합치지 않습니다."""
        row = self._query(f"SELECT {USER_COLUMNS} FROM users WHERE google_sub = %s", (sub,), one=True)
        if row:
            return row
        return self._query(
            f"INSERT INTO users (google_sub, google_email, display_name) VALUES (%s, %s, %s) RETURNING {USER_COLUMNS}",
            (sub, email, name or email.split("@")[0]),
            one=True,
        )

    def get_user(self, user_id: int) -> dict | None:
        return self._query(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,), one=True)

    def update_profile(self, user_id: int, display_name: str, prefs: dict) -> None:
        self._query(
            "UPDATE users SET display_name = %s, pref_cuisine = %s, pref_difficulty = %s, pref_time = %s, "
            "pref_servings = %s WHERE id = %s",
            (display_name, *(prefs.get(k) for k in PREF_KEYS), user_id),
        )

    # ---------- 레시피 ----------

    def save_recipe(self, user_id: int, recipe: dict, ingredients: list[dict]) -> int:
        row = self._query(
            "INSERT INTO saved_recipes (user_id, title, recipe_json, ingredients_json) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (user_id, recipe["title"], json.dumps(recipe, ensure_ascii=False),
             json.dumps(ingredients, ensure_ascii=False)),
            one=True,
        )
        return row["id"]

    def list_recipes(self, user_id: int) -> list[dict]:
        rows = self._query(
            "SELECT id, title, recipe_json, ingredients_json, created_at FROM saved_recipes "
            "WHERE user_id = %s ORDER BY created_at DESC, id DESC",
            (user_id,),
        )
        return [
            {"id": r["id"], "title": r["title"], "recipe": json.loads(r["recipe_json"]),
             "ingredients": json.loads(r["ingredients_json"]), "created_at": to_kst_text(r["created_at"])}
            for r in rows
        ]

    def delete_recipe(self, user_id: int, recipe_id: int) -> None:
        # user_id 조건을 함께 걸어 남의 레시피는 지울 수 없게 합니다.
        self._query("DELETE FROM saved_recipes WHERE id = %s AND user_id = %s", (recipe_id, user_id))


def prefs_of(user: dict | None) -> dict:
    """사용자 행에서 저장된 선호 조건만 꺼냅니다(비어 있으면 빠짐)."""
    if not user:
        return {}
    return {k: user[f"pref_{k}"] for k in PREF_KEYS if user.get(f"pref_{k}")}
