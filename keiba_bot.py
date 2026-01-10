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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup

# ==================================================
# 【設定エリア】secretsから読み込み
# ==================================================
# .streamlit/secrets.toml に以下の設定が必要です
KEIBA_ID = st.secrets.get("KEIBA_ID", "")
KEIBA_PASS = st.secrets.get("KEIBA_PASS", "")
NETKEIBA_ID = st.secrets.get("NETKEIBA_ID", "")
NETKEIBA_PASS = st.secrets.get("NETKEIBA_PASS", "")

DIFY_API_KEY = st.secrets.get("DIFY_API_KEY", "")

# デフォルト設定（UI操作で上書きされます）
YEAR = "2026"
KAI = "01"
PLACE = "02"
DAY = "01"

BASE_URL = "https://s.keibabook.co.jp"

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}

# 競馬ブック PLACEコード → netkeiba 競馬場コード変換
KEIBABOOK_TO_NETKEIBA_PLACE = {
    "08": "01",  # 札幌
    "09": "02",  # 函館
    "06": "03",  # 福島
    "07": "04",  # 新潟
    "04": "05",  # 東京
    "05": "06",  # 中山
    "02": "07",  # 中京
    "00": "08",  # 京都
    "01": "09",  # 阪神
    "03": "10",  # 小倉
}

def set_race_params(year, kai, place, day):
    """グローバル変数を更新"""
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)

def get_current_params():
    """現在のパラメータ（UI表示用）"""
    return YEAR, KAI, PLACE, DAY


# ==================================================
# ★netkeiba 指数セル正規化
# ==================================================
def normalize_netkeiba_index_cell(raw: str) -> str:
    if raw is None:
        return "無"
    t = str(raw).replace("\xa0", " ").strip()
    if t == "":
        return "無"
    if "未" in t or "－" in t or "-" in t:
        return "無"
    nums = re.findall(r"\d+", t)
    if not nums:
        return "無"
    if any(n == "1000" for n in nums):
        short = [n for n in nums if len(n) <= 3 and n != "1000"]
        return short[-1] if short else "無"
    short = [n for n in nums if len(n) <= 3]
    if short:
        return short[-1]
    return "無"


# ==================================================
# ワンクリックコピー
# ==================================================
def render_copy_button(text: str, label: str, dom_id: str):
    safe_text = json.dumps(text)
    html = f"""
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <button id="{dom_id}" style="
        padding:8px 12px; border-radius:10px; border:1px solid #ddd;
        background:#fff; cursor:pointer; font-size:14px;
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
# Selenium Driver (Bot対策強化版)
# ==================================================
def build_driver() -> webdriver.Chrome:
    options = Options()

    # Headlessモード設定（最新版）
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # Bot対策設定
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # User-Agent固定
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument(f"--user-agent={user_agent}")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)

    # navigator.webdriver を undefined に偽装
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ja-JP', 'ja']
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3]
                });
                """
            },
        )
    except Exception:
        pass

    return driver


# ==================================================
# ログイン処理
# ==================================================
def login_keibabook(driver: webdriver.Chrome) -> None:
    if not KEIBA_ID or not KEIBA_PASS:
        raise RuntimeError("KEIBA_ID / KEIBA_PASS が secrets に設定されていません。")

    driver.get(f"{BASE_URL}/login/login")
    wait = WebDriverWait(driver, 15)
    
    wait.until(EC.visibility_of_element_located((By.NAME, "login_id"))).send_keys(KEIBA_ID)
    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))).send_keys(KEIBA_PASS)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'], .btn-login"))).click()
    time.sleep(1.2)


