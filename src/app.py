"""냉장고 레시피 추천 앱(Streamlit).

실행: streamlit run src/app.py
"""

import uuid

import streamlit as st

import auth
import db
import models
import recipe
import vision
from openrouter import AIError, AIResult, describe_error
from settings import get_secret

st.set_page_config(page_title="냉장고 레시피", page_icon="🥕", layout="wide")

# 버튼과 수량 +/- 버튼을 권장 터치 크기(44px) 이상으로 키웁니다.
st.html(
    """<style>
    .stButton button, .stFormSubmitButton button { min-height: 44px; }
    [data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] { min-width: 44px; }
    [data-testid="stNumberInputContainer"], [data-baseweb="input"], [data-baseweb="select"] > div { min-height: 44px; }
    </style>"""
)


# ---------- 저장소와 로그인 ----------

@st.cache_resource(show_spinner=False)
def get_db() -> db.Database:
    return db.Database(get_secret("DATABASE_URL"))


def database() -> db.Database | None:
    """DB를 열 수 없으면 None을 돌려주고, 저장 기능만 꺼집니다."""
    try:
        return get_db()
    except Exception as e:
        st.session_state.db_error = str(e)
        return None


def google_configured() -> bool:
    try:
        return bool(st.secrets.get("auth", {}).get("client_id"))
    except Exception:
        return False


def current_user() -> dict | None:
    if google_configured() and st.user.is_logged_in:
        sub = st.user.get("sub")
        cached = st.session_state.get("user")
        if cached and cached.get("_google_sub") == sub:
            return cached
        store = database()
        if store is None:
            return None
        user = store.google_user(sub, st.user.get("email", ""), st.user.get("name", ""))
        st.session_state.user = {**user, "_google_sub": sub}
    return st.session_state.get("user")


def refresh_user() -> None:
    user = st.session_state.get("user")
    store = database()
    if user and store:
        st.session_state.user = {**store.get_user(user["id"]), "_google_sub": user.get("_google_sub")}


def model_chains() -> tuple[list[str], list[str]]:
    """로그인한 사용자가 프로필에서 고른 모델로 (이미지, 텍스트) 시도 순서를 만듭니다."""
    vision_selected, text_selected = db.models_of(current_user())
    return (
        models.build_chain(vision_selected, vision.IMAGE_MODELS),
        models.build_chain(text_selected, recipe.TEXT_MODELS),
    )


@st.cache_data(ttl=3600, show_spinner="무료 모델 목록을 불러오는 중…")
def free_models() -> list[dict]:
    return models.fetch_free_models()


def clear_session() -> None:
    """로그아웃할 때 진행 상태(사진, 재료, 레시피)와 로그인 정보를 모두 지웁니다."""
    reset_all()
    st.session_state.pop("user", None)


# ---------- 상태 ----------

