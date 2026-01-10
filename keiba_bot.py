import time
import json
import re
import requests
import streamlit as st
import streamlit.components.v1 as components
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# ==================================================
# 【設定エリア】secretsから読み込み
# ==================================================
KEIBA_ID = st.secrets.get("KEIBA_ID", "")
KEIBA_PASS = st.secrets.get("KEIBA_PASS", "")
DIFY_API_KEY = st.secrets.get("DIFY_API_KEY", "")

# デフォルト設定 (グローバル変数)
YEAR = "2026"
KAI = "01"
PLACE = "05"  # 中山
DAY = "03"

BASE_URL = "https://s.keibabook.co.jp"

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}

# 競馬ブック PLACEコード → netkeiba 競馬場コード
KEIBABOOK_TO_NETKEIBA_PLACE = {
    "08": "01", "09": "02", "06": "03", "07": "04", "04": "05",
    "05": "06", "02": "07", "00": "08", "01": "09", "03": "10",
}

# ==================================================
# 【追加】外部呼び出し用 パラメータ設定・取得関数
# ==================================================
def set_race_params(year, kai, place, day):
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)

def get_current_params():
    return YEAR, KAI, PLACE, DAY

# ==================================================
# ユーティリティ
# ==================================================
def _clean_text_ja(s: str) -> str:
    """全角スペース除去・空白正規化"""
    if not s:
        return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _is_missing_marker(s: str) -> bool:
    """情報なしマーカー判定"""
    t = _clean_text_ja(s)
    return t in {"－", "-", "—", "―", "‐", ""}

def render_copy_button(text: str, label: str, dom_id: str):
    """コピーボタン表示"""
    safe_text = json.dumps(text)
    html = f"""
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <button id="{dom_id}" style="
        padding:8px 12px;
        border-radius:10px;
        border:1px solid #ddd;
        background:#fff;
        cursor:pointer;
        font-size:14px;
      ">{label}</button>
      <span id="{dom_id}-msg" style="font-size:12px; color:#666;"></span>
    </div>
    <script>
      (function() {{
        const btn = document.getElementById("{dom_id}");
        const msg = document.getElementById("{dom_id}-msg");
        if (!btn) return;
        btn.addEventListener("click", async () => {{
          try {{
            await navigator.clipboard.writeText({safe_text});
            msg.textContent = "コピーしました";
            setTimeout(() => msg.textContent = "", 1200);
          }} catch (e) {{
            msg.textContent = "コピー失敗";
            setTimeout(() => msg.textContent = "", 2200);
          }}
        }});
      }})();
    </script>
    """
    components.html(html, height=54)

# ==================================================
# Selenium Setup
# ==================================================
def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,2200")
    options.add_argument("--lang=ja-JP")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver

def login_keibabook(driver: webdriver.Chrome) -> None:
    if not KEIBA_ID or not KEIBA_PASS:
        # secretsがない場合のフォールバック（必要に応じて）
        pass 
    driver.get(f"{BASE_URL}/login/login")
    try:
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.NAME, "login_id"))
        ).send_keys(KEIBA_ID)
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
        ).send_keys(KEIBA_PASS)
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'], .btn-login"))
        ).click()
        time.sleep(1.0)
    except:
        pass # 既にログイン済み等の場合

# ==================================================
# 【修正版】競馬ブック：厩舎の話 (Danwa)
# 課題①：馬名が取得できない → HTML構造に合わせて厳密に取得
# ==================================================
def parse_race_info_from_danwa(html: str) -> dict:
    """レース基本情報の取得"""
    soup = BeautifulSoup(html, "html.parser")
    racetitle = soup.find("div", class_="racetitle")
    if not racetitle:
        return {"header_text": ""}

    racemei = racetitle.find("div", class_="racemei")
    header_parts = []
    if racemei:
        for p in racemei.find_all("p"):
            header_parts.append(p.get_text(strip=True))

    racetitle_sub = racetitle.find("div", class_="racetitle_sub")
    if racetitle_sub:
        for p in racetitle_sub.find_all("p"):
            header_parts.append(p.get_text(strip=True))
    
    return {"header_text": "\n".join(header_parts)}