def login_netkeiba(driver: webdriver.Chrome) -> bool:
    if not NETKEIBA_ID or not NETKEIBA_PASS:
        print("netkeiba ID/PASS not found.")
        return False

    # 既にログイン済みかチェック
    try:
        driver.get("https://www.netkeiba.com/")
        if "ログアウト" in driver.page_source or "action=logout" in driver.page_source:
            return True
    except Exception:
        pass

    try:
        # ★ユーザー指定のURLを使用
        login_url = "https://regist.netkeiba.com/account/?pid=login"
        driver.get(login_url)
        wait = WebDriverWait(driver, 10)

        # ID入力 (name="login_id" が一般的だが念のため wait)
        id_el = wait.until(EC.visibility_of_element_located((By.NAME, "login_id")))
        id_el.clear()
        id_el.send_keys(NETKEIBA_ID)

        # PW入力
        pw_el = wait.until(EC.visibility_of_element_located((By.NAME, "pswd")))
        pw_el.clear()
        pw_el.send_keys(NETKEIBA_PASS)

        # ログインボタン押下
        # このページは複数のログインボタンがある可能性があるため、フォーム内の送信ボタンを優先
        btn_candidates = [
            (By.CSS_SELECTOR, "input[type='image'][alt='ログイン']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, ".Btn_Login"),
            (By.XPATH, "//button[contains(text(), 'ログイン')]"),
            (By.XPATH, "//input[@value='ログイン']"),
        ]
        
        clicked = False
        for how, sel in btn_candidates:
            try:
                # フォーム近くのボタンであることを確認したいが、まずは見つかったものをクリック
                btn = driver.find_element(how, sel)
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue
        
        if not clicked:
            print("Login button not found.")
            return False

        # ページ遷移待機（URL変化 or ログアウトボタン出現）
        # accountページからトップなどに遷移するのを待つ
        try:
            wait.until(lambda d: "pid=login" not in d.current_url)
        except TimeoutException:
            pass # タイムアウトしても成功している場合がある

        time.sleep(2.0)

        html = driver.page_source
        if "ログアウト" in html or "action=logout" in html:
            return True
        
        # 失敗時ログ
        print(f"Login failed. Current URL: {driver.current_url}")
        return False

    except Exception as e:
        print(f"netkeiba login exception: {e}")
        return False


# ==================================================
# Parser：競馬ブック
# ==================================================
def parse_race_info(html: str):
    soup = BeautifulSoup(html, "html.parser")
    racetitle = soup.find("div", class_="racetitle")
    if not racetitle:
        return {"date_meet": "", "race_name": "", "cond1": "", "course_line": ""}

    racemei = racetitle.find("div", class_="racemei")
    date_meet = ""
    race_name = ""
    if racemei:
        ps = racemei.find_all("p")
        if len(ps) >= 1:
            date_meet = ps[0].get_text(strip=True)
        if len(ps) >= 2:
            race_name = ps[1].get_text(strip=True)

    racetitle_sub = racetitle.find("div", class_="racetitle_sub")
    cond1 = ""
    course_line = ""
    if racetitle_sub:
        sub_ps = racetitle_sub.find_all("p")
        if len(sub_ps) >= 1:
            cond1 = sub_ps[0].get_text(strip=True)
        if len(sub_ps) >= 2:
            course_line = sub_ps[1].get_text(" ", strip=True)

    return {"date_meet": date_meet, "race_name": race_name, "cond1": cond1, "course_line": course_line}

def parse_danwa_comments(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="danwa")
    if not table or not table.tbody:
        return {}
    danwa_dict = {}
    current_key = None
    for row in table.tbody.find_all("tr"):
        uma_td = row.find("td", class_="umaban")
        bamei_td = row.find("td", class_="bamei")
        if uma_td:
            text = re.sub(r"\D", "", uma_td.get_text(strip=True))
            if text:
                current_key = text
                continue
        if bamei_td and not current_key:
            text = bamei_td.get_text(strip=True)
            if text:
                current_key = text
                continue
        danwa_td = row.find("td", class_="danwa")
        if danwa_td and current_key:
            danwa_dict[current_key] = danwa_td.get_text(strip=True)
            current_key = None
    return danwa_dict

def parse_zenkoso_interview(html: str):
    soup = BeautifulSoup(html, "html.parser")
    h2 = soup.find("h2", string=lambda s: s and "前走" in s)
    if not h2: return {}
    table = h2.find_next("table", class_="syoin")
    if not table or not table.tbody: return {}
    rows = table.tbody.find_all("tr")
    result_dict = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        if "spacer" in (row.get("class") or []):
            i += 1
            continue
        uma_td = row.find("td", class_="umaban")
        bamei_td = row.find("td", class_="bamei")
        if not (uma_td and bamei_td):
            i += 1
            continue
        umaban = re.sub(r"\D", "", uma_td.get_text(strip=True))
        name = bamei_td.get_text(strip=True)
        
        prev_date = ""
        prev_class = ""
        prev_finish = ""
        prev_comment = ""
        detail = rows[i+1] if i+1 < len(rows) else None
        if detail:
            syoin_td = detail.find("td", class_="syoin")
            if syoin_td:
                sdata = syoin_td.find("div", class_="syoindata")
                if sdata:
                    ps = sdata.find_all("p")
                    if ps: prev_date = ps[0].get_text(strip=True)
                    if len(ps) >= 2:
                        spans = ps[1].find_all("span")
                        if len(spans) >= 1: prev_class = spans[0].get_text(strip=True)
                        if len(spans) >= 2: prev_finish = spans[1].get_text(strip=True)
                direct = syoin_td.find_all("p", recursive=False)
                if direct:
                    txt = direct[0].get_text(strip=True)
                    if txt != "－": prev_comment = txt
        if umaban:
            result_dict[umaban] = {
                "umaban": umaban, "name": name, "prev_date_course": prev_date,
                "prev_class": prev_class, "prev_finish": prev_finish, "prev_comment": prev_comment
            }
        i += 2
    return result_dict

def parse_cyokyo(html: str):
    soup = BeautifulSoup(html, "html.parser")
    cyokyo_dict = {}
    section = None
    h2 = soup.find("h2", string=lambda s: s and ("調教" in s or "中間" in s))
    if h2:
        midasi_div = h2.find_parent("div", class_="midasi")
        if midasi_div:
            section = midasi_div.find_next_sibling("div", class_="section")
    if section is None: section = soup
    tables = section.find_all("table", class_="cyokyo")
    for tbl in tables:
        tbody = tbl.find("tbody")
        if not tbody: continue
        rows = tbody.find_all("tr", recursive=False)
        if len(rows) < 1: continue
        header = rows[0]
        uma_td = header.find("td", class_="umaban")
        name_td = header.find("td", class_="kbamei")
        umaban = re.sub(r"\D", "", uma_td.get_text(strip=True)) if uma_td else ""
        bamei_hint = name_td.get_text(" ", strip=True) if name_td else ""
        tanpyo_td = header.find("td", class_="tanpyo")
        tanpyo = tanpyo_td.get_text(strip=True) if tanpyo_td else ""
        detail_row = rows[1] if len(rows) >= 2 else None
        detail_text = detail_row.get_text(" ", strip=True) if detail_row else ""
        payload = {"tanpyo": tanpyo, "detail": detail_text, "bamei_hint": bamei_hint}
        if umaban: cyokyo_dict[umaban] = payload
        elif bamei_hint: cyokyo_dict[bamei_hint] = payload
    return cyokyo_dict

def parse_syutuba(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "syutuba_sp" in c.split())
    if not table: table = soup.find("table", class_=lambda c: c and "syutuba" in c)
    if not table or not table.tbody: return {}
    result = {}
    for tr in table.tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if not tds: continue
        umaban = re.sub(r"\D", "", tds[0].get_text(strip=True))
        if not umaban: continue
        kbamei_p = tr.find("p", class_="kbamei")
        bamei = kbamei_p.get_text(" ", strip=True) if kbamei_p else ""
        kisyu = ""
        kisyu_change = False
        kisyu_p = tr.find("p", class_="kisyu")
        if kisyu_p:
            a = kisyu_p.find("a")
            if a:
                norika = a.find("span", class_="norikawari")
                if norika:
                    kisyu_change = True
                    kisyu = norika.get_text(strip=True)
                else:
                    kisyu = a.get_text(strip=True)
            else:
                kisyu = kisyu_p.get_text(" ", strip=True)
        result[umaban] = {"umaban": umaban, "bamei": bamei, "kisyu": kisyu, "kisyu_change": kisyu_change}
    return result


# ==================================================
# Parser：netkeiba (Speed Index & Past)
# ==================================================
def parse_netkeiba_speed_index(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and ("SpeedIndex_Table" in c))
    if not table or not table.tbody: return {}
    out = {}
    for tr in table.tbody.find_all("tr", class_=lambda c: c and ("HorseList" in c.split()), recursive=False):
        um_td = tr.find("td", class_=lambda c: c and "sk__umaban" in c)
        if not um_td: continue
        umaban = re.sub(r"\D", "", um_td.get_text(" ", strip=True))
        if not umaban: continue
        def cell_text(cell_class):
            td = tr.find("td", class_=lambda c: c and cell_class in c.split())
            if not td: return "無"
            return normalize_netkeiba_index_cell(td.get_text(" ", strip=True))
        out[umaban] = {
            "index1": cell_text("sk__index1"),
            "index2": cell_text("sk__index2"),
            "index3": cell_text("sk__index3"),
            "course": cell_text("sk__max_course_index"),
            "avg5": cell_text("sk__average_index"),
        }
    return out

def parse_netkeiba_past_and_rest(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and ("SpeedIndex_Table" in c))
    if not table or not table.tbody: return {}
    out = {}
    for tr in table.tbody.find_all("tr", class_=lambda c: c and ("HorseList" in c.split()), recursive=False):
        um_td = tr.find("td", class_=lambda c: c and "sk__umaban" in c)
        if not um_td: continue
        umaban = re.sub(r"\D", "", um_td.get_text(" ", strip=True))
        if not umaban: continue
        
        rest_td = tr.find("td", class_="Rest")
        rest_text = ""
        if rest_td:
            items = [d.get_text(" ", strip=True) for d in rest_td.find_all("div", class_="Data01")]
            rest_text = " / ".join([x for x in items if x]).strip()

        past_list = []
        past_tds = tr.find_all("td", class_="Past", recursive=False)
        for past_td in past_tds:
            d01 = past_td.find("div", class_="Data01")
            date_place = d01.find("span").get_text(" ", strip=True) if d01 and d01.find("span") else ""
            final_num = d01.find("span", class_="Num").get_text(" ", strip=True) if d01 and d01.find("span", class_="Num") else ""
            
            d02 = past_td.find("div", class_="Data02")
            race_name = d02.get_text(" ", strip=True) if d02 else ""
            d05 = past_td.find("div", class_="Data05")
            course_time = d05.get_text(" ", strip=True) if d05 else ""
            d03 = past_td.find("div", class_="Data03")
            detail = d03.get_text(" ", strip=True) if d03 else ""
            d06 = past_td.find("div", class_="Data06")
            passage = d06.get_text(" ", strip=True) if d06 else ""
            if passage:
                passage += f" /最終{final_num}着" if (final_num and final_num.isdigit()) else f" /最終{final_num}"
            else:
                passage = f"（通過順なし） /最終{final_num}" if final_num else ""
            d07 = past_td.find("div", class_="Data07")
            winner = d07.get_text(" ", strip=True) if d07 else ""

            if not (date_place or race_name or course_time): continue
            race_name = re.sub(r"\s+", " ", race_name).strip()
            past_list.append({
                "date_place": date_place, "race_name": race_name,
                "course_time": course_time, "detail": detail,
                "passage": passage, "winner": winner
            })
        out[umaban] = {"rest": rest_text, "past": past_list[:3]}
    return out


# ==================================================
# Fetchers (Selenium使用)
# ==================================================
def fetch_danwa_dict(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    time.sleep(0.8)
    html = driver.page_source
    return html, parse_race_info(html), parse_danwa_comments(html)

def fetch_zenkoso_dict(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syoin/{race_id}"
    driver.get(url)
    time.sleep(0.8)
    return parse_zenkoso_interview(driver.page_source)

def fetch_cyokyo_dict(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/cyokyo/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.cyokyo")))
    except: pass
    return parse_cyokyo(driver.page_source)

def fetch_syutuba_dict(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syutuba/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.syutuba_sp, table.syutuba")))
    except: pass
    return parse_syutuba(driver.page_source)

def fetch_netkeiba_speed_html(driver, netkeiba_race_id: str) -> str:
    url = f"https://race.netkeiba.com/race/speed.html?race_id={netkeiba_race_id}&type=shutuba&mode=default"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.SpeedIndex_Table")))
    except: pass
    
    html = driver.page_source
    # ログインが必要そうなら再ログイン
    if ("無料会員登録" in html or "ログイン" in html) and NETKEIBA_ID and NETKEIBA_PASS:
        if login_netkeiba(driver):
            driver.get(url)
            time.sleep(1.0)
            html = driver.page_source
    return html

def keibabook_race_id_to_netkeiba_race_id(year, kai, place, day, race_num_2):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place)
    if not nk_place: return ""
    return f"{str(year)}{nk_place}{str(kai).zfill(2)}{str(day).zfill(2)}{str(race_num_2).zfill(2)}"


# ==================================================
# 自動検出
# ==================================================
def detect_meet_candidates():
    driver = build_driver()
    try:
        login_keibabook(driver)
        driver.get(f"{BASE_URL}/cyuou/")
        time.sleep(1.0)
        html = driver.page_source
        keys = re.findall(r"/cyuou/syutuba/(\d{12})", html)
        if not keys:
            keys = re.findall(r"/cyuou/thursday/(\d{12})", html)
        if not keys:
            driver.get(f"{BASE_URL}/")
            time.sleep(1.0)
            html2 = driver.page_source
            keys = re.findall(r"/cyuou/syutuba/(\d{12})", html2)
        
        meet10_set = set(k[:10] for k in keys if len(k) >= 10)
        candidates = []
        for m10 in sorted(meet10_set, reverse=True)[:12]:
            p = m10[6:8]
            candidates.append({
                "meet10": m10,
                "year": m10[0:4], "kai": m10[4:6], "place": p, "day": m10[8:10],
                "place_name": PLACE_NAMES.get(p, "不明")
            })
        return candidates
    except Exception:
        return []
    finally:
        driver.quit()


# ==================================================
# Dify Streaming
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY:
        yield "⚠️ エラー: DIFY_API_KEY が未設定"
        return
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"}
    payload = {"inputs": {"text": full_text}, "response_mode": "streaming", "user": "keiba-bot-user"}
    try:
        res = requests.post("https://api.dify.ai/v1/workflows/run", headers=headers, json=payload, stream=True, timeout=300)
        if res.status_code != 200:
            yield f"⚠️ エラー: Dify API Error {res.status_code}"
            return
        for line in res.iter_lines():
            if not line: continue
            decoded = line.decode("utf-8", errors="ignore")
            if not decoded.startswith("data:"): continue
            json_str = decoded.replace("data: ", "")
            try:
                data = json.loads(json_str)
                event = data.get("event")
                if event == "workflow_finished":
                    outputs = data.get("data", {}).get("outputs", {})
                    if outputs:
                        txt = "\n".join([v for k,v in outputs.items() if isinstance(v, str)])
                        if txt.strip(): yield txt.strip()
                elif chunk := data.get("answer", ""):
                    yield chunk
            except: continue
    except Exception as e:
        yield f"⚠️ Request Error: {str(e)}"


# ==================================================
# メインロジック
# ==================================================
def run_all_races(target_races=None):
    race_numbers = list(range(1, 13)) if target_races is None else sorted({int(r) for r in target_races})
    base_id = f"{YEAR}{KAI}{PLACE}{DAY}"
    place_name = PLACE_NAMES.get(PLACE, "不明")
    
    st.markdown(f"### 🏁 {place_name}開催 データ分析開始")
    
    driver = build_driver()
    combined_blocks = []

    try:
        st.info("🔑 ログイン中...")
        login_keibabook(driver)
        nk_login = login_netkeiba(driver)
        if nk_login: st.success("✅ netkeiba ログイン成功")
        else: st.warning("⚠️ netkeiba ログイン失敗（指数は閲覧できる範囲で取得します）")

        for r in race_numbers:
            race_num = f"{r:02}"
            race_id = base_id + race_num
            nk_race_id = keibabook_race_id_to_netkeiba_race_id(YEAR, KAI, PLACE, DAY, race_num)
            
            st.markdown(f"#### {place_name} {r}R")
            status = st.empty()
            result_area = st.empty()
            status.info("データ収集中...")

            # Fetch
            _html, race_info, danwa = fetch_danwa_dict(driver, race_id)
            zenkoso = fetch_zenkoso_dict(driver, race_id)
            cyokyo = fetch_cyokyo_dict(driver, race_id)
            syutuba = fetch_syutuba_dict(driver, race_id)
            
            # Netkeiba
            speed_dict = {}
            past_rest_dict = {}
            if nk_race_id:
                html_spd = fetch_netkeiba_speed_html(driver, nk_race_id)
                speed_dict = parse_netkeiba_speed_index(html_spd)
                past_rest_dict = parse_netkeiba_past_and_rest(html_spd)

            # Merge
            merged = []
            umaban_list = sorted(syutuba.keys(), key=lambda x: int(x)) if syutuba else []
            if not umaban_list:
                status.warning("出馬表データなし。スキップ")
                continue

            for umaban in umaban_list:
                sb = syutuba.get(umaban, {})
                bamei = sb.get("bamei", "名称不明").strip()
                kisyu = sb.get("kisyu", "騎手不明")
                if sb.get("kisyu_change"): kisyu = f"替・{kisyu}"
                
                # 厩舎コメント
                d_com = danwa.get(umaban) or "情報なし"
                
                # 前走
                z = zenkoso.get(umaban, {})
                z_txt = f"{z.get('prev_date_course','')} {z.get('prev_class','')} {z.get('prev_finish','')}".strip()
                z_com = z.get("prev_comment", "")
                prev_blk = f"  【前走】 {z_txt or '新馬/不明'}\n  【前走談話】 {z_com or 'なし'}\n"
                
                # 調教
                c = cyokyo.get(umaban, {})
                cyokyo_blk = f"  【調教】 短評:{c.get('tanpyo','-')} / 詳細:{c.get('detail','-')}\n"
                
                # 指数
                s = speed_dict.get(umaban, {})
                spd_blk = f"  【指数】 前:{s.get('index1','-')} 2走:{s.get('index2','-')} 3走:{s.get('index3','-')} コース:{s.get('course','-')} 5平:{s.get('avg5','-')}\n"
                
                # 過去走＆休養
                nr = past_rest_dict.get(umaban, {})
                rest = nr.get("rest", "")
                pasts = nr.get("past", [])
                past_lines = ["  【直近走】"]
                for i, p in enumerate(pasts, 1):
                    lbl = "前走" if i==1 else ("2走前" if i==2 else "3走前")
                    line = f"・{lbl}: {p['date_place']} / {p['race_name']} / {p['course_time']} / {p['detail']} / {p['passage']} / {p['winner']}"
                    past_lines.append("  " + line)
                    if i==1 and rest: past_lines.append(f"  ・休養: {rest}")
                if not pasts and rest: past_lines.append(f"  【休み明け】 {rest}")
                past_blk = "\n".join(past_lines) + "\n"

                merged.append(f"▼[馬番{umaban}] {bamei} / 騎手:{kisyu}\n  【厩舎】 {d_com}\n{prev_blk}{cyokyo_blk}{spd_blk}{past_blk}")

            # AI Prompt
            header = f"{race_info.get('date_meet','')}\n{race_info.get('race_name','')}\n{race_info.get('cond1','')}\n{race_info.get('course_line','')}"
            full_prompt = f"■レース情報\n{header}\n\n以下は{place_name}{r}Rの全頭データ。\n■出走馬詳細データ\n" + "\n".join(merged)
            
            status.info("🤖 AI分析中...")
            full_ans = ""
            for chunk in stream_dify_workflow(full_prompt):
                if chunk:
                    full_ans += chunk
                    result_area.markdown(full_ans + "▌")
            result_area.markdown(full_ans)
            
            if full_ans:
                status.success("完了")
                # 履歴保存なし
                combined_blocks.append(f"【{place_name} {r}R】\n{full_ans}\n")
            else:
                status.error("回答生成失敗")

            st.write("---")

    except Exception as e:
        st.error(f"システムエラー: {str(e)}")
    finally:
        driver.quit()
    
    if combined_blocks:
        st.subheader("📌 全レースまとめ")
        all_txt = "\n".join(combined_blocks)
        render_copy_button(all_txt, "📋 全てコピー", "copy_all_btn")
        st.download_button("⬇️ txt保存", all_txt, f"{place_name}_ALL.txt")


# ==================================================
# Streamlit Entry Point
# ==================================================
if __name__ == "__main__":
    st.set_page_config(page_title="Keiba AI Analyst", layout="wide")
    st.title("🏇 AI競馬予想アナリスト")

    with st.sidebar:
        st.header("開催設定")
        
        # 自動検出ボタン
        if st.button("🔄 直近の開催を自動検出"):
            cands = detect_meet_candidates()
            if cands:
                st.session_state["candidates"] = cands
                st.success(f"{len(cands)}件検出")
            else:
                st.warning("検出できませんでした")
        
        # 候補がある場合はセレクトボックス
        candidates = st.session_state.get("candidates", [])
        if candidates:
            opts = [f"{c['meet10']} {c['place_name']}" for c in candidates]
            sel = st.selectbox("開催選択", opts)
            if sel:
                # 選択された候補からパラメータ抽出
                idx = opts.index(sel)
                c = candidates[idx]
                s_year = c['year']
                s_kai = c['kai']
                s_place = c['place']
                s_day = c['day']
        else:
            # 手動入力
            s_year = st.text_input("年 (YYYY)", YEAR)
            s_kai = st.text_input("回 (01~)", KAI)
            s_place = st.selectbox("場所", list(PLACE_NAMES.keys()), format_func=lambda x: f"{x}:{PLACE_NAMES[x]}", index=2)
            s_day = st.text_input("日 (01~)", DAY)

        target_races = st.multiselect("対象レース (空欄なら全レース)", [str(i) for i in range(1, 13)])
        
        if st.button("🚀 分析開始", type="primary"):
            set_race_params(s_year, s_kai, s_place, s_day)
            races = [int(x) for x in target_races] if target_races else None
            run_all_races(races)