def init_state() -> None:
    defaults = {
        "step": 1,
        "photo": None,  # (jpeg 바이트, 축소 정보)
        "photo_key": None,  # 올린 파일이 바뀌었는지 판단
        "ingredients": [],  # [{"_id", "name", "quantity", "unit", "category"}]
        "vision_result": None,  # 성공한 인식의 모델·시도 정보
        "vision_error": None,
        "recipes": [],
        "recipe_result": None,
        "recipe_error": None,
        "recipes_pending": False,  # 2단계에 들어오면 자동으로 생성
        "uploader_nonce": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


STATE_PREFIXES = ("ing_", "step", "photo", "ingredients", "vision_", "recipe", "opt_", "saved_")


def reset_all() -> None:
    """사진 올리기부터 다시 시작합니다(위젯 상태까지 지움)."""
    nonce = st.session_state.get("uploader_nonce", 0) + 1
    for key in list(st.session_state.keys()):
        if key.startswith(STATE_PREFIXES):
            del st.session_state[key]
    st.session_state.uploader_nonce = nonce


def with_ids(items: list[dict]) -> list[dict]:
    return [{"_id": uuid.uuid4().hex, **item} for item in items]


def show_attempts(result: AIResult | None, error: AIError | None = None) -> None:
    attempts = result.attempts if result else getattr(error, "attempts", [])
    if not attempts:
        return
    with st.expander(f"시도 기록 ({len(attempts)}회)"):
        for a in attempts:
            mark = "✅" if a.ok else "❌"
            st.write(f"{mark} {a.model} · {a.number}차 시도" + (f" — {a.error}" if a.error else ""))


# ---------- 1단계: 사진과 식재료 ----------

def run_recognition() -> None:
    jpeg, _ = st.session_state.photo
    # 이전 결과가 이번 결과처럼 보이지 않도록 먼저 지웁니다.
    st.session_state.ingredients = []
    st.session_state.vision_result = None
    st.session_state.vision_error = None
    with st.status("식재료를 인식하는 중…", expanded=True) as status:
        try:
            result = vision.recognize(jpeg, on_status=status.write, models=model_chains()[0])
        except AIError as e:
            st.session_state.vision_error = e
            status.update(label="식재료 인식 실패", state="error")
            return
        st.session_state.ingredients = with_ids(result.data)
        st.session_state.vision_result = result
        status.update(label=f"인식 완료: {len(result.data)}개", state="complete", expanded=False)


def collect_ingredients() -> list[dict]:
    """편집 위젯의 현재 값을 목록에 반영하고, 같은 재료를 합칩니다."""
    rows = []
    for item in st.session_state.ingredients:
        i = item["_id"]
        name = st.session_state.get(f"ing_name_{i}", item["name"]).strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "quantity": int(st.session_state.get(f"ing_qty_{i}", item["quantity"])),
                "unit": st.session_state.get(f"ing_unit_{i}", item["unit"]),
                "category": item.get("category", "기타"),
            }
        )
    return vision.merge_duplicates(rows)


def delete_ingredient(item_id: str) -> None:
    st.session_state.ingredients = [x for x in st.session_state.ingredients if x["_id"] != item_id]
    for prefix in ("ing_name_", "ing_qty_", "ing_unit_"):
        st.session_state.pop(prefix + item_id, None)


def add_ingredient() -> None:
    name = st.session_state.get("ing_new_name", "").strip()
    if not name:
        return
    st.session_state.ingredients.append(
        {"_id": uuid.uuid4().hex, "name": name, "quantity": 1, "unit": "개", "category": "기타"}
    )
    st.session_state.ing_new_name = ""


def go_to_recipes() -> None:
    st.session_state.ingredients = with_ids(collect_ingredients())
    st.session_state.recipes = []
    st.session_state.recipe_result = None
    st.session_state.recipe_error = None
    st.session_state.recipes_pending = True
    st.session_state.step = 2


def ingredient_editor() -> None:
    items = st.session_state.ingredients
    st.caption("AI 인식 결과는 틀릴 수 있으니 사진과 비교해서 확인해 주세요.")
    for item in items:
        i = item["_id"]
        c_name, c_qty, c_unit, c_del = st.columns([4, 3, 2, 1], vertical_alignment="bottom")
        c_name.text_input("이름", value=item["name"], key=f"ing_name_{i}", label_visibility="collapsed")
        c_qty.number_input(
            "수량", min_value=1, max_value=9999, step=1, value=int(item["quantity"]),
            key=f"ing_qty_{i}", label_visibility="collapsed",
        )
        units = vision.UNITS
        c_unit.selectbox(
            "단위", units, index=units.index(item["unit"]) if item["unit"] in units else 0,
            key=f"ing_unit_{i}", label_visibility="collapsed",
        )
        c_del.button(
            "✕", key=f"ing_del_{i}", help=f"{item['name']} 삭제",
            on_click=delete_ingredient, args=(i,), width="stretch",
        )
    c_new, c_add = st.columns([8, 2], vertical_alignment="bottom")
    c_new.text_input("재료 추가", key="ing_new_name", placeholder="빠진 재료 이름", label_visibility="collapsed")
    c_add.button("추가", on_click=add_ingredient, width="stretch")


