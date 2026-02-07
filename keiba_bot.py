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

# 競馬ブック PLACEコード → netkeiba/Yahoo 競馬場コード (共通)
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
    if not s: return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _is_missing_marker(s: str) -> bool:
    t = _clean_text_ja(s)
    return t in {"－", "-", "—", "―", "‐", ""}

def _safe_int(s, default=0) -> int:
    try:
        if s is None: return default
        if isinstance(s, (int, float)): return int(s)
        ss = str(s).strip()
        ss = re.sub(r"[^0-9\-]", "", ss)
        if ss in {"", "-", "－"}: return default
        return int(ss)
    except: return default

def extract_distance_int(dist_str: str) -> int:
    match = re.search(r'(\d{3,4})', str(dist_str))
    if match: return int(match.group(1))
    return 0

def parse_dify_evaluation(ai_text: str) -> dict:
    """ DifyのMarkdownテーブルから {馬名: 評価ランク} の辞書を作成 """
    eval_map = {}
    pattern = r'\|\s*\d+\s*\|\s*([^|（\(]+)[^|]*\|\s*[^|]*\|\s*[^|]*\|\s*([SABCDEFG])\s*\|'
    matches = re.finditer(pattern, ai_text)
    for m in matches:
        name = m.group(1).strip()
        grade = m.group(2).strip()
        eval_map[name] = grade
    return eval_map

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

# ==================================================
# 計算・解析ロジック
# ==================================================
def compute_speed_metrics(cpu_data: dict, w_max: float = 2.0, w_last: float = 1.8, w_avg: float = 1.2) -> dict:
    raw_scores = {}
    for umaban, d in cpu_data.items():
        last = _safe_int(d.get("sp_last"), 0)
        two = _safe_int(d.get("sp_2"), 0)
        thr = _safe_int(d.get("sp_3"), 0)
        vals = [v for v in [last, two, thr] if v > 0]
        if not vals: continue
        avg = sum(vals) / len(vals)
        max_v = max(vals)
        denom = (w_max + w_last + w_avg)
        raw = (max_v * w_max + last * w_last + avg * w_avg) / denom
        raw_scores[umaban] = raw
    if not raw_scores: return {}
    max_raw = max(raw_scores.values())
    out = {}
    for umaban, raw in raw_scores.items():
        score_35 = (raw / max_raw) * 35.0 if max_raw > 0 else 0.0
        out[umaban] = {"raw_ability": round(raw, 2), "speed_index": round(score_35, 1)}
    return out

def extract_race_info(race_title: str) -> dict:
    result = {"place": None, "distance": None, "track_type": None, "day": None, "course_variant": ""}
    p_match = re.search(r'(\d+)回([^0-9]+?)(\d+)日目', race_title)
    if p_match:
        result["place"] = p_match.group(2).strip()
        result["day"] = int(p_match.group(3))
    d_match = re.search(r'(\d{3,4})m', race_title)
    if d_match: result["distance"] = d_match.group(1)
    if 'ダート' in race_title: result["track_type"] = "dirt"
    elif '芝' in race_title: result["track_type"] = "turf"
    if '内' in race_title: result["course_variant"] = "内"
    elif '外' in race_title: result["course_variant"] = "外"
    return result

def calculate_baba_bias(waku: int, race_title: str) -> dict:
    kaisai_bias, course_bias = 0, 0
    info = extract_race_info(race_title)
    if info["track_type"] == "turf" and info["day"] in [1, 2]:
        if waku == 1: kaisai_bias = 5
        elif waku == 2: kaisai_bias = 3
        elif waku == 3: kaisai_bias = 2
    if info["place"] and info["distance"] and info["track_type"]:
        track_str = "芝" if info["track_type"] == "turf" else "ダート"
        course_key = f"{info['place']}{track_str}{info['distance']}{info['course_variant']}"
        if course_key in BABA_BIAS_DATA:
            bias_data = BABA_BIAS_DATA[course_key]
            for points in [5, 3, 2]:
                if points in bias_data and waku in bias_data[points]:
                    course_bias = points; break
    return {"kaisai_bias": kaisai_bias, "course_bias": course_bias, "total": kaisai_bias + course_bias}

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
    options.page_load_strategy = 'eager'
    options.add_argument("--lang=ja-JP")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30) 
    return driver

