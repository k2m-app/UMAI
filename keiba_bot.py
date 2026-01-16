import time
import json
import re
import math
import requests
import streamlit as st
import streamlit.components.v1 as components
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup, NavigableString

# ==================================================
# 【設定エリア】secretsから読み込み
# ==================================================
KEIBA_ID = st.secrets.get("KEIBA_ID", "")
KEIBA_PASS = st.secrets.get("KEIBA_PASS", "")
DIFY_API_KEY = st.secrets.get("DIFY_API_KEY", "")

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
# 馬場バイアス評価データ
# ==================================================
BABA_BIAS_DATA = {
    "中山ダート1200": {5: [6, 7, 8], 2: [5]},
    "中京ダート1400": {5: [6, 7, 8], 2: [3, 5]},
    "京都ダート1200": {5: [6, 7, 8]},
    "中山芝1200": {5: [1, 2, 3]},
    "阪神芝1600": {5: [1, 2, 3]},
    "阪神芝1400": {5: [1, 2, 3]},
    "阪神芝1200": {5: [1, 2, 3], 2: [4]},
    "函館芝1800": {5: [1, 2, 3]},
    "東京芝2000": {5: [5], 2: [1]},
    "新潟芝1000": {5: [7, 8], 3: [6]},
    "東京ダート1600": {5: [6, 8], 3: [7], 2: [5]},
    "東京芝1600": {5: [6, 8]},
    "札幌ダート1000": {5: [7, 8]},
    "阪神ダート1400": {5: [8], 3: [4, 6], 2: [4, 6]},
    "東京芝1400": {5: [8]},
    "京都芝1600内": {5: [6]},
    "中山ダート1800": {5: [7, 8], 2: [4, 5]},
    "中山芝2500": {5: [5], 3: [6, 8]},
    "中京芝1200": {5: [2, 3], 3: [1], 2: [4, 5]},
    "京都ダート1800": {5: [6]},
    "京都ダート1900": {5: [3]},
    "京都芝1200": {5: [7]},
    "京都芝2400": {5: [2, 4]},
    "小倉芝1200": {5: [7], 3: [8], 2: [6]},
    "新潟ダート1200": {5: [6, 7], 2: [4, 8]},
    "新潟芝1600": {5: [5, 7]},
    "東京ダート1400": {5: [6, 7], 3: [4, 8]},
    "阪神ダート1800": {5: [6, 7]},
    "阪神ダート1200": {5: [8], 3: [5, 6, 7], 2: [4]},
    "中京ダート1200": {3: [1, 6]},
    "中山芝1600": {5: [1], 3: [2, 3, 4]},
    "中京芝1400": {5: [3], 3: [1, 4]},
    "東京芝2400": {3: [1, 3]},
    "阪神芝1800": {5: [1, 3], 3: [2, 4]},
    "函館芝2000": {5: [2], 3: [1, 5], 2: [4, 6]},
    "札幌芝2000": {5: [1, 5], 3: [2, 3]},
    "札幌芝1200": {3: [1, 8], 2: [6, 7]},
}