def parse_danwa_horses(html: str) -> dict:
    """
    返り値: { "1": {"name": "ロードオールライト", "danwa": "コメント..."}, ... }
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "danwa" in str(c))
    if not table or not table.tbody:
        return {}

    horses = {}
    current_umaban = None
    
    # trを行ごとに走査
    rows = table.tbody.find_all("tr", recursive=False)
    
    for tr in rows:
        classes = tr.get("class", [])
        if "spacer" in classes:
            continue

        # --- 馬情報の行 (馬番と馬名がある) ---
        umaban_td = tr.find("td", class_="umaban")
        
        # 馬名は class="left" または class="left bamei" にある
        # find("td", class_="left") は class="left bamei" もヒットする(BeautifulSoup仕様)
        bamei_td = tr.find("td", class_="left") 

        if umaban_td and bamei_td:
            # 馬番取得
            raw_umaban = umaban_td.get_text(strip=True)
            current_umaban = re.sub(r"\D", "", raw_umaban)
            
            # 馬名取得 (aタグがある場合とない場合に対応)
            anchor = bamei_td.find("a")
            if anchor:
                raw_name = anchor.get_text(strip=True)
            else:
                raw_name = bamei_td.get_text(strip=True)
            
            clean_name = _clean_text_ja(raw_name)
            
            if current_umaban:
                horses[current_umaban] = {"name": clean_name, "danwa": ""}
            continue

        # --- コメントの行 (class="danwa"がある) ---
        danwa_td = tr.find("td", class_="danwa")
        if danwa_td and current_umaban:
            # 全テキストを取得し整形
            comment_text = danwa_td.get_text("\n", strip=True)
            comment_text = _clean_text_ja(comment_text)
            
            if horses[current_umaban]["danwa"]: 
                # 万が一複数行に分かれていた場合の結合
                horses[current_umaban]["danwa"] += (" " + comment_text)
            else:
                horses[current_umaban]["danwa"] = comment_text

    return horses

def fetch_keibabook_danwa(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa"))
        )
    except:
        pass
    html = driver.page_source
    return parse_race_info_from_danwa(html), parse_danwa_horses(html)


# ==================================================
# 【修正版】競馬ブック：前走インタビュー (Syoin)
# 課題②：コメントがあるものだけ抽出したい
# 解決策：div.syoindata（メタ情報）を除去してからテキスト判定を行う
# ==================================================
def parse_zenkoso_interview(html: str) -> dict:
    """
    返り値: { "2": "バレンタインガール（１着）内田博騎手...", ... }
    ※ コメントがない馬（－表記など）は辞書に含まない
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "syoin" in str(c))
    if not table or not table.tbody:
        return {}

    interview_data = {}
    current_umaban = None

    rows = table.tbody.find_all("tr", recursive=False)
    
    for tr in rows:
        classes = tr.get("class", [])
        if "spacer" in classes:
            continue

        # --- 馬情報の行 ---
        umaban_td = tr.find("td", class_="umaban")
        if umaban_td:
            raw_u = umaban_td.get_text(strip=True)
            current_umaban = re.sub(r"\D", "", raw_u)
            continue

        # --- インタビュー内容の行 ---
        syoin_td = tr.find("td", class_="syoin")
        if syoin_td and current_umaban:
            # 不要なメタ情報 (div.syoindata) を特定して削除
            meta_div = syoin_td.find("div", class_="syoindata")
            if meta_div:
                meta_div.decompose() # divタグとその中身を完全削除

            # 残ったテキストを取得（これが純粋なコメント部分）
            raw_text = syoin_td.get_text(" ", strip=True)
            clean_text = _clean_text_ja(raw_text)

            # 「－」や空文字の場合はスキップ
            if not _is_missing_marker(clean_text) and len(clean_text) > 1:
                interview_data[current_umaban] = clean_text
            
            # 馬番リセット（次の行のために）
            current_umaban = None

    return interview_data

def fetch_zenkoso_interview(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syoin/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.syoin"))
        )
    except:
        pass
    return parse_zenkoso_interview(driver.page_source)