def login_keibabook(driver: webdriver.Chrome) -> None:
    if not KEIBA_ID or not KEIBA_PASS: return
    driver.get(f"{BASE_URL}/login/login")
    try:
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.NAME, "login_id"))).send_keys(KEIBA_ID)
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))).send_keys(KEIBA_PASS)
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'], .btn-login"))).click()
        time.sleep(1.0)
    except: pass

# ==================================================
# スクレイピング関数の各機能
# ==================================================
def fetch_keibabook_danwa(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    racetitle = soup.find("div", class_="racetitle")
    header_info = {"header_text": "\n".join([p.get_text(strip=True) for p in racetitle.find_all("p")]) if racetitle else ""}
    table = soup.find("table", class_=lambda c: c and "danwa" in str(c))
    horses = {}
    if table and table.tbody:
        current_umaban, current_waku = None, None
        for tr in table.tbody.find_all("tr", recursive=False):
            if "spacer" in tr.get("class", []): continue
            waku_td, umaban_td, bamei_td = tr.find("td", class_="waku"), tr.find("td", class_="umaban"), tr.find("td", class_="left")
            if waku_td and umaban_td and bamei_td:
                waku_p = waku_td.find("p")
                if waku_p:
                    for cls in waku_p.get("class", []):
                        if cls.startswith("waku"): current_waku = re.sub(r"\D", "", cls); break
                current_umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
                horses[current_umaban] = {"name": _clean_text_ja(bamei_td.get_text(strip=True)), "waku": current_waku or "?", "danwa": ""}
                continue
            danwa_td = tr.find("td", class_="danwa")
            if danwa_td and current_umaban:
                txt = _clean_text_ja(danwa_td.get_text("\n", strip=True))
                horses[current_umaban]["danwa"] = (horses[current_umaban]["danwa"] + " " + txt).strip()
    return header_info, horses

def fetch_keibabook_chokyo(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cyokyo")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    for tbl in soup.find_all("table", class_="cyokyo"):
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td: continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
        tanpyo = _clean_text_ja(tbl.find("td", class_="tanpyo").get_text(strip=True)) if tbl.find("td", class_="tanpyo") else "なし"
        details_parts, detail_cell = [], tbl.find("td", colspan="5")
        if detail_cell:
            header_info = ""
            for child in detail_cell.children:
                if isinstance(child, NavigableString): continue
                if child.name == 'dl' and 'dl-table' in child.get('class', []):
                    header_info = " ".join([dt.get_text(" ", strip=True) for dt in child.find_all('dt')])
                elif child.name == 'table' and 'cyokyodata' in child.get('class', []):
                    time_tr, awase_tr = child.find('tr', class_='time'), child.find('tr', class_='awase')
                    time_str = "-".join([td.get_text(strip=True) for td in time_tr.find_all('td')]) if time_tr else ""
                    awase_str = f" (併せ: {_clean_text_ja(awase_tr.get_text(strip=True))})" if awase_tr else ""
                    if header_info or time_str: details_parts.append(f"[{header_info}] {time_str}{awase_str}")
                    header_info = ""
        data[umaban] = {"tanpyo": tanpyo, "details": "\n".join(details_parts) if details_parts else "詳細なし"}
    return data

def fetch_zenkoso_interview(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syoin/{race_id}"
    driver.get(url)
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.syoin")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    interview_data, table = {}, soup.find("table", class_=lambda c: c and "syoin" in str(c))
    if table and table.tbody:
        current_umaban = None
        for tr in table.tbody.find_all("tr", recursive=False):
            umaban_td = tr.find("td", class_="umaban")
            if umaban_td: current_umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True)); continue
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
    data, speed_tbl = {}, soup.find("table", id="cpu_speed_sort_table")
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
    factor_tbl = None
    for t in soup.find_all("table"):
        cap = t.find("caption")
        if cap and "ファクター" in cap.get_text(): factor_tbl = t; break
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
            if is_shinba: data[umaban].update({"fac_deashi": get_m(5), "fac_kettou": get_m(6), "fac_ugoki": get_m(8)})
            else: data[umaban].update({"fac_crs": get_m(5), "fac_dis": get_m(6), "fac_zen": get_m(7)})
    return data

# ==================================================
# Netkeiba & 近走指数
# ==================================================
def calculate_passing_order_bonus(pass_str: str, final_rank: int) -> float:
    if not pass_str or pass_str == "-": return 0.0
    clean_pass = re.sub(r"\(.*?\)", "", pass_str).strip()
    parts = clean_pass.split("-")
    positions = []
    for p in parts:
        try: positions.append(int(p))
        except: pass
    if len(positions) < 2: return 0.0
    max_bonus = 0.0
    for i in range(1, len(positions)):
        drop = positions[i] - positions[i-1]
        if drop > 0:
            if drop >= 4 and final_rank < positions[i]: return 8.0
            if drop >= 2 and final_rank < positions[i]: max_bonus = max(max_bonus, 5.0)
    return max_bonus

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
        umaban_tds, umaban = tr.find_all("td", class_="Waku"), ""
        for td in umaban_tds:
            txt = re.sub(r"\D", "", td.get_text(strip=True))
            if txt: umaban = txt; break
        if not umaban: continue
        jockey_td = tr.find("td", class_="Jockey")
        jockey = _clean_text_ja(jockey_td.get_text(strip=True)) if jockey_td else "不明"
        
        past_str_list, valid_runs = [], []
        prev_jockey = None # 前走騎手格納用

        # Pastカラムを走査 (最大3走)
        past_tds = tr.find_all("td", class_="Past")[:3]
        for idx, td in enumerate(past_tds):
            if "Rest" in td.get("class", []): 
                past_str_list.append("(放牧/休養)")
            else:
                d01, d02 = td.find("div", class_="Data01"), td.find("div", class_="Data02")
                date_place = _clean_text_ja(d01.get_text(strip=True)) if d01 else ""
                race_name_dist = _clean_text_ja(d02.get_text(strip=True)) if d02 else ""
                rank_tag = td.find("span", class_="Num") or td.find("div", class_="Rank")
                rank = rank_tag.get_text(strip=True) if rank_tag else "?"
                passing_order, d06 = "", td.find("div", class_="Data06")
                if d06:
                    match = re.match(r'^([\d\-]+)', d06.get_text(strip=True))
                    if match: passing_order = match.group(1)
                
                # --- ★追加箇所: 前走(index 0)のData03から騎手名を抽出 ---
                if idx == 0:
                    d03 = td.find("div", class_="Data03")
                    if d03:
                        d03_text = _clean_text_ja(d03.get_text(strip=True))
                        # Data03形式例: "18頭 2番 14人 坂井瑠星 58.0"
                        # 人気(人)と斤量(小数)の間にある文字列を抽出
                        j_match = re.search(r'\d+人\s+(.+?)\s+\d+\.\d', d03_text)
                        if j_match:
                            prev_jockey = j_match.group(1).strip()
                        else:
                            # 正規表現で取れない場合の予備：空白区切りの後ろから2番目などを想定
                            parts = d03_text.split()
                            if len(parts) >= 2:
                                prev_jockey = parts[-2]
                # ----------------------------------------------------

                past_str_list.append(f"[{date_place} {race_name_dist} {passing_order}→{rank}着]")
                try:
                    rank_int = int(re.sub(r"\D", "", rank))
                    valid_runs.append({"rank_int": rank_int, "bonus": calculate_passing_order_bonus(passing_order, rank_int)})
                except: pass
        
        base_score = sum(1.0 for r in valid_runs if r["rank_int"] <= 5)
        max_bonus = max([r["bonus"] for r in valid_runs], default=0.0)
        
        data[umaban] = {
            "jockey": jockey, 
            "prev_jockey": prev_jockey, # 戻り値に追加
            "past": past_str_list, 
            "kinsou_index": float(min(base_score + max_bonus, 10.0))
        }
    return data

# ==================================================
# Yahooスポーツナビ 対戦表取得ロジック（★評価ランク対応版）
# ==================================================
def fetch_yahoo_matrix_data(driver, year, place, kai, day, race_num, current_distance_str, horse_evals=None):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place: return "場所コードエラー"
    y_year, y_id = year[-2:], f"{year[-2:]}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    url = f"https://sports.yahoo.co.jp/keiba/race/matrix/{y_id}"
    driver.get(url)
    try: WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "hr-tableLeftTop--matrix")))
    except: return "対戦データ取得タイムアウト"
    soup = BeautifulSoup(driver.page_source, "html.parser")
    table = soup.find("table", class_="hr-tableLeftTop--matrix")
    if not table or not table.thead: return "対戦データなし"
    past_races, header_th_list = [], table.thead.find_all("th")[1:]
    for th in header_th_list:
        link_tag = th.find("a")
        if not link_tag: past_races.append(None); continue
        items = th.find_all("span", class_="hr-tableLeftTop__item")
        dist_str = next((item.get_text(strip=True) for item in items if "m" in item.get_text()), "")
        past_races.append({"id": link_tag.get("href").split("/")[-1], "name": link_tag.get_text(strip=True), "date": th.find("span", class_="hr-tableLeftTop__item--date").get_text(" ", strip=True), "dist_str": dist_str})
    matrix_data = {}
    for tr in table.tbody.find_all("tr"):
        th_horse = tr.find("th")
        if not th_horse or not th_horse.find("a"): continue
        horse_name = th_horse.find("a").get_text(strip=True)
        for idx, td in enumerate(tr.find_all("td")):
            if idx >= len(past_races) or not past_races[idx]: continue
            txt = td.get_text(strip=True)
            if "-" in txt and len(txt) < 5: continue
            rid, rank = past_races[idx]["id"], td.find("span").get_text(strip=True) if td.find("span") else "?"
            if rid not in matrix_data: matrix_data[rid] = {"info": past_races[idx], "results": []}
            matrix_data[rid]["results"].append({"name": horse_name, "rank": rank})
    valid_battles = sorted([d for d in matrix_data.values() if len(d["results"]) >= 2], key=lambda x: x["info"]["id"], reverse=True)
    if not valid_battles: return "対戦データなし（該当レースなし）"
    current_dist_int, output_lines = extract_distance_int(current_distance_str), ["\n【対戦表】"]
    for battle in valid_battles:
        info, results = battle["info"], battle["results"]
        results.sort(key=lambda r: int(re.sub(r"\D", "", r["rank"])) if re.sub(r"\D", "", r["rank"]) else 999)
        diff = extract_distance_int(info["dist_str"]) - current_dist_int
        res_str_list = []
        for r in results:
            grade = horse_evals.get(r['name'], "") if horse_evals else ""
            suffix = f"({grade})" if grade else ""
            res_str_list.append(f"{r['rank']}着{r['name']}{suffix}")
        output_lines.extend([f"・{info['date'].replace(' ', '')} {info['name']} {info['dist_str']}({diff:+}m)", f"URL：https://race.netkeiba.com/race/result.html?race_id=20{info['id']}", "着順：" + "　".join(res_str_list), ""])
    return "\n".join(output_lines)

