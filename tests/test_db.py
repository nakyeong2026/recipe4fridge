import pytest

import auth
import db


@pytest.fixture
def database(tmp_path):
    return db.Database(sqlite_path=tmp_path / "test.db")


def test_password_hash_roundtrip():
    stored = auth.hash_password("비밀번호1234")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("비밀번호1234", stored)
    assert not auth.verify_password("틀린비밀번호", stored)
    assert not auth.verify_password("x", None)


@pytest.mark.parametrize("email,pw,pw2,ok", [
    ("a@b.com", "12345678", "12345678", True),
    ("not-email", "12345678", "12345678", False),
    ("a@b.com", "short", "short", False),
    ("a@b.com", "12345678", "different", False),
])
def test_validate_signup(email, pw, pw2, ok):
    assert (auth.validate_signup(email, pw, pw2) is None) == ok


def test_signup_and_login(database):
    user = database.create_user("Cook@Example.com", "password123")
    assert user["email"] == "cook@example.com" and user["display_name"] == "cook"
    assert database.login("cook@example.com", "password123")["id"] == user["id"]
    assert database.login("cook@example.com", "wrong") is None
    with pytest.raises(db.DuplicateEmail):
        database.create_user("cook@example.com", "another123")


def test_google_user_is_not_linked_to_password_account(database):
    password_user = database.create_user("same@example.com", "password123")
    google = database.google_user("google-sub-1", "same@example.com", "구글 사용자")
    assert google["id"] != password_user["id"]
    assert database.google_user("google-sub-1", "same@example.com", "다른 이름")["id"] == google["id"]


def test_profile_preferences(database):
    user = database.create_user("p@example.com", "password123")
    assert db.prefs_of(user) == {}
    database.update_profile(user["id"], "요리왕", {"cuisine": "중식", "difficulty": "고급", "time": "30분 이상", "servings": "4인분"})
    updated = database.get_user(user["id"])
    assert updated["display_name"] == "요리왕"
    assert db.prefs_of(updated) == {"cuisine": "중식", "difficulty": "고급", "time": "30분 이상", "servings": "4인분"}


def test_save_list_delete_recipes(database):
    me = database.create_user("me@example.com", "password123")
    other = database.create_user("other@example.com", "password123")
    first = database.save_recipe(me["id"], {"title": "김치볶음밥", "steps": ["볶는다"]}, [{"name": "김치"}])
    second = database.save_recipe(me["id"], {"title": "달걀말이", "steps": ["만다"]}, [{"name": "달걀"}])
    rows = database.list_recipes(me["id"])
    assert [r["id"] for r in rows] == [second, first]
    assert rows[1]["recipe"]["title"] == "김치볶음밥" and rows[1]["ingredients"] == [{"name": "김치"}]
    database.delete_recipe(other["id"], first)  # 남의 레시피는 지워지지 않음
    assert len(database.list_recipes(me["id"])) == 2
    database.delete_recipe(me["id"], first)
    assert [r["id"] for r in database.list_recipes(me["id"])] == [second]


def test_to_kst_text():
    from datetime import datetime, timezone

    assert db.to_kst_text("2026-10-24 17:11:05") == "2026-10-25 02:11"
    assert db.to_kst_text(datetime(2026, 10, 24, 17, 11, tzinfo=timezone.utc)) == "2026-10-25 02:11"


def test_model_selection_and_migration_on_existing_table(tmp_path):
    path = tmp_path / "old.db"
    first = db.Database(sqlite_path=path)
    user = first.create_user("m@example.com", "password123")
    assert db.models_of(user) == ([], [])
    second = db.Database(sqlite_path=path)  # 이미 열이 있어도 다시 열 수 있어야 함
    second.update_models(user["id"], ["v/a:free"], ["t/b:free", "t/c:free"])
    assert db.models_of(second.get_user(user["id"])) == (["v/a:free"], ["t/b:free", "t/c:free"])
