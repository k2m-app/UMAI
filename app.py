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
from bs4 import BeautifulSoup

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
# 馬場バイアス評価データ（完全版）
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
    """'-' 等を安全に int 化"""
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
# スピード指数（基礎値→偏差値→30点満点変換）
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

    # 偏差値算出
    hensachi_scores = {}
    for umaban, raw in raw_scores.items():
        if std == 0:
            hensachi = 50.0
        else:
            hensachi = 50.0 + 10.0 * (raw - mean) / std
        hensachi_scores[umaban] = hensachi

    # 最高偏差値を30点に正規化
    if not hensachi_scores:
        return {}
    
    max_hensachi = max(hensachi_scores.values())
    
    out = {}
    for umaban, raw in raw_scores.items():
        hensachi = hensachi_scores[umaban]
        
        if max_hensachi == 0:
             score = 0.0
        elif max_hensachi == min(hensachi_scores.values()):
            score = 30.0
        else:
            score = 30.0 * (hensachi / max_hensachi)
        
        out[umaban] = {
            "raw": round(raw, 2),
            "hensachi": round(hensachi, 2),
            "score": round(score, 2)
        }

    return out


# ==================================================
# 馬場バイアス評価関数（完全版）
# ==================================================
def extract_race_info(race_title: str) -> dict:
    result = {
        "place": None,
        "distance": None,
        "track_type": None,
        "day": None,
        "course_variant": ""
    }
    
    # 競馬場名と開催日の抽出（1回中山4日目 のパターン）
    place_day_pattern = r'(\d+)回([^0-9]+?)(\d+)日目'
    place_day_match = re.search(place_day_pattern, race_title)
    if place_day_match:
        result["place"] = place_day_match.group(2).strip()
        result["day"] = int(place_day_match.group(3))
    
    # 距離の抽出（1200m のパターン）
    distance_pattern = r'(\d{3,4})m'
    distance_match = re.search(distance_pattern, race_title)
    if distance_match:
        result["distance"] = distance_match.group(1)
    
    # 芝/ダートの判定
    if 'ダート' in race_title:
        result["track_type"] = "dirt"
    elif '芝' in race_title:
        result["track_type"] = "turf"
    
    # コース種別（内回り・外回り）の判定
    if '内' in race_title:
        result["course_variant"] = "内"
    elif '外' in race_title:
        result["course_variant"] = "外"
    
    return result