def step_photo() -> None:
    uploaded = st.file_uploader(
        "냉장고 사진을 고르거나 여기에 끌어 놓으세요",
        type=["jpg", "jpeg", "png", "webp"],
        key=f"uploader_{st.session_state.uploader_nonce}",
    )
    if uploaded is not None and uploaded.file_id != st.session_state.photo_key:
        try:
            st.session_state.photo = vision.prepare_image(uploaded.getvalue())
        except Exception:
            st.error("사진을 읽을 수 없습니다. 다른 사진을 골라 주세요.")
            return
        st.session_state.photo_key = uploaded.file_id
        st.session_state.ingredients = []
        st.session_state.vision_result = None
        st.session_state.vision_error = None
    if st.session_state.photo is None:
        return

    jpeg, info = st.session_state.photo
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.image(jpeg, width="stretch")
        st.caption(
            f"원본 {info['original_size'][0]}×{info['original_size'][1]} ({info['original_bytes'] // 1024:,}KB) → "
            f"전송 {info['size'][0]}×{info['size'][1]} ({info['bytes'] // 1024:,}KB)"
        )
        if st.button("식재료 인식하기", type="primary", width="stretch"):
            run_recognition()
    with right:
        st.subheader("인식된 식재료")
        error = st.session_state.vision_error
        result = st.session_state.vision_result
        if error:
            st.error(f"**식재료 인식 실패** — {describe_error(error)}")
            show_attempts(None, error)
        elif result is None and not st.session_state.ingredients:
            st.info("왼쪽의 '식재료 인식하기'를 누르면 이곳에 결과가 나옵니다.")
        if result:
            note = " (예비 모델이 대신 응답)" if result.used_fallback else ""
            st.caption(f"응답 모델: `{result.model}`{note}")
            show_attempts(result)
            if not result.data:
                st.warning("사진에서 식재료를 찾지 못했습니다. 직접 추가할 수 있습니다.")
        if result or st.session_state.ingredients:
            ingredient_editor()
            st.button(
                "레시피 추천받기 →", type="primary", width="stretch",
                disabled=not st.session_state.ingredients, on_click=go_to_recipes,
            )


# ---------- 2단계: 레시피 ----------

OPTION_SPECS = [
    ("cuisine", "종류", recipe.CUISINE_OPTIONS),
    ("difficulty", "난이도", recipe.DIFFICULTY_OPTIONS),
    ("time", "조리 시간", recipe.TIME_OPTIONS),
    ("servings", "인원", recipe.SERVINGS_OPTIONS),
]


def default_options() -> dict:
    """로그인한 사용자는 프로필에 저장한 조건이 기본값이 됩니다."""
    return {**recipe.DEFAULT_OPTIONS, **db.prefs_of(current_user())}


def current_options() -> dict:
    return {key: st.session_state[f"opt_{key}"] for key, _, _ in OPTION_SPECS}


def option_widgets() -> None:
    defaults = default_options()
    for col, (key, label, choices) in zip(st.columns(len(OPTION_SPECS)), OPTION_SPECS):
        st.session_state.setdefault(f"opt_{key}", defaults[key])
        col.selectbox(label, choices, key=f"opt_{key}")


def run_generation() -> None:
    st.session_state.recipes = []
    st.session_state.recipe_result = None
    st.session_state.recipe_error = None
    st.session_state.saved_titles = set()
    ingredients = [{k: v for k, v in i.items() if k != "_id"} for i in st.session_state.ingredients]
    with st.status("레시피를 만드는 중…", expanded=True) as status:
        try:
            result = recipe.generate(
                ingredients, current_options(), on_status=status.write, models=model_chains()[1]
            )
        except AIError as e:
            st.session_state.recipe_error = e
            status.update(label="레시피 생성 실패", state="error")
            return
        st.session_state.recipes = result.data
        st.session_state.recipe_result = result
        status.update(label=f"레시피 {len(result.data)}개 생성 완료", state="complete", expanded=False)