# ==================================================
# 競馬ブック：CPU予想 (そのまま利用)
# ==================================================
def parse_keibabook_cpu(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    # スピード指数
    speed_tbl = soup.find("table", id="cpu_speed_sort_table")
    if speed_tbl and speed_tbl.tbody:
        for tr in speed_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban: continue
            
            tds = tr.find_all("td")
            if len(tds) < 8: continue
            
            # 指数取得関数（数値以外は0）
            def get_v(idx):
                p = tds[idx].find("p")
                txt = re.sub(r"\D", "", p.get_text(strip=True)) if p else ""
                val = int(txt) if txt else 0
                return val if val < 900 else 0 # 1000などは除外

            last = get_v(-1)
            two = get_v(-2)
            thr = get_v(-3)
            vals = [x for x in [last, two, thr] if x > 0]
            avg = round(sum(vals)/len(vals)) if vals else 0
            
            data[umaban] = {
                "sp_last": str(last) if last else "-",
                "sp_2": str(two) if two else "-",
                "sp_3": str(thr) if thr else "-",
                "sp_avg": str(avg) if avg else "-"
            }

    # ファクター
    factor_tbl = None
    for t in soup.find_all("table"):
        c = t.find("caption")
        if c and "ファクター" in c.get_text():
            factor_tbl = t
            break
            
    if factor_tbl and factor_tbl.tbody:
        for tr in factor_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban: continue
            
            tds = tr.find_all("td")
            if len(tds) < 8: continue
            
            def get_m(idx):
                p = tds[idx].find("p")
                t = p.get_text(strip=True) if p else ""
                return t if t else "-"

            if umaban not in data: data[umaban] = {}
            data[umaban].update({
                "fac_crs": get_m(5), "fac_dis": get_m(6), "fac_zen": get_m(7)
            })

    return data

def fetch_keibabook_cpu_data(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/cpu/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "cpu_speed_sort_table")))
    except: pass
    return parse_keibabook_cpu(driver.page_source)


# ==================================================
# Netkeiba (既存機能維持)
# ==================================================
def fetch_netkeiba_data(driver, year, kai, place, day, race_num):
    # 簡易版：ID変換して戦績ページへ
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place: return {}
    
    nk_race_id = f"{year}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={nk_race_id}&rf=shutuba_submenu"
    
    driver.get(url)
    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "Shutuba_Past5_Table")))
    except:
        return {}
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    
    # 馬柱テーブルから騎手と過去走を取得
    rows = soup.find_all("tr", class_="HorseList")
    for tr in rows:
        # 馬番
        waku_tds = tr.find_all("td", class_="Waku")
        # 枠番列と馬番列があるため、数字が入っている方を探す
        umaban = ""
        for td in waku_tds:
            txt = re.sub(r"\D", "", td.get_text(strip=True))
            if txt:
                umaban = txt
                break
        if not umaban: continue

        # 騎手
        jockey_td = tr.find("td", class_="Jockey")
        jockey = _clean_text_ja(jockey_td.get_text(strip=True)) if jockey_td else "不明"
        jockey = re.sub(r"\d+.*", "", jockey) # 斤量カット

        # 過去走 (Pastクラスのtdを収集)
        past_tds = tr.find_all("td", class_="Past")
        past_list = []
        for td in past_tds[:3]: # 最新3走
            # 開催日、レース名、着順などを簡易取得
            txt = _clean_text_ja(td.get_text(" ", strip=True))
            if len(txt) > 5:
                # 簡易的な抽出: 日付と着順を目印に
                past_list.append(txt[:50] + "...") # 長すぎるのでカット
            else:
                past_list.append("-")
        
        data[umaban] = {"jockey": jockey, "past": past_list}
        
    return data


# ==================================================
# Dify Streaming
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY:
        yield "⚠️ DIFY_API_KEY 未設定"
        return

    payload = {
        "inputs": {"text": full_text},
        "response_mode": "streaming",
        "user": "keiba-bot",
    }
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"}
    
    try:
        res = requests.post("https://api.dify.ai/v1/workflows/run", headers=headers, json=payload, stream=True)
        for line in res.iter_lines():
            if not line: continue
            decoded = line.decode("utf-8").replace("data: ", "")
            try:
                data = json.loads(decoded)
                event = data.get("event")
                if event == "workflow_finished":
                    outputs = data.get("data", {}).get("outputs", {})
                    # Difyの出力キーに合わせて調整してください (例: 'result', 'text' etc)
                    for val in outputs.values():
                        if isinstance(val, str): yield val
                if event == "message" or "answer" in data:
                    yield data.get("answer", "")
            except: pass
    except Exception as e:
        yield f"Error: {e}"


