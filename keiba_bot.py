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
# スピード指数
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

    hensachi_scores = {}
    for umaban, raw in raw_scores.items():
        if std == 0:
            hensachi = 50.0
        else:
            hensachi = 50.0 + 10.0 * (raw - mean) / std
        hensachi_scores[umaban] = hensachi

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
# 馬場バイアス評価
# ==================================================
def extract_race_info(race_title: str) -> dict:
    result = {"place": None, "distance": None, "track_type": None, "day": None, "course_variant": ""}
    place_day_match = re.search(r'(\d+)回([^0-9]+?)(\d+)日目', race_title)
    if place_day_match:
        result["place"] = place_day_match.group(2).strip()
        result["day"] = int(place_day_match.group(3))
    
    distance_match = re.search(r'(\d{3,4})m', race_title)
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
    debug_info = []
    
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
# Scraping Parsers
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
    current_umaban, current_waku = None, None

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
            anchor = bamei_td.find("a")
            raw_name = anchor.get_text(strip=True) if anchor else bamei_td.get_text(strip=True)
            if current_umaban:
                horses[current_umaban] = {
                    "name": _clean_text_ja(raw_name),
                    "waku": current_waku if current_waku else "?",
                    "danwa": ""
                }
            continue

        danwa_td = tr.find("td", class_="danwa")
        if danwa_td and current_umaban:
            comment = _clean_text_ja(danwa_td.get_text("\n", strip=True))
            if horses[current_umaban]["danwa"]:
                horses[current_umaban]["danwa"] += (" " + comment)
            else:
                horses[current_umaban]["danwa"] = comment
    return horses


def fetch_keibabook_danwa(driver, race_id: str):
    driver.get(f"{BASE_URL}/cyuou/danwa/0/{race_id}")
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa")))
    except:
        pass
    html = driver.page_source
    return parse_race_info_from_danwa(html), parse_danwa_horses(html)


def parse_keibabook_chokyo(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}
    tables = soup.find_all("table", class_="cyokyo")
    for tbl in tables:
        umaban_td = tbl.find("td", class_="umaban")
        if not umaban_td: continue
        umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
        tanpyo_td = tbl.find("td", class_="tanpyo")
        tanpyo = _clean_text_ja(tanpyo_td.get_text(strip=True)) if tanpyo_td else "なし"
        details = []
        detail_cell = tbl.find("td", colspan="5")
        if detail_cell:
            for child in detail_cell.children:
                if child.name == "dl":
                    details.append(" ".join([c.get_text(strip=True) for c in child.find_all(["dt", "dd"])]))
                elif child.name == "table" and "cyokyodata" in child.get("class", []):
                    time_tr = child.find("tr", class_="time")
                    if time_tr: details.append(" ".join([td.get_text(strip=True) for td in time_tr.find_all("td")]))
                    awase = child.find("tr", class_="awase")
                    if awase: details.append(_clean_text_ja(awase.get_text(strip=True)))
            semekaisetu = detail_cell.find("div", class_="semekaisetu")
            if semekaisetu:
                details.append(f"[攻め解説] {_clean_text_ja(semekaisetu.get_text(strip=True))}")
        
        full = " ".join(details)
        data[umaban] = {"tanpyo": tanpyo, "details": re.sub(r"\s+", " ", full).strip()}
    return data


def fetch_keibabook_chokyo(driver, race_id: str):
    driver.get(f"{BASE_URL}/cyuou/cyokyo/0/{race_id}")
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cyokyo")))
    except: pass
    return parse_keibabook_chokyo(driver.page_source)


def parse_zenkoso_interview(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "syoin" in str(c))
    if not table or not table.tbody: return {}
    data = {}
    current_umaban = None
    for tr in table.tbody.find_all("tr", recursive=False):
        if "spacer" in tr.get("class", []): continue
        umaban_td = tr.find("td", class_="umaban")
        if umaban_td:
            current_umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            continue
        syoin_td = tr.find("td", class_="syoin")
        if syoin_td and current_umaban:
            meta = syoin_td.find("div", class_="syoindata")
            if meta: meta.decompose()
            clean = _clean_text_ja(syoin_td.get_text(" ", strip=True))
            if not _is_missing_marker(clean) and len(clean) > 1:
                data[current_umaban] = clean
            current_umaban = None
    return data


def fetch_zenkoso_interview(driver, race_id: str):
    driver.get(f"{BASE_URL}/cyuou/syoin/{race_id}")
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.syoin")))
    except: pass
    return parse_zenkoso_interview(driver.page_source)


