"""냉장고 레시피 추천 앱(Streamlit).

실행: streamlit run src/app.py
"""

import uuid

import streamlit as st

import recipe
import vision
from openrouter import AIError, AIResult, describe_error

st.set_page_config(page_title="냉장고 레시피", page_icon="🥕", layout="wide")


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


STATE_PREFIXES = ("ing_", "step", "photo", "ingredients", "vision_", "recipe", "opt_")


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
            result = vision.recognize(jpeg, on_status=status.write)
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
    return dict(recipe.DEFAULT_OPTIONS)


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
    ingredients = [{k: v for k, v in i.items() if k != "_id"} for i in st.session_state.ingredients]
    with st.status("레시피를 만드는 중…", expanded=True) as status:
        try:
            result = recipe.generate(ingredients, current_options(), on_status=status.write)
        except AIError as e:
            st.session_state.recipe_error = e
            status.update(label="레시피 생성 실패", state="error")
            return
        st.session_state.recipes = result.data
        st.session_state.recipe_result = result
        status.update(label=f"레시피 {len(result.data)}개 생성 완료", state="complete", expanded=False)


def recipe_card(r: dict) -> None:
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
            st.markdown("**재료**")
            for i in r["used_ingredients"]:
                st.write(f"- {i['name']} {i['amount']}")
            for i in r["missing_ingredients"]:
                st.write(f"- :red[{i['name']} {i['amount']} (부족)]")
            st.markdown("**조리 순서**")
            for n, s in enumerate(r["steps"], start=1):
                st.write(f"{n}. {s}")
            if r["tips"]:
                st.info(r["tips"])


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
        for col, r in zip(st.columns(len(st.session_state.recipes)), st.session_state.recipes):
            with col:
                recipe_card(r)


# ---------- 화면 ----------

def header() -> None:
    title, restart = st.columns([5, 1], vertical_alignment="center")
    title.title("🥕 냉장고 레시피")
    restart.button("처음부터", on_click=reset_all, width="stretch")
    steps = ["① 사진과 식재료", "② 레시피 추천"]
    st.markdown(
        " → ".join(f"**{s}**" if n == st.session_state.step else s for n, s in enumerate(steps, start=1))
    )


def main() -> None:
    init_state()
    header()
    if st.session_state.step == 1:
        step_photo()
    else:
        step_recipes()


main()