# ==================================================
# ユーティリティ
# ==================================================
def _clean_text_ja(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_missing_marker(s: str) -> bool:
    t = _clean_text_ja(s)
    return t in {"－", "-", "—", "―", "‐", ""}


def render_copy_button(text: str, label: str, dom_id: str):
    safe_text = json.dumps(text)
    html = f"""
    <div style="margin:5px 0;">
    <button onclick="copyToClipboard_{dom_id}()" 
            style="padding:6px 12px; background:#4CAF50; color:white; border:none; 
                   border-radius:4px; cursor:pointer; font-size:12px;">
        {label}
    </button>
    </div>
    <script>
    function copyToClipboard_{dom_id}() {{
        const text = {safe_text};
        navigator.clipboard.writeText(text).then(() => {{
        }}).catch(err => {{
        }});
    }}
    </script>
    """
    components.html(html, height=40)


def _safe_int(s, default=0) -> int:
    try:
        if s is None:
            return default
        if isinstance(s, (int, float)):
            return int(s)
        ss = str(s).strip()
        if ss in {"", "-", "－"}:
            return default
        return int(ss)
    except:
        return default


# ==================================================
# スピード指数（基本能力の偏差値化）
# ==================================================
def compute_speed_metrics(cpu_data: dict, w_max: float = 2.0, w_last: float = 1.8, w_avg: float = 1.2) -> dict:
    """
    各馬のスピード指数（基本能力）を算出し、そのレース内での偏差値をスピード指数として返す。
    """
    raw_scores = {}
    for umaban, d in cpu_data.items():
        last = _safe_int(d.get("sp_last"), 0)
        two = _safe_int(d.get("sp_2"), 0)
        thr = _safe_int(d.get("sp_3"), 0)
        vals = [v for v in [last, two, thr] if v > 0]
        if not vals:
            continue
        
        avg = sum(vals) / len(vals)
        max_v = max(vals)
        
        # 重み付け基本能力計算
        denom = (w_max + w_last + w_avg)
        raw = (max_v * w_max + last * w_last + avg * w_avg) / denom
        raw_scores[umaban] = raw

    if not raw_scores:
        return {}

    values = list(raw_scores.values())
    mean = sum(values) / len(values)
    # 標準偏差
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) if len(values) > 1 else 0

    out = {}
    for umaban, raw in raw_scores.items():
        # 偏差値（T-Score）算出: 50 + 10 * (個々の値 - 平均) / 標準偏差
        if std == 0:
            hensachi = 50.0
        else:
            hensachi = 50.0 + 10.0 * (raw - mean) / std
        
        out[umaban] = {
            "raw_ability": round(raw, 2),
            "speed_index": round(hensachi, 1) # これを「スピード指数」として使用
        }

    return out


# ==================================================
# 馬場バイアス評価関数
# ==================================================
def extract_race_info(race_title: str) -> dict:
    result = {
        "place": None,
        "distance": None,
        "track_type": None,
        "day": None,
        "course_variant": ""
    }
    place_day_pattern = r'(\d+)回([^0-9]+?)(\d+)日目'
    place_day_match = re.search(place_day_pattern, race_title)
    if place_day_match:
        result["place"] = place_day_match.group(2).strip()
        result["day"] = int(place_day_match.group(3))
    distance_pattern = r'(\d{3,4})m'
    distance_match = re.search(distance_pattern, race_title)
    if distance_match:
        result["distance"] = distance_match.group(1)
    if 'ダート' in race_title:
        result["track_type"] = "dirt"
    elif '芝' in race_title:
        result["track_type"] = "turf"
    if '内' in race_title:
        result["course_variant"] = "内"
    elif '外' in race_title:
        result["course_variant"] = "外"
    return result


def calculate_baba_bias(waku: int, race_title: str) -> dict:
    kaisai_bias = 0
    course_bias = 0
    race_info = extract_race_info(race_title)
    place_name = race_info["place"]
    distance = race_info["distance"]
    track_type = race_info["track_type"]
    race_day = race_info["day"]
    course_variant = race_info["course_variant"]
    if track_type == "turf" and race_day in [1, 2]:
        if waku == 1: kaisai_bias = 5
        elif waku == 2: kaisai_bias = 3
        elif waku == 3: kaisai_bias = 2
    if place_name and distance and track_type:
        track_str = "芝" if track_type == "turf" else "ダート"
        course_key = f"{place_name}{track_str}{distance}{course_variant}"
        if course_key in BABA_BIAS_DATA:
            bias_data = BABA_BIAS_DATA[course_key]
            for points in [5, 3, 2]:
                if points in bias_data and waku in bias_data[points]:
                    course_bias = points
                    break
    return {
        "kaisai_bias": kaisai_bias,
        "course_bias": course_bias,
        "total": kaisai_bias + course_bias
    }


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
        return
    driver.get(f"{BASE_URL}/login/login")
    try:
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.NAME, "login_id"))).send_keys(KEIBA_ID)
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))).send_keys(KEIBA_PASS)
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'], .btn-login"))).click()
        time.sleep(1.0)
    except:
        pass