def parse_keibabook_cpu(html: str, is_shinba: bool = False) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}
    speed_tbl = soup.find("table", id="cpu_speed_sort_table")
    if speed_tbl and speed_tbl.tbody:
        for tr in speed_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban: continue
            tds = tr.find_all("td")
            if len(tds) < 8: continue
            
            def get_v(idx):
                p = tds[idx].find("p")
                t = re.sub(r"\D", "", p.get_text(strip=True)) if p else ""
                v = int(t) if t else 0
                return v if v < 900 else 0
            
            last, two, thr = get_v(-1), get_v(-2), get_v(-3)
            vals = [x for x in [last, two, thr] if x > 0]
            avg = round(sum(vals)/len(vals)) if vals else 0
            data[umaban] = {"sp_last": str(last or "-"), "sp_2": str(two or "-"), "sp_3": str(thr or "-"), "sp_avg": str(avg or "-")}

    factor_tbl = None
    for t in soup.find_all("table"):
        if t.find("caption") and "ファクター" in t.find("caption").get_text():
            factor_tbl = t; break
    
    if factor_tbl and factor_tbl.tbody:
        for tr in factor_tbl.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td: continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            tds = tr.find_all("td")
            if len(tds) < 6: continue
            def get_m(idx):
                if idx >= len(tds): return "-"
                p = tds[idx].find("p")
                return p.get_text(strip=True) if p else "-"
            
            if umaban not in data: data[umaban] = {}
            if is_shinba:
                data[umaban].update({"fac_deashi": get_m(5), "fac_kettou": get_m(6), "fac_ugoki": get_m(8)})
            else:
                data[umaban].update({"fac_crs": get_m(5), "fac_dis": get_m(6), "fac_zen": get_m(7)})
    return data


def fetch_keibabook_cpu_data(driver, race_id: str, is_shinba: bool = False):
    driver.get(f"{BASE_URL}/cyuou/cpu/{race_id}")
    try: WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "main")))
    except: pass
    return parse_keibabook_cpu(driver.page_source, is_shinba)


def fetch_netkeiba_data(driver, year, kai, place, day, race_num):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place, "")
    if not nk_place: return {}
    nk_race_id = f"{year}{nk_place}{kai.zfill(2)}{day.zfill(2)}{race_num.zfill(2)}"
    driver.get(f"https://race.netkeiba.com/race/shutuba_past.html?race_id={nk_race_id}&rf=shutuba_submenu")
    try: WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "Shutuba_Past5_Table")))
    except: return {}
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {}
    for tr in soup.find_all("tr", class_="HorseList"):
        waku_tds = tr.find_all("td", class_="Waku")
        umaban = ""
        for td in waku_tds:
            t = re.sub(r"\D", "", td.get_text(strip=True))
            if t: umaban = t; break
        if not umaban: continue
        
        jockey_td = tr.find("td", class_="Jockey")
        jockey = "不明"
        if jockey_td:
            a_tag = jockey_td.find("a")
            jockey = a_tag.get_text(strip=True) if a_tag else jockey_td.get_text(strip=True)
        jockey = _clean_text_ja(jockey)
        
        past_list = []
        for td in tr.find_all("td", class_="Past")[:3]:
            if "Rest" in td.get("class", []): past_list.append("(休)")
            else: past_list.append(_parse_netkeiba_past_td(td))
        data[umaban] = {"jockey": jockey, "past": past_list}
    return data


# ==================================================
# Dify Streaming
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY:
        yield "⚠️ DIFY_API_KEY 未設定"; return
    
    payload = {"inputs": {"text": full_text}, "response_mode": "streaming", "user": "keiba-bot"}
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"}
    
    try:
        res = requests.post("https://api.dify.ai/v1/workflows/run", headers=headers, json=payload, stream=True, timeout=90)
        for line in res.iter_lines():
            if not line: continue
            decoded = line.decode("utf-8").replace("data: ", "")
            try:
                data = json.loads(decoded)
                if data.get("event") == "message" or "answer" in data:
                    yield data.get("answer", "")
            except: pass
    except Exception as e:
        yield f"Error: {e}"


# ==================================================
# Main Execution (Batch)
# ==================================================
def run_batch_prediction(jobs_config):
    driver = build_driver()
    full_output_log = ""
    try:
        st.info(f"ログイン中... (ID: {KEIBA_ID[:2]}**)")
        login_keibabook(driver)
        st.success("ログイン成功。連続実行を開始します。")

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

            for r in target_races:
                race_num_str = f"{r:02}"
                race_id = base_id + race_num_str
                
                st.markdown(f"### {place_name} {r}R")
                status = st.empty()
                status.text("データ収集中...")
                
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
                
                status.text("AI分析中...")
                result_area = st.empty()
                ai_output = ""
                for chunk in stream_dify_workflow(full_prompt):
                    ai_output += chunk
                    result_area.markdown(ai_output + "▌")
                
                result_area.markdown(ai_output)
                full_output_log += f"\n--- {place_name} {r}R ---\n{ai_output}"
                render_copy_button(ai_output, f"{place_name}{r}R コピー", f"copy_{job_idx}_{r}")
                status.success("完了")
            
            st.success(f"✅ {place_name}開催分の処理が完了しました")
            
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
    finally:
        driver.quit()
        
    return full_output_log
