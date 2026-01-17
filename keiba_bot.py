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

# 競馬ブック PLACEコード → netkeiba 競馬場コード
KEIBABOOK_TO_NETKEIBA_PLACE = {
    "08": "01", "09": "02", "06": "03", "07": "04", "04": "05",
    "05": "06", "02": "07", "00": "08", "01": "09", "03": "10",
}

# ==================================================
# 馬場バイアス評価データ (既存のまま)
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


def _safe_int(s, default=0) -> int:
    try:
        if s is None:
            return default
        if isinstance(s, (int, float)):
            return int(s)
        ss = str(s).strip()
        ss = re.sub(r"[^0-9\-]", "", ss) # 数字とマイナス以外削除
        if ss in {"", "-", "－"}:
            return default
        return int(ss)
    except:
        return default

def extract_distance_int(dist_str: str) -> int:
    """ 'ダ1900' や '芝1600' から 1900 等の数値を抽出 """
    match = re.search(r'(\d{3,4})', str(dist_str))
    if match:
        return int(match.group(1))
    return 0

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
# スピード指数・バイアス計算
# ==================================================
def compute_speed_metrics(cpu_data: dict, w_max: float = 2.0, w_last: float = 1.8, w_avg: float = 1.2) -> dict:
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
        
        denom = (w_max + w_last + w_avg)
        raw = (max_v * w_max + last * w_last + avg * w_avg) / denom
        raw_scores[umaban] = raw

    if not raw_scores:
        return {}

    values = list(raw_scores.values())
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) if len(values) > 1 else 0

    out = {}
    for umaban, raw in raw_scores.items():
        if std == 0:
            hensachi = 50.0
        else:
            hensachi = 50.0 + 10.0 * (raw - mean) / std
        
        out[umaban] = {
            "raw_ability": round(raw, 2),
            "speed_index": round(hensachi, 1)
        }

    return out


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
# 競馬ブック スクレイピング関数
# ==================================================
def fetch_keibabook_danwa(driver, race_id: str):
    # (既存のコードと同じ)
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa")))
    except: pass
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    racetitle = soup.find("div", class_="racetitle")
    header_parts = []
    if racetitle:
        for p in racetitle.find_all("p"):
            header_parts.append(p.get_text(strip=True))
    header_info = {"header_text": "\n".join(header_parts)}
    
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
    # (既存のコードと同じ)
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cyokyo")))
    except: pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    tables = soup.find_all("table", class_="cyokyo")
    for tbl in tables:
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td: continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
        tanpyo_td = tbl.find("td", class_="tanpyo")
        tanpyo = _clean_text_ja(tanpyo_td.get_text(strip=True)) if tanpyo_td else "なし"
        detail_cell = tbl.find("td", colspan="5")
        details_text_parts = []
        if detail_cell:
            current_header_info = ""
            for child in detail_cell.children:
                if isinstance(child, NavigableString): continue
                if child.name == 'dl' and 'dl-table' in child.get('class', []):
                    dt_texts = [dt.get_text(" ", strip=True) for dt in child.find_all('dt')]
                    current_header_info = " ".join([t for t in dt_texts if t])
                elif child.name == 'table' and 'cyokyodata' in child.get('class', []):
                    time_tr = child.find('tr', class_='time')
                    time_str = ""
                    if time_tr:
                        times = []
                        for td in time_tr.find_all('td'):
                            txt = td.get_text(strip=True)
                            if txt: times.append(txt)
                        time_str = "-".join(times)
                    awase_tr = child.find('tr', class_='awase')
                    awase_str = ""
                    if awase_tr:
                        awase_txt = _clean_text_ja(awase_tr.get_text(strip=True))
                        if awase_txt: awase_str = f" (併せ: {awase_txt})"
                    if current_header_info or time_str:
                        details_text_parts.append(f"[{current_header_info}] {time_str}{awase_str}")
                    current_header_info = ""
        full_details = "\n".join(details_text_parts) if details_text_parts else "詳細なし"
        data[umaban] = {"tanpyo": tanpyo, "details": full_details}
    return data


