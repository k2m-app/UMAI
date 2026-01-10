import streamlit as st
import keiba_bot

# ==================================================
# App config
# ==================================================
st.set_page_config(
    page_title="JRA予想：UMAI",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 軽量CSS（スマホ最適化：ボタン/余白/チェック押しやすさ）
st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; padding-bottom: 2rem; }
      /* ボタンを押しやすく */
      .stButton > button { width: 100%; padding: 0.8rem 1rem; font-size: 1.02rem; border-radius: 14px; }
      /* ラジオ/チェック周り */
      label { font-size: 0.98rem !important; }
      /* サイドバーも押しやすく */
      section[data-testid="stSidebar"] .stButton > button { padding: 0.7rem 0.9rem; }
      /* 情報ボックスの余白 */
      div[data-testid="stAlert"] { border-radius: 14px; }
    </style>
    """,
    unsafe_allow_html=True
)

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}

# ==================================================
# Session state init
# ==================================================
if "selected_races" not in st.session_state:
    st.session_state.selected_races = set()

if "meet_candidates" not in st.session_state:
    st.session_state.meet_candidates = []

if "combined_output" not in st.session_state:
    st.session_state.combined_output = ""

# チェック状態の初期化（value= を渡さず、stateのみ）
for i in range(1, 13):
    k = f"race_{i}"
    if k not in st.session_state:
        st.session_state[k] = False


# ==================================================
# Helpers
# ==================================================
def sync_selected_races_from_checks():
    st.session_state.selected_races = {i for i in range(1, 13) if st.session_state.get(f"race_{i}", False)}

def set_all_races():
    for i in range(1, 13):
        st.session_state[f"race_{i}"] = True
    sync_selected_races_from_checks()

def clear_all_races():
    for i in range(1, 13):
        st.session_state[f"race_{i}"] = False
    sync_selected_races_from_checks()

def set_races_preset(preset: str):
    """スマホ向け：よく使う範囲だけサクッと選ぶ"""
    clear_all_races()
    if preset == "1-4":
        for i in range(1, 5):
            st.session_state[f"race_{i}"] = True
    elif preset == "5-8":
        for i in range(5, 9):
            st.session_state[f"race_{i}"] = True
    elif preset == "9-12":
        for i in range(9, 13):
            st.session_state[f"race_{i}"] = True
    sync_selected_races_from_checks()


# ==================================================
# Sidebar
# ==================================================
st.sidebar.title("JRA予想：UMAI")
st.sidebar.caption("手順：①開催候補取得 → ②開催設定 → ③レース選択 → ④実行")

with st.sidebar.expander("① 直近の開催候補（複数場）", expanded=True):
    # 連打で何度も走らないよう、ボタン押下時だけ実行
    if st.button("📌 候補を取得", key="btn_fetch_candidates"):
        with st.spinner("KeibaBookへログインして開催候補を検出中..."):
            candidates = keiba_bot.auto_detect_meet_candidates()
        st.session_state.meet_candidates = candidates or []
        if st.session_state.meet_candidates:
            st.success(f"候補 {len(st.session_state.meet_candidates)} 件を検出")
        else:
            st.error("開催候補を検出できませんでした（導線なし/ページ構造変更等）。")

    if st.session_state.meet_candidates:
        def fmt(c):
            return f"{c['year']}年 {c['kai']}回 {c['place_name']} {c['day']}日目（{c['meet10']}）"

        selected = st.selectbox(
            "検出された開催から選択",
            options=st.session_state.meet_candidates,
            format_func=fmt,
            key="sb_meet_select"
        )

        if st.button("✅ この開催を採用", key="btn_apply_meet"):
            keiba_bot.set_race_params(selected["year"], selected["kai"], selected["place"], selected["day"])
            st.success(f"採用: {fmt(selected)}")

with st.sidebar.expander("② 開催パラメータ（手動）", expanded=False):
    cur_year, cur_kai, cur_place, cur_day = keiba_bot.get_current_params()

    # 誤入力しづらいUI（軽量＆安定）
    year = st.text_input("年 (YYYY)", value=str(cur_year), key="in_year")
    kai = st.text_input("回 (2桁)", value=str(cur_kai).zfill(2), key="in_kai")
    place = st.selectbox(
        "競馬場",
        options=list(PLACE_NAMES.keys()),
        index=list(PLACE_NAMES.keys()).index(cur_place) if cur_place in PLACE_NAMES else 0,
        format_func=lambda x: f"{x} : {PLACE_NAMES.get(x,'?')}",
        key="in_place"
    )
    day = st.text_input("日 (2桁)", value=str(cur_day).zfill(2), key="in_day")

    if st.button("✅ 手動設定を反映", key="btn_apply_manual"):
        keiba_bot.set_race_params(year, kai, place, day)
        st.success("開催パラメータを反映しました。")


# ==================================================
# Main
# ==================================================
st.title("JRA予想：UMAI")

y, k, p, d = keiba_bot.get_current_params()
place_name = PLACE_NAMES.get(p, "不明")
st.info(f"現在の開催：{y}年 {k}回 {place_name} {d}日目")

st.divider()

# --- レース選択（スマホでも押しやすい）
st.subheader("レース選択（1〜12R）")

# プリセット（スマホ向け）
preset_col1, preset_col2, preset_col3, preset_col4 = st.columns(4)
with preset_col1:
    if st.button("✅ 全部", key="btn_all"):
        set_all_races()
with preset_col2:
    if st.button("🧹 解除", key="btn_clear"):
        clear_all_races()
with preset_col3:
    if st.button("1-4R", key="btn_1_4"):
        set_races_preset("1-4")
with preset_col4:
    if st.button("9-12R", key="btn_9_12"):
        set_races_preset("9-12")

# チェック配置：PCは6列、スマホは自動的に縦に潰れるのでOK
grid = st.columns(6)
for i in range(1, 13):
    with grid[(i - 1) % 6]:
        st.checkbox(f"{i}R", key=f"race_{i}")

sync_selected_races_from_checks()

st.divider()

run_mode = st.radio(
    "実行モード",
    options=["選択レースだけ実行", "全レース実行（1〜12）"],
    index=0,
    horizontal=True,
    key="run_mode",
)

# 実行ボタンは最下部に大きく1つ
run_clicked = st.button("🚀 実行開始", type="primary", key="btn_run")

if run_clicked:
    st.session_state["combined_output"] = ""  # 前回結果クリア

    y, k, p, d = keiba_bot.get_current_params()
    place_name = PLACE_NAMES.get(p, "不明")
    st.info(f"実行対象：{y}年 {k}回 {place_name} {d}日目")

    if run_mode == "全レース実行（1〜12）":
        keiba_bot.run_all_races(target_races=None)
    else:
        if not st.session_state.selected_races:
            st.warning("レース未選択です。少なくとも1つチェックしてください。")
        else:
            keiba_bot.run_all_races(target_races=st.session_state.selected_races)

# まとめ表示（keiba_bot が session_state["combined_output"] を埋める想定）
if st.session_state.get("combined_output", "").strip():
    st.divider()
    st.subheader("📌 まとめ出力")
    st.text_area(
        "全レースまとめ（閲覧用）",
        value=st.session_state["combined_output"],
        height=420,
        key="ta_combined",
    )
