import streamlit as st
import keiba_bot

st.set_page_config(page_title="KeibaBook AI", layout="wide")

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}

# -----------------------------
# State 初期化
# -----------------------------
if "selected_races" not in st.session_state:
    st.session_state.selected_races = set()

if "meet_candidates" not in st.session_state:
    st.session_state.meet_candidates = []

# まとめ出力（keiba_bot側でセットされる）
if "combined_output" not in st.session_state:
    st.session_state.combined_output = ""

# race_1〜race_12 の初期化（ここでのみ初期値を作る）
for i in range(1, 13):
    k = f"race_{i}"
    if k not in st.session_state:
        st.session_state[k] = (i in st.session_state.selected_races)

def sync_selected_races_from_checks():
    """チェック状態 -> selected_races へ同期"""
    st.session_state.selected_races = {i for i in range(1, 13) if st.session_state[f"race_{i}"]}

def set_all_races():
    for i in range(1, 13):
        st.session_state[f"race_{i}"] = True
    sync_selected_races_from_checks()

def clear_all_races():
    for i in range(1, 13):
        st.session_state[f"race_{i}"] = False
    sync_selected_races_from_checks()

# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.title("設定")
st.sidebar.caption("1) 直近開催候補を取得 → 2) 開催選択 → 3) レース選択 → 4) 実行")

if st.sidebar.button("📌 直近の開催候補を取得（複数場対応）"):
    with st.spinner("Keibabookへログインして開催候補を検出中..."):
        candidates = keiba_bot.auto_detect_meet_candidates()

    if candidates:
        st.session_state.meet_candidates = candidates
        st.sidebar.success(f"候補 {len(candidates)}件を検出しました")
    else:
        st.session_state.meet_candidates = []
        st.sidebar.error("開催候補を検出できませんでした（導線なし/ページ構造変更等）。")

if st.session_state.meet_candidates:
    def fmt(c):
        return f"{c['year']}年 {c['kai']}回 {c['place_name']} {c['day']}日目（{c['meet10']}）"

    selected = st.sidebar.selectbox(
        "検出された開催から選択",
        options=st.session_state.meet_candidates,
        format_func=fmt
    )

    if st.sidebar.button("✅ この開催を採用"):
        keiba_bot.set_race_params(selected["year"], selected["kai"], selected["place"], selected["day"])
        st.sidebar.success(f"採用: {fmt(selected)}")

cur_year, cur_kai, cur_place, cur_day = keiba_bot.get_current_params()

st.sidebar.subheader("開催パラメータ（手動修正OK）")
year = st.sidebar.text_input("年 (YYYY)", value=cur_year)
kai = st.sidebar.text_input("回 (2桁)", value=cur_kai)
place = st.sidebar.selectbox(
    "競馬場",
    options=list(PLACE_NAMES.keys()),
    index=list(PLACE_NAMES.keys()).index(cur_place) if cur_place in PLACE_NAMES else 0,
    format_func=lambda x: f"{x} : {PLACE_NAMES.get(x,'?')}",
)
day = st.sidebar.text_input("日 (2桁)", value=cur_day)

if st.sidebar.button("✅ 手動設定を反映"):
    keiba_bot.set_race_params(year, kai, place, day)
    st.sidebar.success("開催パラメータを反映しました。")

# -----------------------------
# Main
# -----------------------------
st.title("KeibaBook AI（開催選択→レース選択→実行）")

y, k, p, d = keiba_bot.get_current_params()
place_name = PLACE_NAMES.get(p, "不明")
st.info(f"現在の開催：{y}年 {k}回 {place_name} {d}日目")

st.divider()

# レース選択 UI
colA, colB, colC = st.columns([1, 1, 2])

with colA:
    if st.button("✅ 全レース選択"):
        set_all_races()

with colB:
    if st.button("🧹 全解除"):
        clear_all_races()

with colC:
    st.caption("チェック状態は session_state のみで管理（value= を渡さない）のでエラーになりません。")

st.subheader("レース選択（1〜12R）")

grid = st.columns(6)
for i in range(1, 13):
    col = grid[(i - 1) % 6]
    with col:
        st.checkbox(f"{i}R", key=f"race_{i}")

# チェックボックス描画後に同期
sync_selected_races_from_checks()

st.divider()

run_mode = st.radio(
    "実行モード",
    options=["選択レースだけ実行", "全レース実行（1〜12）"],
    index=0,
    horizontal=True,
)

if st.button("🚀 実行開始", type="primary"):
    # 実行のたびに前回のまとめをクリア（表示が混ざるのを防ぐ）
    st.session_state["combined_output"] = ""

    y, k, p, d = keiba_bot.get_current_params()
    place_name = PLACE_NAMES.get(p, "不明")
    st.info(f"実行対象：{y}年 {k}回 {place_name} {d}日目")

    if run_mode == "全レース実行（1〜12）":
        keiba_bot.run_all_races(target_races=None)
    else:
        if not st.session_state.selected_races:
            st.warning("レースが未選択です。少なくとも1つチェックしてください。")
        else:
            keiba_bot.run_all_races(target_races=st.session_state.selected_races)