def calculate_baba_bias(waku: int, race_title: str) -> dict:
    kaisai_bias = 0
    course_bias = 0
    debug_info = []
    
    # レース情報抽出
    race_info = extract_race_info(race_title)
    
    place_name = race_info["place"]
    distance = race_info["distance"]
    track_type = race_info["track_type"]
    race_day = race_info["day"]
    course_variant = race_info["course_variant"]
    
    # 開催週バイアス(芝のレースで開催1-2日目のみ)
    if track_type == "turf" and race_day in [1, 2]:
        if waku == 1:
            kaisai_bias = 5
        elif waku == 2:
            kaisai_bias = 3
        elif waku == 3:
            kaisai_bias = 2
    
    # コースバイアス評価
    if place_name and distance and track_type:
        track_str = "芝" if track_type == "turf" else "ダート"
        course_key = f"{place_name}{track_str}{distance}{course_variant}"
        
        if course_key in BABA_BIAS_DATA:
            bias_data = BABA_BIAS_DATA[course_key]
            
            # 点数の高い順にチェック（5→3→2）
            for points in [5, 3, 2]:
                if points in bias_data and waku in bias_data[points]:
                    course_bias = points
                    break
    
    return {
        "kaisai_bias": kaisai_bias,
        "course_bias": course_bias,
        "total": kaisai_bias + course_bias,
        "debug": " | ".join(debug_info)
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
        pass


# ==================================================
# 競馬ブック:厩舎の話 (Danwa) - 枠番追加版
# ==================================================
def parse_race_info_from_danwa(html: str) -> dict:
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
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "danwa" in str(c))
    if not table or not table.tbody:
        return {}

    horses = {}
    current_umaban = None
    current_waku = None

    rows = table.tbody.find_all("tr", recursive=False)
    for tr in rows:
        classes = tr.get("class", [])
        if "spacer" in classes:
            continue

        # 枠番取得
        waku_td = tr.find("td", class_="waku")
        umaban_td = tr.find("td", class_="umaban")
        bamei_td = tr.find("td", class_="left")

        if waku_td and umaban_td and bamei_td:
            # 枠番抽出
            waku_p = waku_td.find("p")
            if waku_p:
                waku_class = waku_p.get("class", [])
                for cls in waku_class:
                    if cls.startswith("waku"):
                        current_waku = re.sub(r"\D", "", cls)
                        break
            
            # 馬番抽出
            raw_umaban = umaban_td.get_text(strip=True)
            current_umaban = re.sub(r"\D", "", raw_umaban)

            # 馬名抽出
            anchor = bamei_td.find("a")
            if anchor:
                raw_name = anchor.get_text(strip=True)
            else:
                raw_name = bamei_td.get_text(strip=True)
            clean_name = _clean_text_ja(raw_name)

            if current_umaban:
                horses[current_umaban] = {
                    "name": clean_name,
                    "waku": current_waku if current_waku else "?",
                    "danwa": ""
                }
            continue

        danwa_td = tr.find("td", class_="danwa")
        if danwa_td and current_umaban:
            comment_text = danwa_td.get_text("\n", strip=True)
            comment_text = _clean_text_ja(comment_text)
            if horses[current_umaban]["danwa"]:
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
# 競馬ブック:調教 (Chokyo)
# ==================================================
def parse_keibabook_chokyo(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}
    tables = soup.find_all("table", class_="cyokyo")

    for tbl in tables:
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td:
            continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))

        tanpyo_td = tbl.find("td", class_="tanpyo")
        tanpyo = _clean_text_ja(tanpyo_td.get_text(strip=True)) if tanpyo_td else "なし"

        details_text_parts = []
        detail_cell = tbl.find("td", colspan="5")
        if detail_cell:
            for child in detail_cell.children:
                if child.name == "dl" and "dl-table" in child.get("class", []):
                    dt_texts = [c.get_text(strip=True) for c in child.find_all(["dt", "dd"])]
                    line = " ".join(dt_texts)
                    details_text_parts.append(line)
                elif child.name == "table" and "cyokyodata" in child.get("class", []):
                    time_tr = child.find("tr", class_="time")
                    if time_tr:
                        times = [td.get_text(strip=True) for td in time_tr.find_all("td")]
                        details_text_parts.append(" ".join(times))
                    awase_tr = child.find("tr", class_="awase")
                    if awase_tr:
                        awase_txt = _clean_text_ja(awase_tr.get_text(strip=True))
                        details_text_parts.append(awase_txt)

            semekaisetu_div = detail_cell.find("div", class_="semekaisetu")
            if semekaisetu_div:
                kaisetu_p = semekaisetu_div.find("p")
                if kaisetu_p:
                    k_text = _clean_text_ja(kaisetu_p.get_text(strip=True))
                    details_text_parts.append(f"[攻め解説] {k_text}")

        full_detail = " ".join(details_text_parts)
        full_detail = re.sub(r"\s+", " ", full_detail).strip()
        data[umaban] = {"tanpyo": tanpyo, "details": full_detail}

    return data


def fetch_keibabook_chokyo(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "cyokyo"))
        )
    except:
        pass
    html = driver.page_source
    return parse_keibabook_chokyo(html)


