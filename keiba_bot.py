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
        ss = re.sub(r"[^0-9\-]", "", ss)
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
# スピード指数（偏差値算出 → 35点満点へ変換）
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
        
        # 【修正ポイント】偏差値を35点満点スケールに変換する
        # 偏差値30以下は0点、偏差値70以上は35点(満点)となるように調整
        # 計算式: (偏差値 - 30) * 0.875
        # 例: 偏差値50 -> 17.5点 / 偏差値70 -> 35.0点
        
        score_35 = (hensachi - 30) * 0.875
        
        # 範囲を0〜35に制限（クリッピング）
        score_35 = max(0.0, min(35.0, score_35))

        out[umaban] = {
            "raw_ability": round(raw, 2),
            "speed_index": round(score_35, 1) # ここには35点満点の値が入る
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
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cyokyo")))
    except:
        pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    tables = soup.find_all("table", class_="cyokyo")

    for tbl in tables:
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td:
            continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
        tanpyo_td = tbl.find("td", class_="tanpyo")
        tanpyo = _clean_text_ja(tanpyo_td.get_text(strip=True)) if tanpyo_td else "なし"
        
        detail_cell = tbl.find("td", colspan="5")
        details_text_parts = []
        if detail_cell:
            current_header_info = ""
            for child in detail_cell.children:
                if isinstance(child, NavigableString):
                    continue
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
                            if txt:
                                times.append(txt)
                        time_str = "-".join(times)
                    awase_tr = child.find('tr', class_='awase')
                    awase_str = ""
                    if awase_tr:
                        awase_txt = _clean_text_ja(awase_tr.get_text(strip=True))
                        if awase_txt:
                            awase_str = f" (併せ: {awase_txt})"
                    if current_header_info or time_str:
                        details_text_parts.append(f"[{current_header_info}] {time_str}{awase_str}")
                    current_header_info = ""
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
def calculate_passing_order_bonus(pass_str: str, final_rank: int) -> float:
    """
    通過順による近走指数ボーナス計算
    例: 10-10-14 -> 6
    10->14で4つ下がったが、最終着順6(14より良い)なのでボーナス8.0
    """
    if not pass_str or pass_str == "-":
        return 0.0
    
    clean_pass = re.sub(r"\(.*?\)", "", pass_str).strip()
    parts = clean_pass.split("-")
    
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
                return 8.0
            
            # 条件②: 2つ以上下がったのに、最終着順がその「下がった位置」より良い
            if drop >= 2 and final_rank < curr:
                max_bonus = max(max_bonus, 5.0)
                
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
        
        # 直近3走を取得
        for td in tr.find_all("td", class_="Past")[:3]:
            if "Rest" in td.get("class", []):
                past_str_list.append("(放牧/休養)")
            else:
                d01 = td.find("div", class_="Data01")
                date_place = ""
                d02 = td.find("div", class_="Data02")
                race_name_dist = ""
                
                if d01:
                    first_span = d01.find("span")
                    if first_span: date_place = _clean_text_ja(first_span.get_text(strip=True))
                    else: date_place = _clean_text_ja(d01.get_text(strip=True))
                
                if d02:
                    race_name_dist = _clean_text_ja(d02.get_text(strip=True))

                rank_tag = td.find("span", class_="Num") or td.find("div", class_="Rank")
                rank = rank_tag.get_text(strip=True) if rank_tag else "?"
                
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
                    
                    # 近走指数計算用データ
                    bonus = calculate_passing_order_bonus(passing_order, rank_int)
                    valid_runs.append({"rank_int": rank_int, "bonus": bonus})
                except: pass
        
        # 近走指数計算
        # 基本点: 5着以内回数 * 1.0
        base_score = sum(1.0 for r in valid_runs if r["rank_int"] <= 5)
        # ボーナスの最大値を加算
        max_bonus = max([r["bonus"] for r in valid_runs], default=0.0)
        
        final_index_val = base_score + max_bonus
        final_index = float(min(final_index_val, 10.0))
        
        data[umaban] = {
            "jockey": jockey, 
            "past": past_str_list, 
            "kinsou_index": final_index,
        }
    return data


# ==================================================
# Yahooスポーツナビ 対戦表取得ロジック
# ==================================================
def fetch_yahoo_matrix_data(driver, year, place, kai, day, race_num, current_distance_str):
    """
    Yahoo!スポーツナビの対戦成績ページをスクレイピングし、
    Netkeibaリンク付きの対戦表テキストを生成する
    """
    # YahooのURL構築 (例: 2608010603)
    # 年(2桁) + 場所(2桁) + 回(2桁) + 日(2桁) + レース(2桁)
    
    # Netkeiba用場所コードはYahooでも共通と仮定（JRAコード）
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place:
        return "場所コードエラーにより対戦表取得不可"
        
    y_year = year[-2:] # 下2桁
    yahoo_race_id = f"{y_year}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    
    url = f"https://sports.yahoo.co.jp/keiba/race/matrix/{yahoo_race_id}"
    driver.get(url)
    
    try:
        # テーブルが表示されるまで待機（クラス名は提供されたHTMLに基づく）
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "hr-tableLeftTop--matrix")))
    except:
        return "対戦データなし (Yahooページ取得タイムアウト)"
        
    soup = BeautifulSoup(driver.page_source, "html.parser")
    table = soup.find("table", class_="hr-tableLeftTop--matrix")
    if not table:
        return "対戦データなし"
        
    # --- 1. ヘッダー情報の解析（過去レース情報） ---
    thead = table.find("thead")
    if not thead: return "対戦データ構造エラー"
    
    past_races = []
    # 最初のthは「開催日/レース名...」の見出しなのでスキップ
    header_th_list = thead.find_all("th")[1:]
    
    for th in header_th_list:
        # レースリンク取得 (例: /keiba/race/index/2608010202)
        link_tag = th.find("a")
        if not link_tag:
            past_races.append(None) # リンクがない列（想定外だが）
            continue
            
        href = link_tag.get("href") # /keiba/race/index/2608010202
        y_past_id = href.split("/")[-1]
        
        race_name = link_tag.get_text(strip=True)
        
        # 日付抽出 (span class="hr-tableLeftTop__item--date")
        date_span = th.find("span", class_="hr-tableLeftTop__item--date")
        race_date = date_span.get_text(" ", strip=True) if date_span else "日付不明"
        
        # 距離抽出 (3番目のspanだが、構造依存を避けるためテキスト全体から探すか、itemクラスを舐める)
        # HTML例: <span class="hr-tableLeftTop__item">ダ1900m</span>
        items = th.find_all("span", class_="hr-tableLeftTop__item")
        race_dist_str = ""
        for item in items:
            t = item.get_text(strip=True)
            if "m" in t and ("芝" in t or "ダ" in t or "障" in t):
                race_dist_str = t
                break
        
        past_races.append({
            "id": y_past_id,
            "name": race_name,
            "date": race_date,
            "dist_str": race_dist_str
        })
        
    # --- 2. 行データの解析（各馬の着順） ---
    tbody = table.find("tbody")
    if not tbody: return "対戦データなし"
    
    # matrix_data: { yahoo_race_id: [ {horse:name, rank:10}, ... ] }
    matrix_data = {}
    
    rows = tbody.find_all("tr")
    for tr in rows:
        # 馬名取得 (th a)
        th_horse = tr.find("th")
        if not th_horse: continue
        horse_a = th_horse.find("a")
        if not horse_a: continue
        horse_name = horse_a.get_text(strip=True)
        
        # 結果セル取得
        cells = tr.find_all("td")
        for idx, td in enumerate(cells):
            if idx >= len(past_races): break
            race_info = past_races[idx]
            if not race_info: continue
            
            # 着順抽出
            # <td class="hr-tableLeftTop__data"><span>10</span>(2.2)<br>57.0</td>
            # 不参加の場合は "-"
            txt = td.get_text(strip=True)
            if "-" in txt and len(txt) < 5: # 簡易判定
                continue
                
            rank_span = td.find("span")
            if rank_span:
                rank = rank_span.get_text(strip=True)
                
                rid = race_info["id"]
                if rid not in matrix_data:
                    matrix_data[rid] = {
                        "info": race_info,
                        "results": []
                    }
                matrix_data[rid]["results"].append({
                    "name": horse_name,
                    "rank": rank
                })

    # --- 3. 出力テキスト生成 ---
    if not matrix_data:
        return "対戦データなし"
        
    current_dist_int = extract_distance_int(current_distance_str)
    
    # 複数頭が出走しているレースのみフィルタリング
    valid_battles = []
    for rid, data in matrix_data.items():
        if len(data["results"]) >= 2:
            valid_battles.append(data)
            
    if not valid_battles:
        return "対戦データなし（該当レースなし）"
        
    # ID降順（新しい順）
    valid_battles.sort(key=lambda x: x["info"]["id"], reverse=True)
    
    output_lines = ["\n【対戦表】"]
    
    for battle in valid_battles:
        info = battle["info"]
        results = battle["results"]
        
        # 着順ソート (数字変換できるものは数字で、以外は後ろへ)
        def rank_key(r):
            try: return int(re.sub(r"\D", "", r["rank"]))
            except: return 999
        results.sort(key=rank_key)
        
        # 距離差計算
        past_dist_int = extract_distance_int(info["dist_str"])
        diff = past_dist_int - current_dist_int
        diff_str = f"{diff:+}m" if diff != 0 else "±0m"
        
        # Netkeiba URL生成 (Yahoo ID "2608..." -> Netkeiba "202608...")
        # 2000年以降前提
        nk_full_id = "20" + info["id"]
        nk_url = f"https://race.netkeiba.com/race/result.html?race_id={nk_full_id}&rf=race_list"
        
        # フォーマット構築
        # ・2026年1月5日 未勝利 ダ1900m(±0m)
        header = f"・{info['date'].replace(' ', '')} {info['name']} {info['dist_str']}({diff_str})"
        url_line = f"URL：{nk_url}"
        
        # 着順：4着フィサブロス　5着テスタヴェローチェ
        res_str_list = [f"{r['rank']}着{r['name']}" for r in results]
        res_line = "着順：" + "　".join(res_str_list)
        
        output_lines.append(header)
        output_lines.append(url_line)
        output_lines.append(res_line)
        output_lines.append("")
        
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
                
                # 1. 競馬ブック談話
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
                
                # 4. Netkeibaデータ（近走指数用）
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
                    sp_str = f"スピード指数:{sp_val}/35点" # AIに分かりやすく表記
                    
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

                # 5. 対戦表生成（Yahoo!スポーツナビから取得）
                current_dist_str = extract_race_info(race_title).get("distance", "")
                battle_matrix_text = fetch_yahoo_matrix_data(driver, year, place, kai, day, race_num_str, current_dist_str)

                # AIへの入力プロンプト（対戦表は含めない）
                full_prompt = f"■レース情報\n{race_title}\n\n■各馬詳細\n" + "\n".join(lines)
                
                status.text("AI分析中...")
                result_area = st.empty()
                ai_output = ""
                
                # Difyからのストリーミング回答を表示
                for chunk in stream_dify_workflow(full_prompt):
                    ai_output += chunk
                    result_area.markdown(ai_output + "▌")
                
                # AI回答後に対戦表を結合
                final_output = ai_output + "\n\n" + battle_matrix_text
                
                # 最終結果表示
                result_area.markdown(final_output)
                
                # ログとコピーボタンにも結合データを入れる
                full_output_log += f"\n{race_title}\n{final_output}\n"
                render_copy_button(final_output, f"{r}Rコピー", f"cp_{base_id}_{r}")
                
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