def fetch_zenkoso_interview(driver, race_id: str):
    # (既存のコードと同じ)
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
    # (既存のコードと同じ)
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
# Netkeiba & 近走指数 & 対戦表用データ収集
# ==================================================
def calculate_passing_order_bonus(pass_str: str, final_rank: int) -> float:
    """
    通過順による近走指数ボーナス計算
    例: 10-10-14 -> 6
    10->14で4つ下がったが、最終着順6(14より良い)なのでボーナス8.0
    """
    if not pass_str or pass_str == "-":
        return 0.0
    
    # 10-10-14 (38.7) のような形式から数値リストを抽出
    # カッコ書きなどを除去してハイフン区切りだけ見る
    clean_pass = re.sub(r"\(.*?\)", "", pass_str).strip()
    parts = clean_pass.split("-")
    
    # 数値に変換
    positions = []
    for p in parts:
        try:
            positions.append(int(p))
        except:
            pass
            
    if len(positions) < 2:
        return 0.0
    
    max_bonus = 0.0
    
    # 通過順リストを走査して「下がり」を検知
    for i in range(1, len(positions)):
        prev = positions[i-1]
        curr = positions[i]
        
        # 順位が下がった（数値が大きくなった）場合
        drop = curr - prev
        
        if drop > 0:
            # 条件①: 4つ以上下がったのに、最終着順がその「下がった位置」より良い
            if drop >= 4 and final_rank < curr:
                # 8.0点 (優先度高)
                return 8.0
            
            # 条件②: 2つ以上下がったのに、最終着順がその「下がった位置」より良い
            if drop >= 2 and final_rank < curr:
                # 5.0点 (8.0がなければ適用)
                max_bonus = max(max_bonus, 5.0)
                
    return max_bonus


def fetch_netkeiba_data(driver, year, kai, place, day, race_num, horse_name_map):
    """
    horse_name_map: {umaban_str: horse_name} を受け取り、対戦表作成用に名前も保持する
    """
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
        history_raw_data = [] # 対戦表用生データ
        valid_runs = []
        
        # 馬名取得（対戦表マッチング用）
        horse_name = horse_name_map.get(umaban, "Unknown")

        # 直近3走を取得
        for td in tr.find_all("td", class_="Past")[:3]:
            if "Rest" in td.get("class", []):
                past_str_list.append("(放牧/休養)")
            else:
                # --- データ抽出 ---
                # 日付・場所
                d01 = td.find("div", class_="Data01")
                date_place = ""
                # レース名・距離
                d02 = td.find("div", class_="Data02")
                race_name_dist = ""
                
                # レースID抽出（リンクから）
                past_race_id = None
                
                if d01:
                    first_span = d01.find("span")
                    if first_span: date_place = _clean_text_ja(first_span.get_text(strip=True))
                    else: date_place = _clean_text_ja(d01.get_text(strip=True))
                
                if d02:
                    race_name_dist = _clean_text_ja(d02.get_text(strip=True))
                    # リンク取得
                    a_tag = d02.find("a")
                    if a_tag and a_tag.get("href"):
                        href = a_tag.get("href")
                        # href="../race/result.html?race_id=202506050911&rf=race_list"
                        rid_match = re.search(r'race_id=(\d+)', href)
                        if rid_match:
                            past_race_id = rid_match.group(1)

                # 着順
                rank_tag = td.find("span", class_="Num") or td.find("div", class_="Rank")
                rank = rank_tag.get_text(strip=True) if rank_tag else "?"
                
                # 通過順
                d06 = td.find("div", class_="Data06")
                passing_order = ""
                if d06:
                    raw_d06 = d06.get_text(strip=True)
                    match = re.match(r'^([\d\-]+)', raw_d06)
                    if match: passing_order = match.group(1)
                
                pass_str = f" {passing_order}→" if passing_order else " "
                txt = f"[{date_place} {race_name_dist}{pass_str}{rank}着]"
                past_str_list.append(txt)
                
                rank_int = 99
                try:
                    rank_int = int(re.sub(r"\D", "", rank))
                    
                    # --- 近走指数用データ蓄積 ---
                    # ① 通常点: 3着以内なら3点 (後でminをとる) -> 元のロジック維持
                    # ② ボーナス計算
                    bonus = calculate_passing_order_bonus(passing_order, rank_int)
                    valid_runs.append({"rank_int": rank_int, "bonus": bonus})
                    
                    # --- 対戦表用データ蓄積 ---
                    if past_race_id:
                        history_raw_data.append({
                            "race_id": past_race_id,
                            "date": date_place.split(" ")[0] if " " in date_place else date_place, # 日付簡易取得
                            "name": race_name_dist,
                            "rank": rank, # 表示用文字列
                            "rank_int": rank_int, # ソート用
                            "horse_name": horse_name
                        })

                except: pass
        
        # 近走指数計算
        # 基本点: 3着以内回数 * 3 (MAX 9) ※元のロジックが sum(3 for...) だったのでそれに合わせるが
        # ここでは「着順が良い」ことの評価と「巻き返し」の評価を合わせる
        
        # 元のロジック: sum(3 for r in valid_runs if r["rank_int"] <= 3) -> 最大9点
        base_score = sum(3 for r in valid_runs if r["rank_int"] <= 3)
        
        # ボーナスの最大値を加算（1レースでも凄まじい巻き返しがあれば評価）
        max_bonus = max([r["bonus"] for r in valid_runs], default=0.0)
        
        final_index = float(min(base_score + max_bonus, 10.0)) # 上限10でキャップする場合
        # ※もしボーナスで10を超えてアピールしたい場合はキャップを外しても良いですが、
        # 元のコードに合わせて一旦 min(..., 10) としておきます。
        # ただしボーナス8点が入るとすぐにカンストするので、上限を少し解放するか、
        # あるいは「指数」として扱うならそのまま足しても良いです。
        # ここでは「最大値10の指数」という前提を少し緩め、ボーナス分は素直に乗せます(ただし表示等の都合で調整)
        
        final_index_val = base_score + max_bonus
        
        data[umaban] = {
            "jockey": jockey, 
            "past": past_str_list, 
            "kinsou_index": final_index_val,
            "history_raw": history_raw_data # 対戦表生成用に保持
        }
    return data