# ==================================================
# 競馬ブック各ページ解析
# ==================================================
def fetch_keibabook_danwa(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa")))
    except: pass
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    # ヘッダー取得
    racetitle = soup.find("div", class_="racetitle")
    header_parts = []
    if racetitle:
        for p in racetitle.find_all("p"):
            header_parts.append(p.get_text(strip=True))
    header_info = {"header_text": "\n".join(header_parts)}
    
    # 馬データ取得
    table = soup.find("table", class_=lambda c: c and "danwa" in str(c))
    horses = {}
    if table and table.tbody:
        current_umaban = None
        current_waku = None
        for tr in table.tbody.find_all("tr", recursive=False):
            if "spacer" in tr.get("class", []): continue
            waku_td = tr.find("td", class_="waku")
            umaban_td = tr.find("td", class_="umaban")
            bamei_td = tr.find("td", class_="left")
            if waku_td and umaban_td and bamei_td:
                waku_p = waku_td.find("p")
                if waku_p:
                    for cls in waku_p.get("class", []):
                        if cls.startswith("waku"):
                            current_waku = re.sub(r"\D", "", cls)
                            break
                current_umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
                clean_name = _clean_text_ja(bamei_td.get_text(strip=True))
                horses[current_umaban] = {"name": clean_name, "waku": current_waku or "?", "danwa": ""}
                continue
            danwa_td = tr.find("td", class_="danwa")
            if danwa_td and current_umaban:
                txt = _clean_text_ja(danwa_td.get_text("\n", strip=True))
                horses[current_umaban]["danwa"] = (horses[current_umaban]["danwa"] + " " + txt).strip()
    return header_info, horses


def fetch_keibabook_chokyo(driver, race_id: str):
    """
    スマート版のHTML構造に対応した調教データ取得関数（修正版）
    """
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cyokyo")))
    except:
        pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}

    # HTML内には "table.cyokyo" が複数存在する（馬ごとのブロック）
    tables = soup.find_all("table", class_="cyokyo")

    for tbl in tables:
        # 1. 馬番の取得
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td:
            continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))

        # 2. 短評の取得
        tanpyo_td = tbl.find("td", class_="tanpyo")
        tanpyo = _clean_text_ja(tanpyo_td.get_text(strip=True)) if tanpyo_td else "なし"

        # 3. 詳細データの取得
        # 詳細データは colspan="5" のセルの中に、<dl class="dl-table"> と <table class="cyokyodata"> が交互に入っている
        detail_cell = tbl.find("td", colspan="5")
        details_text_parts = []

        if detail_cell:
            current_header_info = ""
            
            # 子要素を順番に処理して、dl(ヘッダー)とtable(タイム)を紐付ける
            for child in detail_cell.children:
                if isinstance(child, NavigableString):
                    continue
                
                # ヘッダー情報（日付、コース、助手、強さなど）
                if child.name == 'dl' and 'dl-table' in child.get('class', []):
                    # dtタグの中身を結合: 例 "助手", "1/7 美Ｗ 良", "強めに追う"
                    dt_texts = [dt.get_text(" ", strip=True) for dt in child.find_all('dt')]
                    current_header_info = " ".join([t for t in dt_texts if t])

                # タイム情報
                elif child.name == 'table' and 'cyokyodata' in child.get('class', []):
                    # タイム行
                    time_tr = child.find('tr', class_='time')
                    time_str = ""
                    if time_tr:
                        times = []
                        for td in time_tr.find_all('td'):
                            txt = td.get_text(strip=True)
                            if txt:
                                times.append(txt)
                        time_str = "-".join(times)

                    # 併せ馬情報
                    awase_tr = child.find('tr', class_='awase')
                    awase_str = ""
                    if awase_tr:
                        awase_txt = _clean_text_ja(awase_tr.get_text(strip=True))
                        if awase_txt:
                            awase_str = f" (併せ: {awase_txt})"
                    
                    # 情報を結合してリストに追加
                    if current_header_info or time_str:
                        # 例: [1/7 美Ｗ 良 助手 強めに追う] 85.6-69.1-54.3-39.8-12.8 [6] (併せ: ...)
                        details_text_parts.append(f"[{current_header_info}] {time_str}{awase_str}")
                    
                    # ヘッダー情報をリセット（次のdlが来るまで空にする）
                    current_header_info = ""

        # 全調教履歴を結合
        full_details = "\n".join(details_text_parts) if details_text_parts else "詳細なし"

        data[umaban] = {
            "tanpyo": tanpyo,
            "details": full_details
        }
        
    return data