# ==================================================
# Dify Streaming
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY: yield "⚠️ DIFY_API_KEY 未設定"; return
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
                    for val in data.get("data", {}).get("outputs", {}).values():
                        if isinstance(val, str): yield val
                elif "answer" in data: yield data.get("answer", "")
            except: pass
    except Exception as e: yield f"Error: {e}"

# ==================================================
# Main Execution (Batch)
# ==================================================
def run_batch_prediction(jobs_config, mode="ai"):
    full_output_log = ""
    for job_idx, job in enumerate(jobs_config):
        
        # --- ★ここからリトライループの開始 ---
        max_retries = 2
        for attempt in range(max_retries):
            driver = build_driver()
            try:
                st.info(f"[{job_idx+1}/{len(jobs_config)}] ログイン処理中 (試行 {attempt+1}/{max_retries})...")
                login_keibabook(driver)
                
                year = job["year"]
                kai = str(job["kai"]).zfill(2)
                place = str(job["place"]).zfill(2)
                day = str(job["day"]).zfill(2)
                place_name = job["place_name"]
                base_id = f"{year}{kai}{place}{day}"
                
                st.markdown(f"## 🏁 {place_name}開催")
                full_output_log += f"\n\n--- {place_name} ---\n"

                # 指定されたレースをすべて処理するループ
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
                        
                        sp_val = sm.get("speed_index", "-")
                        sp_str = f"スピード指数:{sp_val}/35点"
                        kinsou_idx = n.get("kinsou_index", 0.0)
                        fac_str = f"F:{c.get('fac_deashi','-')}/{c.get('fac_kettou','-')}" if is_shinba else f"F:{c.get('fac_crs','-')}/{c.get('fac_dis','-')}"
                        
                        # --- ★追加箇所: 騎手乗り替わり表示ロジック ---
                        current_jockey = n.get('jockey', '-')
                        prev_jockey = n.get('prev_jockey', None)
                        
                        if prev_jockey and prev_jockey != current_jockey:
                            jockey_disp = f"騎手:{current_jockey}←{prev_jockey}"
                        else:
                            jockey_disp = f"騎手:{current_jockey}"
                        # ----------------------------------------

                        line = (
                            f"▼{d['waku']}枠{umaban}番 {d['name']} ({jockey_disp})\n"
                            f"【データ】{sp_str} バイアス:{bias['total']} 近走指数:{kinsou_idx:.1f} {fac_str}\n"
                            f"【厩舎】{d['danwa']}\n"
                            f"【前走】{interview_data.get(umaban, 'なし')}\n"
                            f"【調教】{k['tanpyo']} \n{k['details']}\n"
                            f"【近走】{' / '.join(n.get('past', []))}\n"
                        )
                        lines.append(line)

                    raw_data_block = f"■レース情報\n{race_title}\n\n■各馬詳細\n" + "\n".join(lines)
                    result_area = st.empty()
                    ai_output = ""

                    if mode == "info":
                        ai_output = raw_data_block
                        result_area.text_area(f"{r}R データ", ai_output, height=400)
                        battle_matrix_text = ""
                    else:
                        status.text("AI分析中...")
                        for chunk in stream_dify_workflow(raw_data_block):
                            ai_output += chunk
                            result_area.markdown(ai_output + "▌")
                        
                        horse_evals = parse_dify_evaluation(ai_output)
                        battle_matrix_text = fetch_yahoo_matrix_data(
                            driver, year, place, kai, day, race_num_str, 
                            extract_race_info(race_title).get("distance", ""), 
                            horse_evals=horse_evals
                        )

                    final_output = ai_output + "\n\n" + battle_matrix_text
                    result_area.markdown(final_output)
                    
                    full_output_log += f"\n{race_title}\n{final_output}\n"
                    render_copy_button(final_output, f"{r}Rコピー", f"cp_{base_id}_{r}")
                    status.success("完了")

                # --- ★全てのレース(1R~3R等)が正常に終われば、リトライループを抜ける ---
                break

            except Exception as e:
                # タイムアウトなどのエラーが発生した場合
                if attempt < max_retries - 1:
                    st.warning(f"接続エラーのため再試行します... ({e})")
                    driver.quit()
                    time.sleep(2)
                    continue # 次の attempt (試行) へ
                else:
                    st.error(f"エラーが発生しました: {e}")
            finally:
                driver.quit()
        # --- ★リトライループ終了 ---

    return full_output_log