def recipe_details(r: dict) -> None:
    st.markdown("**재료**")
    for i in r["used_ingredients"]:
        st.write(f"- {i['name']} {i['amount']}")
    for i in r["missing_ingredients"]:
        st.write(f"- :red[{i['name']} {i['amount']} (부족)]")
    st.markdown("**조리 순서**")
    for n, s in enumerate(r["steps"], start=1):
        st.write(f"{n}. {s}")
    if r.get("tips"):
        st.info(r["tips"])


def save_button(r: dict, index: int) -> None:
    user = current_user()
    saved = st.session_state.setdefault("saved_titles", set())
    store = database()
    if user is None or store is None:
        st.button("로그인하면 저장할 수 있어요", key=f"save_{index}", disabled=True, width="stretch")
    elif r["title"] in saved:
        st.button("저장됨 ✓", key=f"save_{index}", disabled=True, width="stretch")
    elif st.button("레시피 저장", key=f"save_{index}", width="stretch"):
        ingredients = [{k: v for k, v in i.items() if k != "_id"} for i in st.session_state.ingredients]
        store.save_recipe(user["id"], r, ingredients)
        saved.add(r["title"])
        st.toast(f"'{r['title']}'을(를) 내 레시피에 저장했습니다.")
        st.rerun()


def recipe_card(r: dict, index: int) -> None:
    with st.container(border=True):
        # 요약 칸의 높이를 고정해서 카드마다 '자세히 보기' 위치를 맞춥니다.
        with st.container(height=280, border=False):
            st.markdown(f"#### {r['title']}")
            if r["summary"]:
                st.caption(r["summary"])
            meta = [f"⏱ {r['time_minutes']}분" if r["time_minutes"] else "", r["difficulty"],
                    f"{r['servings']}인분" if r["servings"] else ""]
            st.write(" · ".join(m for m in meta if m))
            if r["used_ingredients"]:
                st.markdown("**있는 재료** " + ", ".join(i["name"] for i in r["used_ingredients"]))
            if r["missing_ingredients"]:
                st.markdown(
                    ":red[**부족한 재료**] "
                    + ", ".join(f"{i['name']} {i['amount']}".strip() for i in r["missing_ingredients"])
                )
            else:
                st.markdown(":green[부족한 재료 없음]")
        with st.expander("자세히 보기"):
            recipe_details(r)
        save_button(r, index)


def step_recipes() -> None:
    with st.container(horizontal=True):
        for i in st.session_state.ingredients:
            st.badge(f"{i['name']} {i['quantity']}{i['unit']}", color="gray")
    option_widgets()
    back, again = st.columns(2)
    back.button("← 이전(식재료 수정)", on_click=lambda: st.session_state.update(step=1), width="stretch")
    regenerate = again.button("이 조건으로 다시 추천받기", type="primary", width="stretch")

    if st.session_state.recipes_pending or regenerate:
        st.session_state.recipes_pending = False
        run_generation()

    error = st.session_state.recipe_error
    result = st.session_state.recipe_result
    if error:
        st.error(f"**레시피 생성 실패** — {describe_error(error)}")
        show_attempts(None, error)
        return
    if result:
        note = " (예비 모델이 대신 응답)" if result.used_fallback else ""
        st.caption(f"응답 모델: `{result.model}`{note}")
        show_attempts(result)
        recipes = st.session_state.recipes
        for index, (col, r) in enumerate(zip(st.columns(len(recipes)), recipes)):
            with col:
                recipe_card(r, index)


# ---------- 화면 ----------