# ==================================================
# Main Execution (App側から呼ばれる場合にも対応)
# ==================================================
def run_all_races(target_races=None):
    # StreamlitのUI表示があるため、app.pyから呼ぶ場合は
    # st.*** の呼び出し先がapp.pyのコンテキストになる
    
    # 引数 target_races があればそれだけ実行
    race_nums = target_races if target_races else list(range(1, 13))
    race_nums = [int(r) for r in race_nums]
    
    base_id = f"{YEAR}{KAI}{PLACE}{DAY}" 
    
    driver = build_driver()
    try:
        st.info(f"ログイン中... (ID: {KEIBA_ID[:2]}**)")
        login_keibabook(driver)
        st.success("ログイン成功")
        
        combined_text = ""

        for r in race_nums:
            race_num_str = f"{r:02}"
            race_id = base_id + race_num_str
            
            st.markdown(f"### {PLACE_NAMES.get(PLACE, '場')} {r}R")
            status = st.empty()
            status.text("データ収集中...")

            # 1. 厩舎の話 (基本情報 + コメント)
            header_info, danwa_data = fetch_keibabook_danwa(driver, race_id)
            if not danwa_data:
                st.error("馬データが見つかりませんでした (厩舎の話ページ取得失敗)")
                continue

            # 2. CPU予想
            cpu_data = fetch_keibabook_cpu_data(driver, race_id)

            # 3. 前走インタビュー
            interview_data = fetch_zenkoso_interview(driver, race_id)

            # 4. Netkeiba (騎手・戦績)
            nk_data = fetch_netkeiba_data(driver, YEAR, KAI, PLACE, DAY, race_num_str)

            # --- データ統合 ---
            lines = []
            # 馬番順にソート
            for umaban in sorted(danwa_data.keys(), key=int):
                d_info = danwa_data[umaban]
                c_info = cpu_data.get(umaban, {})
                i_text = interview_data.get(umaban, "なし")
                n_info = nk_data.get(umaban, {})

                # 戦績テキスト
                past_list = n_info.get("past", [])
                past_str = " / ".join(past_list) if past_list else "情報なし"
                
                # 指数テキスト
                cpu_str = (f"指数(前/2/3/平):{c_info.get('sp_last','-')}/{c_info.get('sp_2','-')}/"
                           f"{c_info.get('sp_3','-')}/{c_info.get('sp_avg','-')} "
                           f"F(コ/距/前):{c_info.get('fac_crs','-')}/{c_info.get('fac_dis','-')}/{c_info.get('fac_zen','-')}")

                line = (
                    f"▼馬番{umaban} {d_info['name']} (騎手:{n_info.get('jockey','-')})\n"
                    f"【厩舎の話】{d_info['danwa']}\n"
                    f"【前走インタビュー】{i_text}\n"
                    f"【データ】{cpu_str}\n"
                    f"【近走】{past_str}\n"
                )
                lines.append(line)

            full_prompt = (
                f"■レース情報\n{header_info['header_text']}\n\n"
                f"■各馬詳細\n" + "\n".join(lines)
            )

            # AI生成
            status.text("AI分析中...")
            result_area = st.empty()
            ai_output = ""
            for chunk in stream_dify_workflow(full_prompt):
                ai_output += chunk
                result_area.markdown(ai_output + "▌")
            result_area.markdown(ai_output)
            
            combined_text += f"\n\n--- {r}R ---\n{ai_output}"
            
            # コピーボタン
            render_copy_button(ai_output, f"{r}R コピー", f"copy_btn_{r}")
            status.success("完了")

        st.subheader("全レースまとめ")
        render_copy_button(combined_text, "全レースコピー", "copy_btn_all")
        st.text_area("出力結果", combined_text, height=300)

    finally:
        driver.quit()

if __name__ == "__main__":
    st.title("🏇 競馬AI予想データ生成")
    run_all_races()
