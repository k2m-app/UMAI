import streamlit as st
import keiba_bot
from datetime import datetime

# ==================================================
# App config
# ==================================================
st.set_page_config(
    page_title="JRA予想：UMAI (Multi)",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 軽量CSS
st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; padding-bottom: 2rem; }
      .stButton > button { width: 100%; padding: 0.6rem; border-radius: 8px; }
      label { font-size: 0.9rem !important; }
      /* タブの見た目調整 */
      .stTabs [data-baseweb="tab-list"] { gap: 8px; }
      .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 4px 4px 0 0; gap: 1px; padding-top: 10px; padding-bottom: 10px; }
      .stTabs [aria-selected="true"] { background-color: #ffffff; border-top: 2px solid #ff4b4b; }
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
# 3場分の設定を保持するための初期化
MAX_VENUES = 3

if "combined_output" not in st.session_state:
    st.session_state.combined_output = ""

# デフォルト値（今日の日付などから推測、あるいは固定）
today = datetime.now()
default_year = str(today.year)

# 各会場の設定保存用State初期化
for v_idx in range(MAX_VENUES):
    prefix = f"v{v_idx}"
    if f"{prefix}_active" not in st.session_state:
        st.session_state[f"{prefix}_active"] = (v_idx == 0) # 最初だけActive
    if f"{prefix}_year" not in st.session_state:
        st.session_state[f"{prefix}_year"] = default_year
    if f"{prefix}_kai" not in st.session_state:
        st.session_state[f"{prefix}_kai"] = "01"
    if f"{prefix}_place" not in st.session_state:
        st.session_state[f"{prefix}_place"] = "05" # Default 中山
    if f"{prefix}_day" not in st.session_state:
        st.session_state[f"{prefix}_day"] = "01"
    
    # レース選択初期化
    for r in range(1, 13):
        rk = f"{prefix}_r{r}"
        if rk not in st.session_state:
            st.session_state[rk] = False

# ==================================================
# Helper Functions
# ==================================================
def set_preset(v_idx, mode):
    prefix = f"v{v_idx}"
    for r in range(1, 13):
        key = f"{prefix}_r{r}"
        if mode == "all":
            st.session_state[key] = True
        elif mode == "clear":
            st.session_state[key] = False
        elif mode == "1-6":
            st.session_state[key] = (r <= 6)
        elif mode == "7-12":
            st.session_state[key] = (r >= 7)

# ==================================================
# Main UI
# ==================================================
st.title("JRA予想：UMAI Multi-Venue")
st.caption("最大3つの開催場を一括設定し、連続で予想を実行します。")

# タブで会場切り替え
tabs = st.tabs([f"開催設定 {i+1}" for i in range(MAX_VENUES)])

jobs_config = [] # 実行時に渡す設定リスト

for v_idx, tab in enumerate(tabs):
    prefix = f"v{v_idx}"
    with tab:
        # アクティブ切り替え
        is_active = st.toggle(f"この開催（開催設定{v_idx+1}）を有効にする", key=f"{prefix}_active", value=(v_idx==0))
        
        if is_active:
            col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1, 1, 1])
            with col_p1:
                st.text_input("年", key=f"{prefix}_year")
            with col_p2:
                st.selectbox("回", [f"{i:02}" for i in range(1, 7)], key=f"{prefix}_kai")
            with col_p3:
                # keyからindex逆算
                opts = list(PLACE_NAMES.keys())
                curr = st.session_state[f"{prefix}_place"]
                idx = opts.index(curr) if curr in opts else 0
                st.selectbox("場所", opts, format_func=lambda x: f"{x}:{PLACE_NAMES[x]}", index=idx, key=f"{prefix}_place")
            with col_p4:
                st.selectbox("日目", [f"{i:02}" for i in range(1, 15)], key=f"{prefix}_day")
            
            st.markdown("---")
            st.caption("▼ 対象レース選択")
            
            # プリセットボタン
            bc1, bc2, bc3, bc4 = st.columns(4)
            if bc1.button("全選択", key=f"btn_all_{v_idx}"): set_preset(v_idx, "all")
            if bc2.button("クリア", key=f"btn_clr_{v_idx}"): set_preset(v_idx, "clear")
            if bc3.button("1-6R", key=f"btn_frst_{v_idx}"): set_preset(v_idx, "1-6")
            if bc4.button("7-12R", key=f"btn_last_{v_idx}"): set_preset(v_idx, "7-12")

            # チェックボックスグリッド
            chk_cols = st.columns(6)
            selected_races = []
            for r in range(1, 13):
                is_checked = st.checkbox(f"{r}R", key=f"{prefix}_r{r}")
                if is_checked:
                    selected_races.append(r)
            
            # 設定情報まとめ
            place_name = PLACE_NAMES.get(st.session_state[f"{prefix}_place"], "不明")
            summary_text = f"設定中：{st.session_state[f'{prefix}_year']}年 {st.session_state[f'{prefix}_kai']}回 {place_name} {st.session_state[f'{prefix}_day']}日目 ({len(selected_races)}レース選択)"
            st.info(summary_text)

            # ジョブリストに追加
            if selected_races:
                jobs_config.append({
                    "year": st.session_state[f"{prefix}_year"],
                    "kai": st.session_state[f"{prefix}_kai"],
                    "place": st.session_state[f"{prefix}_place"],
                    "day": st.session_state[f"{prefix}_day"],
                    "races": selected_races,
                    "place_name": place_name
                })
        else:
            st.warning("この開催設定は無効化されています。")

st.divider()

# ==================================================
# Execution Block
# ==================================================
st.subheader("🚀 実行")

if not jobs_config:
    st.error("実行対象がありません。少なくとも1つの開催を有効にし、レースを選択してください。")
    btn_disabled = True
else:
    btn_disabled = False
    msg = "以下の内容で連続実行します：\n"
    for job in jobs_config:
        msg += f"- 【{job['place_name']}】 {job['day']}日目 : {job['races']}\n"
    st.text(msg)

if st.button("AI予想を開始する", type="primary", disabled=btn_disabled):
    st.session_state["combined_output"] = ""
    
    # バッチ実行呼び出し
    result_text = keiba_bot.run_batch_prediction(jobs_config)
    
    st.session_state["combined_output"] = result_text

# ==================================================
# Output Area
# ==================================================
if st.session_state["combined_output"]:
    st.divider()
    st.subheader("📌 統合出力結果")
    st.text_area(
        "全開催・全レースまとめ",
        value=st.session_state["combined_output"],
        height=600
    )