# ==================================================
# 対戦表生成ロジック
# ==================================================
def generate_battle_matrix(all_horses_data: dict, current_race_distance_str: str) -> str:
    """
    全馬の過去走データ(history_raw)を集計し、同一レースに出走した馬のグループを作成する
    """
    # 現在のレースの距離数値
    current_dist = extract_distance_int(current_race_distance_str)
    
    # 全過去レースを RaceID をキーに集約
    # map: { race_id: { "info": {date, name}, "horses": [ {name, rank, rank_int}, ... ] } }
    race_map = {}
    
    for umaban, d in all_horses_data.items():
        history = d.get("history_raw", [])
        for h in history:
            rid = h["race_id"]
            if rid not in race_map:
                race_map[rid] = {
                    "info": {"date": h["date"], "name": h["name"], "id": rid},
                    "horses": []
                }
            race_map[rid]["horses"].append({
                "name": h["horse_name"],
                "rank": h["rank"],
                "rank_int": h["rank_int"]
            })
            
    # 出走馬が2頭以上のレースのみ抽出
    battles = []
    for rid, data in race_map.items():
        if len(data["horses"]) >= 2:
            # 着順でソート
            sorted_horses = sorted(data["horses"], key=lambda x: x["rank_int"])
            battles.append({
                "info": data["info"],
                "horses": sorted_horses
            })
            
    if not battles:
        return "対戦データなし"

    # 日付順（新しい順）等でソートしたいが、日付文字列解析が複雑なため
    # IDの降順（概ね新しい順）で簡易ソート
    battles.sort(key=lambda x: x["info"]["id"], reverse=True)
    
    # テキスト生成
    output_lines = ["\n【対戦表】"]
    for battle in battles:
        info = battle["info"]
        
        # 距離差計算
        past_dist = extract_distance_int(info["name"])
        diff = past_dist - current_dist
        diff_str = f"{diff:+}m" if diff != 0 else "±0m"
        
        # URL生成
        # https://race.netkeiba.com/race/result.html?race_id=202608010202&rf=race_list
        race_url = f"https://race.netkeiba.com/race/result.html?race_id={info['id']}&rf=race_list"
        
        header = f"・{info['date']} {info['name']} ({diff_str})"
        url_line = f"URL：{race_url}"
        
        # 着順リスト
        # 4着フィサブロス　5着テスタヴェローチェ
        horse_results = []
        for h in battle["horses"]:
            horse_results.append(f"{h['rank']}着{h['name']}")
        
        results_line = "着順：" + "　".join(horse_results)
        
        output_lines.append(header)
        output_lines.append(url_line)
        output_lines.append(results_line)
        output_lines.append("") # 空行
        
    return "\n".join(output_lines)


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
                
                # 1. 競馬ブック談話（馬名・枠順取得）
                header_info, danwa_data = fetch_keibabook_danwa(driver, race_id)
                if not danwa_data:
                    st.error(f"データ取得失敗: {race_id}")
                    continue
                
                race_title = header_info.get("header_text", "")
                is_shinba = any(x in race_title for x in ["新馬", "メイクデビュー"])
                
                # 2. 競馬ブックCPU指数
                cpu_data = fetch_keibabook_cpu_data(driver, race_id, is_shinba=is_shinba)
                speed_metrics = compute_speed_metrics(cpu_data)
                
                # 3. 競馬ブックその他
                interview_data = fetch_zenkoso_interview(driver, race_id)
                chokyo_data = fetch_keibabook_chokyo(driver, race_id)
                
                # 4. Netkeibaデータ（近走指数・対戦表用履歴含む）
                # 馬名マッピング作成（馬番 -> 馬名）
                horse_name_map = {u: d["name"] for u, d in danwa_data.items()}
                nk_data = fetch_netkeiba_data(driver, year, kai, place, day, race_num_str, horse_name_map)
                
                lines = []
                for umaban in sorted(danwa_data.keys(), key=int):
                    d = danwa_data[umaban]
                    sm = speed_metrics.get(umaban, {})
                    n = nk_data.get(umaban, {})
                    c = cpu_data.get(umaban, {})
                    k = chokyo_data.get(umaban, {"tanpyo": "-", "details": "-"})
                    bias = calculate_baba_bias(int(d["waku"]) if d["waku"].isdigit() else 0, race_title)
                    
                    sp_val = sm.get("speed_index", "-")
                    sp_str = f"スピード指数(偏差値):{sp_val}"
                    
                    kinsou_idx = n.get("kinsou_index", 0.0)
                    fac_str = f"F:{c.get('fac_deashi','-')}/{c.get('fac_kettou','-')}" if is_shinba else f"F:{c.get('fac_crs','-')}/{c.get('fac_dis','-')}"
                    
                    line = (
                        f"▼{d['waku']}枠{umaban}番 {d['name']} (騎手:{n.get('jockey','-')})\n"
                        f"【データ】{sp_str} バイアス:{bias['total']} 近走指数:{kinsou_idx:.1f} {fac_str}\n"
                        f"【厩舎】{d['danwa']}\n"
                        f"【前走】{interview_data.get(umaban, 'なし')}\n"
                        f"【調教】{k['tanpyo']} \n{k['details']}\n"
                        f"【近走】{' / '.join(n.get('past', []))}\n"
                    )
                    lines.append(line)

                # 5. 対戦表生成
                battle_matrix_text = generate_battle_matrix(nk_data, extract_race_info(race_title).get("distance", ""))

                full_prompt = f"■レース情報\n{race_title}\n\n■各馬詳細\n" + "\n".join(lines) + "\n" + battle_matrix_text
                
                status.text("AI分析中...")
                result_area = st.empty()
                ai_output = ""
                
                # Difyからのストリーミング回答を表示
                for chunk in stream_dify_workflow(full_prompt):
                    ai_output += chunk
                    result_area.markdown(ai_output + "▌")
                result_area.markdown(ai_output)
                
                # 結果ログとコピーボタン
                # 対戦表もAIのアウトプットには含まれるようにプロンプトに投げているが、
                # もしAIがそれを無視した場合でも、生データをユーザーが見れるように
                # 必要であればここに追加表示してもよい。今回はプロンプトに含めてAIに解析させる想定。
                full_output_log += f"\n{race_title}\n{ai_output}\n"
                render_copy_button(ai_output, f"{r}Rコピー", f"cp_{base_id}_{r}")
                status.success("完了")
                
        except Exception as e:
            st.error(f"エラー: {e}")
        finally:
            driver.quit()
    return full_output_log

# Streamlit UI
if __name__ == "__main__":
    st.set_page_config(page_title="AI競馬予想", layout="wide")
    st.title("AI競馬予想システム Pro")
    
    # サイドバー設定
    st.sidebar.header("開催設定")
    s_year = st.sidebar.text_input("年(YYYY)", "2026")
    s_kai = st.sidebar.text_input("回(数字)", "1")
    s_place = st.sidebar.selectbox("場所", list(KEIBABOOK_TO_NETKEIBA_PLACE.keys()), format_func=lambda x: requests.utils.unquote(x) if False else x + " (コード)")
    s_day = st.sidebar.text_input("日目(数字)", "1")
    s_place_name = st.sidebar.text_input("場所名(表示用)", "京都")
    s_races = st.sidebar.text_input("レース番号(カンマ区切り)", "1,2,3")
    
    if st.sidebar.button("予想開始"):
        try:
            r_list = [int(x.strip()) for x in s_races.split(",") if x.strip()]
            job = {
                "year": s_year,
                "kai": s_kai,
                "place": s_place,
                "day": s_day,
                "place_name": s_place_name,
                "races": r_list
            }
            run_batch_prediction([job])
        except Exception as e:
            st.error(f"設定エラー: {e}")