def header() -> None:
    title, restart = st.columns([5, 1], vertical_alignment="center")
    title.title("🥕 냉장고 레시피")
    restart.button("처음부터", on_click=reset_all, width="stretch")
    steps = ["① 사진과 식재료", "② 레시피 추천"]
    st.markdown(
        " → ".join(f"**{s}**" if n == st.session_state.step else s for n, s in enumerate(steps, start=1))
    )


def login_forms(store: db.Database) -> None:
    login_tab, signup_tab = st.tabs(["로그인", "회원가입"])
    with login_tab, st.form("login_form", border=False):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        if st.form_submit_button("로그인", width="stretch"):
            user = store.login(email, password)
            if user:
                st.session_state.user = user
                st.rerun()
            st.error("이메일 또는 비밀번호가 맞지 않습니다.")
    with signup_tab, st.form("signup_form", border=False):
        email = st.text_input("이메일", key="signup_email")
        password = st.text_input("비밀번호(8자 이상)", type="password", key="signup_pw")
        password_again = st.text_input("비밀번호 확인", type="password", key="signup_pw2")
        if st.form_submit_button("가입하기", width="stretch"):
            problem = auth.validate_signup(email.strip(), password, password_again)
            if problem:
                st.error(problem)
            else:
                try:
                    st.session_state.user = store.create_user(email, password)
                    st.rerun()
                except db.DuplicateEmail:
                    st.error("이미 가입된 이메일입니다.")


def sidebar() -> None:
    with st.sidebar:
        store = database()
        if store is None:
            st.error("저장소에 연결하지 못해 로그인과 저장 기능을 쓸 수 없습니다.")
            st.caption(st.session_state.get("db_error", ""))
            return
        user = current_user()
        if user:
            st.success(f"**{user['display_name']}** 님")
            st.caption(user.get("email") or user.get("google_email") or "")
            if st.button("로그아웃", width="stretch"):
                was_google = bool(user.get("_google_sub"))
                clear_session()
                if was_google:
                    st.logout()
                st.rerun()
        else:
            login_forms(store)
            st.divider()
            if google_configured():
                if st.button("Google로 로그인", width="stretch"):
                    st.login()
            else:
                st.caption("구글 로그인은 아직 설정되지 않았습니다.")
        st.caption("저장소: " + ("Supabase(Postgres)" if store.kind == "postgres" else "로컬 SQLite(재부팅하면 지워질 수 있음)"))


def page_recipes() -> None:
    init_state()
    header()
    if st.session_state.step == 1:
        step_photo()
    else:
        step_recipes()


def require_login() -> tuple[dict, db.Database] | None:
    user, store = current_user(), database()
    if user is None or store is None:
        st.info("왼쪽 사이드바에서 로그인하면 이용할 수 있습니다.")
        return None
    return user, store


def page_saved() -> None:
    st.title("📒 내 레시피")
    session = require_login()
    if session is None:
        return
    user, store = session
    rows = store.list_recipes(user["id"])
    if not rows:
        st.info("아직 저장한 레시피가 없습니다. 레시피 찾기에서 마음에 드는 레시피를 저장해 보세요.")
        return
    st.caption(f"{len(rows)}개")
    for row in rows:
        r = row["recipe"]
        with st.expander(f"{row['title']} · {row['created_at']} 저장"):
            if r.get("summary"):
                st.caption(r["summary"])
            st.write("그때 냉장고 재료: " + ", ".join(i["name"] for i in row["ingredients"]))
            recipe_details(r)
            if st.button("삭제", key=f"del_saved_{row['id']}"):
                store.delete_recipe(user["id"], row["id"])
                st.rerun()