# ==================================================
# 競馬ブック:前走インタビュー (Syoin)
# ==================================================
def parse_zenkoso_interview(html: str) -> dict:
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

        umaban_td = tr.find("td", class_="umaban")
        if umaban_td:
            raw_u = umaban_td.get_text(strip=True)
            current_umaban = re.sub(r"\D", "", raw_u)
            continue

        syoin_td = tr.find("td", class_="syoin")
        if syoin_td and current_umaban:
            meta_div = syoin_td.find("div", class_="syoindata")
            if meta_div:
                meta_div.decompose()
            raw_text = syoin_td.get_text(" ", strip=True)
            clean_text = _clean_text_ja(raw_text)
            if not _is_missing_marker(clean_text) and len(clean_text) > 1:
                interview_data[current_umaban] = clean_text
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
# 競馬ブック:CPU予想 (新馬対応版)
# ==================================================
def parse_keibabook_cpu(html: str, is_shinba: bool = False) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    # --- スピード指数テーブル ---
    speed_tbl = soup.find("table", id="cpu_speed_sort_table")
    if speed_tbl and speed_tbl.tbody:
        for tr in speed_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td:
                continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban:
                continue

            tds = tr.find_all("td")
            if len(tds) < 8:
                continue

            def get_v(idx):
                p = tds[idx].find("p")
                txt = re.sub(r"\D", "", p.get_text(strip=True)) if p else ""
                val = int(txt) if txt else 0
                return val if val < 900 else 0

            last = get_v(-1)
            two = get_v(-2)
            thr = get_v(-3)

            vals = [x for x in [last, two, thr] if x > 0]
            avg = round(sum(vals) / len(vals)) if vals else 0

            data[umaban] = {
                "sp_last": str(last) if last else "-",
                "sp_2": str(two) if two else "-",
                "sp_3": str(thr) if thr else "-",
                "sp_avg": str(avg) if avg else "-",
            }

    # --- ファクターテーブル ---
    factor_tbl = None
    for t in soup.find_all("table"):
        c = t.find("caption")
        if c and "ファクター" in c.get_text():
            factor_tbl = t
            break

    if factor_tbl and factor_tbl.tbody:
        for tr in factor_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td:
                continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban:
                continue

            tds = tr.find_all("td")
            if len(tds) < 6:
                continue

            def get_m(idx):
                if idx >= len(tds):
                    return "-"
                p = tds[idx].find("p")
                t = p.get_text(strip=True) if p else ""
                return t if t else "-"

            if umaban not in data:
                data[umaban] = {}

            if is_shinba:
                data[umaban].update(
                    {
                        "fac_deashi": get_m(5),
                        "fac_kettou": get_m(6),
                        "fac_ugoki": get_m(8),
                    }
                )
            else:
                data[umaban].update(
                    {
                        "fac_crs": get_m(5),
                        "fac_dis": get_m(6),
                        "fac_zen": get_m(7),
                    }
                )

    return data


def fetch_keibabook_cpu_data(driver, race_id: str, is_shinba: bool = False):
    url = f"{BASE_URL}/cyuou/cpu/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "main")))
    except:
        pass
    return parse_keibabook_cpu(driver.page_source, is_shinba)


# ==================================================
# Netkeiba (騎手・戦績詳細取得)
# ==================================================
def _parse_netkeiba_past_td(td) -> str:
    """netkeibaの過去走セル(td.Past)を解析して文字列化"""
    if not td:
        return "-"

    data01 = td.find("div", class_="Data01")
    date_place = _clean_text_ja(data01.get_text(strip=True)) if data01 else ""

    data02 = td.find("div", class_="Data02")
    race_name = _clean_text_ja(data02.get_text(strip=True)) if data02 else ""

    data03 = td.find("div", class_="Data03")
    jockey_weight = _clean_text_ja(data03.get_text(" ", strip=True)) if data03 else ""

    rank = "?"
    rank_tag = td.find("span", class_="Num")
    if not rank_tag:
        rank_tag = td.find("div", class_="Rank") or td.find("span", class_="Rank") or td.find("span", class_="Order")
    if rank_tag:
        rank = _clean_text_ja(rank_tag.get_text(strip=True))

    data05 = td.find("div", class_="Data05")
    time_dist = _clean_text_ja(data05.get_text(" ", strip=True)) if data05 else ""

    passing = ""
    data06 = td.find("div", class_="Data06")
    if data06:
        raw_d6 = _clean_text_ja(data06.get_text(strip=True))
        match = re.search(r"(\d{1,2}(?:-\d{1,2})+)", raw_d6)
        if match:
            passing = match.group(1)
        if not passing:
            match_single = re.match(r"^(\d{1,2})\s", raw_d6)
            if match_single:
                passing = match_single.group(1)

    if len(date_place) < 2:
        return "-"

    rank_display = f"{passing}→{rank}着" if passing else f"{rank}着"
    return f"[{date_place} {race_name} {jockey_weight} {time_dist} ({rank_display})]"