def fetch_zenkoso_interview(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syoin/{race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.syoin")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    interview_data = {}
    table = soup.find("table", class_=lambda c: c and "syoin" in str(c))
    if table and table.tbody:
        current_umaban = None
        for tr in table.tbody.find_all("tr", recursive=False):
            umaban_td = tr.find("td", class_="umaban")
            if umaban_td:
                current_umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
                continue
            syoin_td = tr.find("td", class_="syoin")
            if syoin_td and current_umaban:
                meta = syoin_td.find("div", class_="syoindata")
                if meta: meta.decompose()
                txt = _clean_text_ja(syoin_td.get_text(" ", strip=True))
                if not _is_missing_marker(txt): interview_data[current_umaban] = txt
    return interview_data


def fetch_keibabook_cpu_data(driver, race_id: str, is_shinba: bool = False):
    url = f"{BASE_URL}/cyuou/cpu/{race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "main")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    speed_tbl = soup.find("table", id="cpu_speed_sort_table")
    if speed_tbl and speed_tbl.tbody:
        for tr in speed_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            tds = tr.find_all("td")
            if len(tds) < 8: continue
            def get_v(idx):
                p = tds[idx].find("p")
                txt = re.sub(r"\D", "", p.get_text(strip=True)) if p else ""
                val = int(txt) if txt else 0
                return val if val < 900 else 0
            data[umaban] = {"sp_last": get_v(-1), "sp_2": get_v(-2), "sp_3": get_v(-3)}
    
    # ファクター取得
    factor_tbl = None
    for t in soup.find_all("table"):
        cap = t.find("caption")
        if cap and "ファクター" in cap.get_text():
            factor_tbl = t; break
    if factor_tbl and factor_tbl.tbody:
        for tr in factor_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            tds = tr.find_all("td")
            if len(tds) < 6: continue
            def get_m(idx):
                p = tds[idx].find("p")
                return p.get_text(strip=True) if p else "-"
            if umaban not in data: data[umaban] = {}
            if is_shinba:
                data[umaban].update({"fac_deashi": get_m(5), "fac_kettou": get_m(6), "fac_ugoki": get_m(8)})
            else:
                data[umaban].update({"fac_crs": get_m(5), "fac_dis": get_m(6), "fac_zen": get_m(7)})
    return data


# ==================================================
# Netkeiba & 近走指数
# ==================================================
def fetch_netkeiba_data(driver, year, kai, place, day, race_num):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place: return {}
    nk_race_id = f"{year}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={nk_race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "Shutuba_Past5_Table")))
    except: return {}
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    for tr in soup.find_all("tr", class_="HorseList"):
        umaban_tds = tr.find_all("td", class_="Waku")
        umaban = ""
        for td in umaban_tds:
            txt = re.sub(r"\D", "", td.get_text(strip=True))
            if txt: umaban = txt; break
        if not umaban: continue
        
        jockey_td = tr.find("td", class_="Jockey")
        jockey = _clean_text_ja(jockey_td.get_text(strip=True)) if jockey_td else "不明"
        
        past_str_list = []
        valid_runs = []
        for td in tr.find_all("td", class_="Past")[:3]:
            if "Rest" in td.get("class", []):
                past_str_list.append("(放牧/休養)")
            else:
                d01 = td.find("div", class_="Data01")
                d02 = td.find("div", class_="Data02")
                rank_tag = td.find("span", class_="Num") or td.find("div", class_="Rank")
                rank = rank_tag.get_text(strip=True) if rank_tag else "?"
                txt = f"[{_clean_text_ja(d01.get_text() if d01 else '')} {rank}着]"
                past_str_list.append(txt)
                try:
                    r_int = int(re.sub(r"\D", "", rank))
                    valid_runs.append({"rank_int": r_int})
                except: pass
        
        # 簡易近走指数計算 (3着以内回数等)
        kinsou_idx = sum(3 for r in valid_runs if r["rank_int"] <= 3)
        data[umaban] = {"jockey": jockey, "past": past_str_list, "kinsou_index": float(min(kinsou_idx, 10))}
    return data


# ==================================================
# Dify Streaming
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY:
        yield "⚠️ DIFY_API_KEY 未設定"
        return
    payload = {"inputs": {"text": full_text}, "response_mode": "streaming", "user": "keiba-bot"}
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"}
    try:
        res = requests.post("https://api.dify.ai/v1/workflows/run", headers=headers, json=payload, stream=True, timeout=90)
        for line in res.iter_lines():
            if not line: continue
            decoded = line.decode("utf-8").replace("data: ", "")
            try:
                data = json.loads(decoded)
                if data.get("event") == "workflow_finished":
                    outputs = data.get("data", {}).get("outputs", {})
                    for val in outputs.values():
                        if isinstance(val, str): yield val
                elif "answer" in data:
                    yield data.get("answer", "")
            except: pass
    except Exception as e:
        yield f"Error: {e}"


# ==================================================
# Main Execution (Batch)
# ==================================================
def run_batch_prediction(jobs_config):
    full_output_log = ""
    for job_idx, job in enumerate(jobs_config):
        driver = build_driver()
        try:
            st.info(f"[{job_idx+1}/{len(jobs_config)}] ログイン処理中...")
            login_keibabook(driver)
            
            year = job["year"]
            kai = str(job["kai"]).zfill(2)
            place = str(job["place"]).zfill(2)
            day = str(job["day"]).zfill(2)
            place_name = job["place_name"]
            base_id = f"{year}{kai}{place}{day}"
            
            st.markdown(f"## 🏁 {place_name}開催")
            full_output_log += f"\n\n--- {place_name} ---\n"

            for r in sorted(job["races"]):
                race_num_str = f"{r:02}"
                race_id = base_id + race_num_str
                st.markdown(f"### {place_name} {r}R")
                status = st.empty()
                status.text("データ収集中...")
                
                header_info, danwa_data = fetch_keibabook_danwa(driver, race_id)
                if not danwa_data:
                    st.error(f"データ取得失敗: {race_id}")
                    continue
                
                race_title = header_info.get("header_text", "")
                is_shinba = any(x in race_title for x in ["新馬", "メイクデビュー"])
                
                cpu_data = fetch_keibabook_cpu_data(driver, race_id, is_shinba=is_shinba)
                # ★偏差値ベースのスピード指数計算
                speed_metrics = compute_speed_metrics(cpu_data)
                
                interview_data = fetch_zenkoso_interview(driver, race_id)
                chokyo_data = fetch_keibabook_chokyo(driver, race_id)
                nk_data = fetch_netkeiba_data(driver, year, kai, place, day, race_num_str)
                
                lines = []
                for umaban in sorted(danwa_data.keys(), key=int):
                    d = danwa_data[umaban]
                    sm = speed_metrics.get(umaban, {})
                    n = nk_data.get(umaban, {})
                    c = cpu_data.get(umaban, {})
                    k = chokyo_data.get(umaban, {"tanpyo": "-", "details": "-"})
                    bias = calculate_baba_bias(int(d["waku"]) if d["waku"].isdigit() else 0, race_title)
                    
                    # ★偏差値をそのまま表示
                    sp_val = sm.get("speed_index", "-")
                    sp_str = f"スピード指数(偏差値):{sp_val}"
                    
                    kinsou_idx = n.get("kinsou_index", 0.0)
                    fac_str = f"F:{c.get('fac_deashi','-')}/{c.get('fac_kettou','-')}" if is_shinba else f"F:{c.get('fac_crs','-')}/{c.get('fac_dis','-')}"
                    
                    line = (
                        f"▼{d['waku']}枠{umaban}番 {d['name']} (騎手:{n.get('jockey','-')})\n"
                        f"【データ】{sp_str} バイアス:{bias['total']} 近走指数:{kinsou_idx} {fac_str}\n"
                        f"【厩舎】{d['danwa']}\n"
                        f"【前走】{interview_data.get(umaban, 'なし')}\n"
                        f"【調教】{k['tanpyo']} \n{k['details']}\n"
                        f"【近走】{' / '.join(n.get('past', []))}\n"
                    )
                    lines.append(line)

                full_prompt = f"■レース情報\n{race_title}\n\n■各馬詳細\n" + "\n".join(lines)
                
                status.text("AI分析中...")
                result_area = st.empty()
                ai_output = ""
                for chunk in stream_dify_workflow(full_prompt):
                    ai_output += chunk
                    result_area.markdown(ai_output + "▌")
                result_area.markdown(ai_output)
                
                full_output_log += f"\n{race_title}\n{ai_output}\n"
                render_copy_button(ai_output, f"{r}Rコピー", f"cp_{base_id}_{r}")
                status.success("完了")
                
        except Exception as e:
            st.error(f"エラー: {e}")
        finally:
            driver.quit()
    return full_output_log

# Streamlit UI (簡易版呼び出し例)
if __name__ == "__main__":
    st.title("AI競馬予想システム (偏差値スピード指数版)")