def page_profile() -> None:
    st.title("👤 프로필")
    session = require_login()
    if session is None:
        return
    user, store = session
    prefs = {**recipe.DEFAULT_OPTIONS, **db.prefs_of(user)}
    with st.form("profile_form"):
        name = st.text_input("표시 이름", value=user["display_name"])
        st.markdown("**레시피 추천 기본 조건**")
        chosen = {}
        for col, (key, label, choices) in zip(st.columns(len(OPTION_SPECS)), OPTION_SPECS):
            chosen[key] = col.selectbox(label, choices, index=choices.index(prefs[key]))
        if st.form_submit_button("저장", type="primary"):
            store.update_profile(user["id"], name.strip() or user["display_name"], chosen)
            refresh_user()
            # 다음에 레시피 화면을 열 때 새 기본값이 적용되도록 조건 위젯 상태를 지웁니다.
            for key, _, _ in OPTION_SPECS:
                st.session_state.pop(f"opt_{key}", None)
            # 사이드바는 페이지보다 먼저 그려지므로, 바뀐 이름이 보이도록 한 번 더 그립니다.
            st.session_state.profile_saved = True
            st.rerun()
    if st.session_state.pop("profile_saved", False):
        st.success("프로필을 저장했습니다. 레시피 추천 조건의 기본값으로 쓰입니다.")
    model_selection(user, store)


def model_table(title: str, rows: list[dict], selected: list[str], defaults: list[str], key: str) -> list[str]:
    """무료 모델 표에서 체크한 모델 id 목록을 돌려줍니다."""
    import pandas as pd

    checked = set(selected or defaults)
    frame = pd.DataFrame(
        [{"사용": r["id"] in checked, "모델 ID": r["id"], "이름": r["name"],
          "컨텍스트": f"{r['context']:,}", "출시일": r["created"]} for r in rows]
    )
    st.markdown(f"**{title}**")
    edited = st.data_editor(
        frame, key=key, hide_index=True, width="stretch",
        disabled=["모델 ID", "이름", "컨텍스트", "출시일"],
        column_config={"사용": st.column_config.CheckboxColumn(width="small")},
    )
    return edited.loc[edited["사용"], "모델 ID"].tolist()


def model_selection(user: dict, store: db.Database) -> None:
    st.divider()
    st.subheader("AI 모델 선택")
    st.caption(
        "OpenRouter에서 입력·출력 가격이 모두 $0인 무료 모델만 보여 줍니다. "
        f"체크한 모델을 수업 지정 모델 → 나머지 순서로 최대 {models.MAX_CHAIN}개까지 차례로 시도하고, "
        "하나도 고르지 않으면 기본 순서를 씁니다. 같은 무료 모델이라도 계정이나 시점에 따라 403·404로 거절될 수 있습니다."
    )
    try:
        rows = free_models()
    except Exception as e:
        st.warning(f"무료 모델 목록을 불러오지 못했습니다: {e}")
        return
    vision_selected, text_selected = db.models_of(user)
    with st.form("model_form"):
        chosen_vision = model_table(
            "식재료 인식(이미지 입력 가능 모델)", [r for r in rows if r["vision"]],
            vision_selected, vision.IMAGE_MODELS, "vision_table",
        )
        chosen_text = model_table("레시피 생성", rows, text_selected, recipe.TEXT_MODELS, "text_table")
        if st.form_submit_button("모델 선택 저장", type="primary"):
            store.update_models(user["id"], chosen_vision, chosen_text)
            refresh_user()
            st.session_state.models_saved = True
            st.rerun()
    if st.session_state.pop("models_saved", False):
        st.success("모델 선택을 저장했습니다.")
    vision_chain, text_chain = model_chains()
    st.caption("지금 시도 순서 — 인식: " + " → ".join(vision_chain) + " / 레시피: " + " → ".join(text_chain))


def main() -> None:
    pages = [
        st.Page(page_recipes, title="레시피 찾기", icon="🥕", url_path="recipes", default=True),
        st.Page(page_saved, title="내 레시피", icon="📒", url_path="saved"),
        st.Page(page_profile, title="프로필", icon="👤", url_path="profile"),
    ]
    navigation = st.navigation(pages)
    sidebar()
    navigation.run()


main()