def fetch_netkeiba_data(driver, year, kai, place, day, race_num):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place:
        return {}

    nk_race_id = f"{year}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={nk_race_id}&rf=shutuba_submenu"

    driver.get(url)
    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "Shutuba_Past5_Table")))
    except:
        return {}

    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}

    rows = soup.find_all("tr", class_="HorseList")
    for tr in rows:
        waku_tds = tr.find_all("td", class_="Waku")
        umaban = ""
        for td in waku_tds:
            txt = re.sub(r"\D", "", td.get_text(strip=True))
            if txt:
                umaban = txt
                break

        if not umaban:
            continue

        jockey_td = tr.find("td", class_="Jockey")
        jockey = "不明"
        if jockey_td:
            a_tag = jockey_td.find("a")
            if a_tag:
                jockey = a_tag.get_text(strip=True)
            else:
                barei = jockey_td.find("span", class_="Barei")
                if barei:
                    barei.decompose()
                jockey = jockey_td.get_text(strip=True)
        jockey = _clean_text_ja(jockey)

        past_tds = tr.find_all("td", class_="Past")
        past_list = []
        for td in past_tds[:3]:
            if "Rest" in td.get("class", []):
                past_list.append("(放牧/休養)")
            else:
                past_list.append(_parse_netkeiba_past_td(td))

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
        res = requests.post(
            "https://api.dify.ai/v1/workflows/run",
            headers=headers,
            json=payload,
            stream=True,
            timeout=90
        )
        for line in res.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8").replace("data: ", "")
            try:
                data = json.loads(decoded)
                event = data.get("event")
                if event == "workflow_finished":
                    outputs = data.get("data", {}).get("outputs", {})
                    for val in outputs.values():
                        if isinstance(val, str):
                            yield val
                if event == "message" or "answer" in data:
                    yield data.get("answer", "")
            except:
                pass
    except Exception as e:
        yield f"Error: {e}"


# ==================================================
# Main Execution (Batch)
# ==================================================
def run_batch_prediction(jobs_config):
    """
    複数場の設定を受け取り、ブラウザを一度立ち上げたら連続で処理する
    jobs_config = [
        {"year": "2026", "kai": "01", "place": "05", "day": "04", "races": [1,2...], "place_name": "中山"},
        ...
    ]
    """
    driver = build_driver()
    full_output_log = ""
    
    try:
        st.info(f"ログイン中... (ID: {KEIBA_ID[:2]}**)")
        login_keibabook(driver)
        st.success("ログイン成功。連続実行を開始します。")

        # ジョブ（開催場）ループ
        for job_idx, job in enumerate(jobs_config):
            year = job["year"]
            kai = str(job["kai"]).zfill(2)
            place = str(job["place"]).zfill(2)
            day = str(job["day"]).zfill(2)
            target_races = sorted(job["races"])
            place_name = job["place_name"]
            
            base_id = f"{year}{kai}{place}{day}"
            
            st.markdown(f"## 🏁 {place_name}開催 ({day}日目)")
            full_output_log += f"\n\n{'='*30}\n【{place_name}】 {year}年{kai}回{day}日目\n{'='*30}\n"

            # レースループ
            for r in target_races:
                race_num_str = f"{r:02}"
                race_id = base_id + race_num_str
                
                st.markdown(f"### {place_name} {r}R")
                status = st.empty()
                status.text("データ収集中...")
                
                # --- データ取得 ---
                header_info, danwa_data = fetch_keibabook_danwa(driver, race_id)
                if not danwa_data:
                    st.error(f"{place_name} {r}R データ取得失敗 (ID:{race_id})")
                    continue
                
                race_title = header_info.get("header_text", "")
                is_shinba = ("新馬" in race_title) or ("メイクデビュー" in race_title)
                
                cpu_data = fetch_keibabook_cpu_data(driver, race_id, is_shinba=is_shinba)
                speed_metrics = compute_speed_metrics(cpu_data)
                interview_data = fetch_zenkoso_interview(driver, race_id)
                chokyo_data = fetch_keibabook_chokyo(driver, race_id)
                nk_data = fetch_netkeiba_data(driver, year, kai, place, day, race_num_str)
                
                # --- プロンプト構築 ---
                lines = []
                for umaban in sorted(danwa_data.keys(), key=int):
                    d_info = danwa_data[umaban]
                    c_info = cpu_data.get(umaban, {})
                    i_text = interview_data.get(umaban, "なし")
                    k_info = chokyo_data.get(umaban, {"tanpyo": "-", "details": "-"})
                    n_info = nk_data.get(umaban, {})
                    waku = d_info.get("waku", "?")
                    
                    sm = speed_metrics.get(umaban, {})
                    baba_bias = calculate_baba_bias(int(waku) if waku.isdigit() else 0, race_title)
                    
                    past_str = " / ".join(n_info.get("past", [])) or "情報なし"
                    sp_str = f"指数:{sm.get('score','-')}/30 (偏差値:{sm.get('hensachi','-')})"
                    bias_str = f"バイアス:{baba_bias['total']}/10"
                    
                    if is_shinba:
                        fac_str = f"F:{c_info.get('fac_deashi','-')}/{c_info.get('fac_kettou','-')}"
                    else:
                        fac_str = f"F:{c_info.get('fac_crs','-')}/{c_info.get('fac_dis','-')}"
                        
                    line = (
                        f"▼{waku}枠{umaban}番 {d_info['name']} (騎手:{n_info.get('jockey','-')})\n"
                        f"【データ】{sp_str} {bias_str} {fac_str}\n"
                        f"【厩舎】{d_info['danwa']}\n"
                        f"【前走】{i_text}\n"
                        f"【調教】{k_info['tanpyo']} {k_info['details']}\n"
                        f"【近走】{past_str}\n"
                    )
                    lines.append(line)

                full_prompt = f"■レース情報\n{race_title}\n\n■各馬詳細\n" + "\n".join(lines)
                
                # AI生成
                status.text("AI分析中...")
                result_area = st.empty()
                ai_output = ""
                for chunk in stream_dify_workflow(full_prompt):
                    ai_output += chunk
                    result_area.markdown(ai_output + "▌")
                
                result_area.markdown(ai_output)
                
                # ログ蓄積
                race_log = f"\n--- {place_name} {r}R ---\n{ai_output}"
                full_output_log += race_log
                
                render_copy_button(ai_output, f"{place_name}{r}R コピー", f"copy_{job_idx}_{r}")
                status.success("完了")
            
            # 1開催終了ごとの区切り
            st.success(f"✅ {place_name}開催分の処理が完了しました")
            
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
    finally:
        driver.quit()
        
    return full_output_logJRAimport streamlit as st
import keiba_bot

# ==================================================
# App config
# ==================================================
st.set_page_config(
    page_title="UMAI",
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

# 年の選択肢（2026をデフォルトにするため先頭に配置）
YEAR_OPTIONS = ["2026", "2025"]

# ==================================================
# Session state init
# ==================================================
# 3場分の設定を保持するための初期化
MAX_VENUES = 3

if "combined_output" not in st.session_state:
    st.session_state.combined_output = ""

# 各会場の設定保存用State初期化
for v_idx in range(MAX_VENUES):
    prefix = f"v{v_idx}"
    
    if f"{prefix}_active" not in st.session_state:
        st.session_state[f"{prefix}_active"] = (v_idx == 0) # 最初だけActive
    
    # 年の初期値は "2026"
    if f"{prefix}_year" not in st.session_state:
        st.session_state[f"{prefix}_year"] = "2026"
        
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
st.title("UMAI")
st.caption("最大3つの開催場を一括設定し、連続で予想を実行します。")

# タブで会場切り替え
tabs = st.tabs([f"開催設定 {i+1}" for i in range(MAX_VENUES)])

jobs_config = [] # 実行時に渡す設定リスト

for v_idx, tab in enumerate(tabs):
    prefix = f"v{v_idx}"
    with tab:
        is_active = st.toggle(f"この開催（開催設定{v_idx+1}）を有効にする", key=f"{prefix}_active")
        
        if is_active:
            col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1, 1, 1])
            with col_p1:
                # 【修正箇所】テキスト入力からセレクトボックスに変更
                st.selectbox("年", YEAR_OPTIONS, key=f"{prefix}_year")
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
