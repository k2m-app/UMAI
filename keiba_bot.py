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
from supabase import create_client, Client

# ==================================================
# 【設定エリア】secretsから読み込み
# ==================================================
KEIBA_ID = st.secrets.get("KEIBA_ID", "")
KEIBA_PASS = st.secrets.get("KEIBA_PASS", "")
DIFY_API_KEY = st.secrets.get("DIFY_API_KEY", "")

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY", "")

# ★追加:netkeiba
NETKEIBA_ID = st.secrets.get("NETKEIBA_ID", "")
NETKEIBA_PASS = st.secrets.get("NETKEIBA_PASS", "")

# デフォルト設定(app.py 側で set_race_params が呼ばれると書き換わる)
YEAR = "2025"
KAI = "04"
PLACE = "02"
DAY = "02"

BASE_URL = "https://s.keibabook.co.jp"

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}

# ★追加:競馬ブック PLACEコード → netkeiba 競馬場コード
# 01札幌 02函館 03福島 04新潟 05東京 06中山 07中京 08京都 09阪神 10小倉
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
    """app.py から開催情報を差し替えるための関数"""
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)

def get_current_params():
    """現在のパラメータ(UI表示用)"""
    return YEAR, KAI, PLACE, DAY


# ==================================================
# ★修正:netkeiba 指数セル正規化(1000は必ず「無」)
# ==================================================
def normalize_netkeiba_index_cell(raw: str) -> str:
    """
    目的:
    - netkeiba指数が「未」「-」のとき内部的に1000になるケースを「無」に統一
    - "1070 70" 等の混在から本命値だけを抽出
    ルール:
    - "1000" は無条件で「無」
    - "未" / "-" / "－" / 空 は「無」
    - 数字は 3桁以下を優先して末尾を採用(70, 54, 107など)
    - それも無ければ「無」
    """
    if raw is None:
        return "無"

    t = str(raw).replace("\xa0", " ").strip()
    if t == "":
        return "無"

    # 明示的な未/欠損
    if "未" in t:
        return "無"
    if "－" in t or "-" in t:
        return "無"

    # 数字抽出
    nums = re.findall(r"\d+", t)
    if not nums:
        return "無"

    # 1000が含まれる(または単体)なら「無」
    if any(n == "1000" for n in nums):
        # ただし "1070 70" のように1000以外の有効値がある場合は有効値を優先
        short = [n for n in nums if len(n) <= 3 and n != "1000"]
        return short[-1] if short else "無"

    # 3桁以下優先(多くの場合、ここが本命の指数)
    short = [n for n in nums if len(n) <= 3]
    if short:
        return short[-1]

    return "無"


# ==================================================
# ワンクリックコピー(components.html + clipboard)
# ==================================================
def render_copy_button(text: str, label: str, dom_id: str):
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
            msg.textContent = "コピーに失敗(ブラウザ制限の可能性)";
            setTimeout(() => msg.textContent = "", 2200);
          }}
        }});
      }})();
    </script>
    """
    components.html(html, height=54)


# ==================================================
# Supabase
# ==================================================
@st.cache_resource
def get_supabase_client() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def save_history(
    year: str,
    kai: str,
    place_code: str,
    place_name: str,
    day: str,
    race_num_str: str,
    race_id: str,
    ai_answer: str,
) -> None:
    supabase = get_supabase_client()
    if supabase is None:
        return

    data = {
        "year": str(year),
        "kai": str(kai),
        "place_code": str(place_code),
        "place_name": place_name,
        "day": str(day),
        "race_num": race_num_str,
        "race_id": race_id,
        "output_text": ai_answer,
    }

    try:
        supabase.table("history").insert(data).execute()
    except Exception as e:
        print("Supabase insert error:", e)


# ==================================================
# Selenium
# ==================================================
def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,2200")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


def login_keibabook(driver: webdriver.Chrome) -> None:
    if not KEIBA_ID or not KEIBA_PASS:
        raise RuntimeError("KEIBA_ID / KEIBA_PASS が secrets に設定されていません。")

    driver.get(f"{BASE_URL}/login/login")

    WebDriverWait(driver, 15).until(
        EC.visibility_of_element_located((By.NAME, "login_id"))
    ).send_keys(KEIBA_ID)

    WebDriverWait(driver, 15).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
    ).send_keys(KEIBA_PASS)

    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'], .btn-login"))
    ).click()

    time.sleep(1.2)


# ★追加:netkeibaログイン(必要なときだけ)
def login_netkeiba(driver: webdriver.Chrome) -> bool:
    """
    成功したら True、失敗/未設定なら False
    """
    if not NETKEIBA_ID or not NETKEIBA_PASS:
        return False

    try:
        driver.get("https://regist.netkeiba.com/?pid=stage_login")
        time.sleep(0.8)

        id_candidates = [
            (By.NAME, "login_id"),
            (By.NAME, "userid"),
            (By.NAME, "id"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ]
        pass_candidates = [
            (By.NAME, "pswd"),
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]

        id_el = None
        for how, sel in id_candidates:
            try:
                id_el = WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((how, sel))
                )
                if id_el:
                    break
            except Exception:
                continue

        pw_el = None
        for how, sel in pass_candidates:
            try:
                pw_el = WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((how, sel))
                )
                if pw_el:
                    break
            except Exception:
                continue

        if not id_el or not pw_el:
            return False

        id_el.clear()
        id_el.send_keys(NETKEIBA_ID)
        pw_el.clear()
        pw_el.send_keys(NETKEIBA_PASS)

        btn_candidates = [
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, ".Btn_Login, .btn_login, .btn"),
        ]
        clicked = False
        for how, sel in btn_candidates:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((how, sel))
                )
                btn.click()
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            return False

        time.sleep(1.2)

        html = driver.page_source
        if "ログアウト" in html or "action=logout" in html:
            return True

        return False

    except Exception:
        return False


# ==================================================
# Parser:共通(競馬ブック)
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

    return {
        "date_meet": date_meet,
        "race_name": race_name,
        "cond1": cond1,
        "course_line": course_line,
    }


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
    if not h2:
        return {}

    table = h2.find_next("table", class_="syoin")
    if not table or not table.tbody:
        return {}

    rows = table.tbody.find_all("tr")
    result_dict = {}

    i = 0
    while i < len(rows):
        row = rows[i]
        if "spacer" in (row.get("class") or []):
            i += 1
            continue

        waku_td = row.find("td", class_="waku")
        uma_td = row.find("td", class_="umaban")
        bamei_td = row.find("td", class_="bamei")

        if not (waku_td and uma_td and bamei_td):
            i += 1
            continue

        waku = waku_td.get_text(strip=True)
        umaban = re.sub(r"\D", "", uma_td.get_text(strip=True))
        name = bamei_td.get_text(strip=True)

        prev_date = ""
        prev_class = ""
        prev_finish = ""
        prev_comment = ""

        detail = rows[i + 1] if i + 1 < len(rows) else None
        if detail:
            syoin_td = detail.find("td", class_="syoin")
            if syoin_td:
                sdata = syoin_td.find("div", class_="syoindata")
                if sdata:
                    ps = sdata.find_all("p")
                    if ps:
                        prev_date = ps[0].get_text(strip=True)
                    if len(ps) >= 2:
                        spans = ps[1].find_all("span")
                        if len(spans) >= 1:
                            prev_class = spans[0].get_text(strip=True)
                        if len(spans) >= 2:
                            prev_finish = spans[1].get_text(strip=True)

                direct = syoin_td.find_all("p", recursive=False)
                if direct:
                    txt = direct[0].get_text(strip=True)
                    if txt != "－":
                        prev_comment = txt

        if umaban:
            result_dict[umaban] = {
                "waku": waku,
                "umaban": umaban,
                "name": name,
                "prev_date_course": prev_date,
                "prev_class": prev_class,
                "prev_finish": prev_finish,
                "prev_comment": prev_comment,
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
    if section is None:
        section = soup

    tables = section.find_all("table", class_="cyokyo")
    for tbl in tables:
        tbody = tbl.find("tbody")
        if not tbody:
            continue
        rows = tbody.find_all("tr", recursive=False)
        if len(rows) < 1:
            continue

        header = rows[0]
        uma_td = header.find("td", class_="umaban")
        name_td = header.find("td", class_="kbamei")

        umaban_text = uma_td.get_text(strip=True) if uma_td else ""
        umaban = re.sub(r"\D", "", umaban_text)

        bamei_hint = ""
        if name_td:
            bamei_hint = name_td.get_text(" ", strip=True)

        tanpyo_td = header.find("td", class_="tanpyo")
        tanpyo = tanpyo_td.get_text(strip=True) if tanpyo_td else ""

        detail_row = rows[1] if len(rows) >= 2 else None
        detail_text = detail_row.get_text(" ", strip=True) if detail_row else ""

        payload = {"tanpyo": tanpyo, "detail": detail_text, "bamei_hint": bamei_hint}

        if umaban:
            cyokyo_dict[umaban] = payload
        else:
            if bamei_hint:
                cyokyo_dict[bamei_hint] = payload

    return cyokyo_dict


def parse_syutuba(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", class_=lambda c: c and "syutuba_sp" in c.split())
    if not table:
        table = soup.find("table", class_=lambda c: c and "syutuba" in c)

    if not table or not table.tbody:
        return {}

    result = {}
    for tr in table.tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        umaban_raw = tds[0].get_text(strip=True)
        umaban = re.sub(r"\D", "", umaban_raw)
        if not umaban:
            continue

        bamei = ""
        kbamei_p = tr.find("p", class_="kbamei")
        if kbamei_p:
            bamei = kbamei_p.get_text(" ", strip=True)

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
                norika = kisyu_p.find("span", class_="norikawari")
                if norika:
                    kisyu_change = True
                    kisyu = norika.get_text(strip=True)
                else:
                    kisyu = kisyu_p.get_text(" ", strip=True)

        result[umaban] = {
            "umaban": umaban,
            "bamei": bamei,
            "kisyu": kisyu,
            "kisyu_change": kisyu_change,
        }

    return result


# ==================================================
# ★修正強化:netkeiba タイム指数 parser(1000→無 を適用)
# ==================================================
def parse_netkeiba_speed_index(html: str) -> dict:
    """
    netkeiba speed.html の出馬表から指数を抜く。
    HTML構造:
    <td class="cellcolor_ sk__index1">
        <span class="Sort_Function_Data_Hidden">1089</span>
        <a href="...">89</a>  ← この89を取得
    </td>
    または
    <td class="cellcolor_ sk__average_index">
        <span class="Sort_Function_Data_Hidden">1086</span>
        86*  ← このテキストを取得
    </td>
    
    戻り値:{ "1": {"index1":"89","index2":"77","index3":"94","course":"無","avg5":"86"}, ... }
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", class_=lambda c: c and ("SpeedIndex_Table" in c))
    if not table or not table.tbody:
        return {}

    out = {}

    for tr in table.tbody.find_all("tr", class_=lambda c: c and ("HorseList" in c.split()), recursive=False):
        um_td = tr.find("td", class_=lambda c: c and "sk__umaban" in c)
        if not um_td:
            continue
        umaban = re.sub(r"\D", "", um_td.get_text(" ", strip=True))
        if not umaban:
            continue

        def cell_text(cell_class: str) -> str:
            """
            指定されたクラス名のtdセルを探し、Sort_Function_Data_Hiddenの後のテキストを取得
            """
            td = tr.find("td", class_=lambda c: c and cell_class in c.split())
            if not td:
                return "無"
            
            # Sort_Function_Data_Hiddenスパンを見つける
            hidden_span = td.find("span", class_="Sort_Function_Data_Hidden")
            if hidden_span:
                # 隠しスパンの後にaタグがあるか確認
                a_tag = td.find("a")
                if a_tag:
                    # aタグのテキストを取得
                    txt = a_tag.get_text(strip=True)
                else:
                    # aタグがない場合、隠しスパンを除去してテキスト取得
                    hidden_span.decompose()
                    txt = td.get_text(strip=True)
            else:
                # 隠しスパンがない場合は普通にテキスト取得
                txt = td.get_text(strip=True)
            
            # 正規化して返す
            return normalize_netkeiba_index_cell(txt)

        out[umaban] = {
            "index1": cell_text("sk__index1"),             # 前走
            "index2": cell_text("sk__index2"),             # 2走前
            "index3": cell_text("sk__index3"),             # 3走前
            "course": cell_text("sk__max_course_index"),   # コース最高
            "avg5":   cell_text("sk__average_index"),      # 5走平均
        }

    return out


def fetch_netkeiba_speed_dict(driver: webdriver.Chrome, netkeiba_race_id: str) -> dict:
    """
    netkeiba speed.html を開いて指数辞書を返す
    """
    url = f"https://race.netkeiba.com/race/speed.html?race_id={netkeiba_race_id}&type=shutuba&mode=default"
    driver.get(url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.SpeedIndex_Table"))
        )
    except Exception:
        pass

    time.sleep(1.0)  # JavaScript レンダリング待ち

    html = driver.page_source

    # ログインが必要そうなら 1回だけログインして再取得
    if ("無料会員登録" in html or "ログイン" in html) and NETKEIBA_ID and NETKEIBA_PASS:
        ok = login_netkeiba(driver)
        if ok:
            driver.get(url)
            time.sleep(1.0)
            html = driver.page_source

    return parse_netkeiba_speed_index(html)


def keibabook_race_id_to_netkeiba_race_id(year: str, kai: str, place: str, day: str, race_num_2: str) -> str:
    """
    netkeiba race_id = YYYY + (netkeiba場コード2桁) + 回2桁 + 日2桁 + R2桁
    """
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place)
    if not nk_place:
        return ""
    return f"{str(year)}{nk_place}{str(kai).zfill(2)}{str(day).zfill(2)}{str(race_num_2).zfill(2)}"


# ==================================================
# ★修正:netkeiba 馬柱(5走) から「戦績」を正確に抽出
# 実際のHTML構造に基づいて実装
# ==================================================
def _extract_race_result_from_past_td(past_td) -> str:
    """
    td.Past の中身から戦績を指定フォーマットで抽出:
    例: 2025.11.02 京都 古都S(3勝) 芝3000(外) 13頭4番2人 岩田望来 55.0→1-1-1-1 ...最終9着
    
    実際のHTML構造:
    <td class="Past">
      <div class="Data_Item">
        <div class="Data01"><span>2025.11.02 京都</span><span class="Num">9</span></div>
        <div class="Data02"><a href="...">古都S<span class="Icon_GradeType...">3勝</span></a></div>
        <div class="Data05">芝3000(外) 3:05.5 <strong>良</strong></div>
        <div class="Data03">13頭 4番 2人 岩田望来 55.0</div>
        <div class="Data06">1-1-1-1 (36.4) 514(-12)</div>
        <div class="Data07"><a href="...">ヴォランテ</a>(1.7)</div>
      </div>
    </td>
    """
    if past_td is None:
        return ""
    
    # 空セルチェック
    cell_text = past_td.get_text(" ", strip=True)
    if not cell_text or len(cell_text) < 5:
        return ""
    
    # Data_Item を探す(存在しない場合は直接探す)
    data_item = past_td.find("div", class_="Data_Item")
    if data_item:
        container = data_item
    else:
        container = past_td
    
    # ===== Data01: 日付・競馬場・着順 =====
    data01 = container.find("div", class_="Data01")
    date_str = ""
    place_str = ""
    rank_num = ""
    
    if data01:
        spans = data01.find_all("span")
        if spans:
            # 最初のspan: "2025.11.02 京都" または "2025.11.02　京都"
            first_span_text = spans[0].get_text(" ", strip=True)
            # 日付と競馬場を分離
            date_match = re.search(r"(\d{4}[./]\d{1,2}[./]\d{1,2})", first_span_text)
            if date_match:
                raw_date = date_match.group(1)
                date_str = raw_date.replace("/", ".")
            
            # 競馬場名(日付以降の文字列)
            place_match = re.search(r"\d{4}[./]\d{1,2}[./]\d{1,2}\s*(.+)", first_span_text)
            if place_match:
                place_str = place_match.group(1).strip()
                # "京都10" のような場合、数字を除去
                place_str = re.sub(r"\d+$", "", place_str).strip()
        
        # 着順(class="Num" のspan)
        num_span = data01.find("span", class_="Num")
        if num_span:
            rank_num = num_span.get_text(strip=True)
            # "中" は競走中止
            if rank_num and rank_num not in ["中", "取", "除"]:
                rank_num = re.sub(r"\D", "", rank_num)
    
    # ===== Data02: レース名・クラス =====
    data02 = container.find("div", class_="Data02")
    race_name = ""
    class_str = ""
    
    if data02:
        # レース名(aタグのテキスト、ただしspanを除く)
        a_tag = data02.find("a")
        if a_tag:
            # spanを一時的に除去してテキスト取得
            for span in a_tag.find_all("span"):
                span_text = span.get_text(strip=True)
                if span_text:
                    class_str = span_text
            race_name = a_tag.get_text(strip=True)
            # クラス文字列を除去
            if class_str and class_str in race_name:
                race_name = race_name.replace(class_str, "").strip()
        else:
            race_name = data02.get_text(strip=True)
        
        # Icon_GradeType からクラスを取得
        grade_span = data02.find("span", class_=lambda c: c and "Icon_GradeType" in str(c))
        if grade_span:
            class_str = grade_span.get_text(strip=True)
    
    # ===== Data05: コース情報(芝/ダ、距離、タイム) =====
    data05 = container.find("div", class_="Data05")
    course_str = ""
    
    if data05:
        text = data05.get_text(" ", strip=True)
        # "芝3000(外) 3:05.5 良" のようなフォーマット
        # コース部分だけ抽出(芝/ダ + 距離 + (内/外))
        course_match = re.match(r"(芝|ダ)\d+(\([内外]\))?", text)
        if course_match:
            course_str = course_match.group(0)
    
    # ===== Data03: 頭数・馬番・人気・騎手・斤量 =====
    data03 = container.find("div", class_="Data03")
    field_info = ""
    jockey = ""
    weight = ""
    
    if data03:
        text = data03.get_text(" ", strip=True)
        # "13頭 4番 2人 岩田望来 55.0" のようなフォーマット
        
        # 頭数・馬番・人気
        head_match = re.search(r"(\d+)頭", text)
        num_match = re.search(r"(\d+)番", text)
        pop_match = re.search(r"(\d+)人", text)
        
        parts = []
        if head_match:
            parts.append(f"{head_match.group(1)}頭")
        if num_match:
            parts.append(f"{num_match.group(1)}番")
        if pop_match:
            parts.append(f"{pop_match.group(1)}人")
        field_info = "".join(parts)
        
        # 斤量(数字.数字 のパターン)
        weight_match = re.search(r"(\d+\.\d+|\d+\.0)", text)
        if weight_match:
            weight = weight_match.group(1)
        
        # 騎手名(人気の後、斤量の前)
        # "2人 岩田望来 55.0" → "岩田望来"
        jockey_match = re.search(r"\d+人\s+([^\d\s]+(?:\s+[^\d\s]+)?)\s+\d", text)
        if jockey_match:
            jockey = jockey_match.group(1).strip()
        else:
            # 別パターン: 人気がない場合
            jockey_match2 = re.search(r"番\s+([^\d]+)\s+\d+\.", text)
            if jockey_match2:
                jockey = jockey_match2.group(1).strip()
    
    # ===== Data06: 通過順位 =====
    data06 = container.find("div", class_="Data06")
    passing = ""
    
    if data06:
        text = data06.get_text(" ", strip=True)
        # "1-1-1-1 (36.4) 514(-12)" のようなフォーマット
        # 通過順位パターン(ハイフン区切りの数字列)
        passing_match = re.match(r"([\d]+-[\d]+(?:-[\d]+)*)", text)
        if passing_match:
            passing = passing_match.group(1)
    
    # ===== 結果文字列を組み立て =====
    # フォーマット: 2025.11.02 京都 古都S(3勝) 芝3000(外) 13頭4番2人 岩田望来 55.0→1-1-1-1 ...最終9着
    
    result_parts = []
    
    if date_str:
        result_parts.append(date_str)
    
    if place_str:
        result_parts.append(place_str)
    
    # レース名(クラス)
    if race_name and class_str:
        result_parts.append(f"{race_name}({class_str})")
    elif race_name:
        result_parts.append(race_name)
    
    if course_str:
        result_parts.append(course_str)
    
    if field_info:
        result_parts.append(field_info)
    
    # 騎手 斤量
    if jockey and weight:
        result_parts.append(f"{jockey} {weight}")
    elif jockey:
        result_parts.append(jockey)
    
    # 通過順→着順
    if passing:
        result_parts.append(f"→{passing}")
    
    if rank_num:
        result_parts.append(f"...最終{rank_num}着")
    
    if not result_parts:
        return ""
    
    return " ".join(result_parts)


def parse_netkeiba_shutuba_past5(html: str, take_last_n: int = 3) -> dict:
    """
    shutuba_past.html(馬柱5走表示)から戦績を抜く。
    
    実際のHTML構造:
    <table class="Shutuba_Table Shutuba_Past5_Table" id="sort_table">
      <tbody>
        <tr class="HorseList" id="tr_8">
          <td class="Waku1">1</td>      <!-- 枠番 -->
          <td class="Waku">1</td>       <!-- 馬番 -->
          <td class="Horse_Select">...</td>
          <td class="Horse_Info">...</td>
          <td class="Jockey">...</td>
          <td class="Past">...</td>     <!-- 前走 -->
          <td class="Past">...</td>     <!-- 2走前 -->
          <td class="Past">...</td>     <!-- 3走前 -->
          ...
        </tr>
      </tbody>
    </table>

    戻り値:
      {
        "1": {"past3": [str, str, str]},
        "2": {"past3": [str, str, str]},
        ...
      }
    
    past3[0] = 前走, past3[1] = 2走前, past3[2] = 3走前
    """
    soup = BeautifulSoup(html, "html.parser")

    # テーブル検索
    table = soup.find("table", id="sort_table")
    if not table:
        table = soup.find("table", class_=lambda c: c and "Shutuba_Past5_Table" in str(c))
    if not table:
        table = soup.find("table", class_=lambda c: c and "Shutuba_Table" in str(c))
    
    if not table:
        return {}
    
    tbody = table.find("tbody")
    if not tbody:
        tbody = table

    out = {}

    # 各馬の行を取得
    rows = tbody.find_all("tr", class_=lambda c: c and "HorseList" in str(c))
    
    for tr in rows:
        # ===== 馬番を取得 =====
        # 枠番と馬番の2つのtd.Wakuがある
        # 枠番: class="Waku1" など (Waku + 数字)
        # 馬番: class="Waku" のみ
        umaban = ""
        
        waku_tds = tr.find_all("td", class_=lambda c: c and "Waku" in str(c))
        for td in waku_tds:
            td_class = td.get("class") or []
            # class が ["Waku"] のみのものが馬番
            if td_class == ["Waku"]:
                umaban = re.sub(r"\D", "", td.get_text(strip=True))
                break
        
        # 見つからない場合は2番目のWaku系tdを使用
        if not umaban and len(waku_tds) >= 2:
            umaban = re.sub(r"\D", "", waku_tds[1].get_text(strip=True))
        
        if not umaban:
            continue

        # ===== 過去走(Past)を取得 =====
        past_tds = tr.find_all("td", class_=lambda c: c and "Past" in str(c))
        
        # Rest(休み明け情報)を除外
        past_tds = [td for td in past_tds if "Rest" not in str(td.get("class", []))]
        
        past_summaries = []
        for td in past_tds[:take_last_n]:
            summary = _extract_race_result_from_past_td(td)
            past_summaries.append(summary)
        
        # 不足分は空文字で埋める
        while len(past_summaries) < take_last_n:
            past_summaries.append("")
        
        out[umaban] = {
            "past3": past_summaries[:take_last_n],
        }

    return out


def fetch_netkeiba_past5_dict(driver: webdriver.Chrome, netkeiba_race_id: str) -> dict:
    """
    shutuba_past.html を開いて、戦績情報を辞書で返す
    """
    url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={netkeiba_race_id}&rf=shutuba_submenu"
    driver.get(url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table#sort_table, table.Shutuba_Past5_Table"))
        )
    except Exception:
        pass
    
    time.sleep(0.5)  # JavaScriptレンダリング待ち

    html = driver.page_source

    # ログインが必要そうなら 1回だけログインして再取得
    if ("無料会員登録" in html or "ログイン" in html) and NETKEIBA_ID and NETKEIBA_PASS:
        ok = login_netkeiba(driver)
        if ok:
            driver.get(url)
            time.sleep(0.8)
            html = driver.page_source

    return parse_netkeiba_shutuba_past5(html, take_last_n=3)


# ==================================================
# fetch(Selenium)競馬ブック
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
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.cyokyo"))
        )
    except Exception:
        pass
    return parse_cyokyo(driver.page_source)

def fetch_syutuba_dict(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syutuba/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.syutuba_sp, table.syutuba"))
        )
    except Exception:
        pass
    return parse_syutuba(driver.page_source)


# ==================================================
# 直近開催:複数候補検出
# ==================================================
def detect_meet_candidates(driver, max_candidates: int = 12):
    driver.get(f"{BASE_URL}/cyuou/")
    time.sleep(1.0)
    html = driver.page_source

    keys12 = re.findall(r"/cyuou/syutuba/(\d{12})", html)
    if not keys12:
        keys12 = re.findall(r"/cyuou/thursday/(\d{12})", html)

    if not keys12:
        driver.get(f"{BASE_URL}/")
        time.sleep(1.0)
        html2 = driver.page_source
        keys12 = re.findall(r"/cyuou/syutuba/(\d{12})", html2)
        if not keys12:
            keys12 = re.findall(r"/cyuou/thursday/(\d{12})", html2)

    if not keys12:
        return []

    meet10_set = set(k[:10] for k in keys12 if len(k) >= 10)
    meet10_sorted = sorted(meet10_set, reverse=True)

    candidates = []
    for m10 in meet10_sorted[:max_candidates]:
        year = m10[0:4]
        kai = m10[4:6]
        place = m10[6:8]
        day = m10[8:10]
        candidates.append({
            "meet10": m10,
            "year": year,
            "kai": kai,
            "place": place,
            "day": day,
            "place_name": PLACE_NAMES.get(place, "不明"),
        })

    return candidates

def auto_detect_meet_candidates():
    driver = build_driver()
    try:
        login_keibabook(driver)
        return detect_meet_candidates(driver)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ==================================================
# ★修正:Dify(Streaming) - タイムアウト対策強化
# ==================================================
def stream_dify_workflow(full_text: str):
    if not DIFY_API_KEY:
        yield "⚠️ エラー: DIFY_API_KEY が未設定"
        return

    payload = {
        "inputs": {"text": full_text},
        "response_mode": "streaming",
        "user": "keiba-bot-user",
    }

    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        res = requests.post(
            "https://api.dify.ai/v1/workflows/run",
            headers=headers,
            json=payload,
            stream=True,
            timeout=600,  # 10分に延長
        )

        if res.status_code != 200:
            yield f"⚠️ エラー: Dify API Error {res.status_code}\n{res.text}"
            return

        for line in res.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8", errors="ignore")
            if not decoded.startswith("data:"):
                continue

            json_str = decoded.replace("data: ", "")
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            event = data.get("event")
            if event in ["workflow_started", "node_started", "node_finished"]:
                continue

            chunk = data.get("answer", "")
            if chunk:
                yield chunk

            if event == "workflow_finished":
                outputs = data.get("data", {}).get("outputs", {})
                if outputs:
                    found_text = ""
                    for _, value in outputs.items():
                        if isinstance(value, str):
                            found_text += value + "\n"
                    if found_text.strip():
                        yield found_text.strip()

    except requests.exceptions.Timeout:
        yield "\n\n⚠️ タイムアウト: Difyの処理時間が長すぎます。データは保存されていますが、全ての出力を受信できませんでした。"
    except Exception as e:
        yield f"\n\n⚠️ Request Error: {str(e)}"


# ==================================================
# 結合用:馬名キー救済
# ==================================================
def _find_by_name_key(d: dict, bamei: str):
    if not bamei:
        return None
    if bamei in d:
        return d[bamei]
    for k, v in d.items():
        if (not str(k).isdigit()) and (str(k).strip() == bamei.strip()):
            return v
    return None


# ==================================================
# ★修正:メイン処理(複数レース) - Streamlitタイムアウト対策
# ==================================================
def run_all_races(target_races=None):
    race_numbers = (
        list(range(1, 13))
        if target_races is None
        else sorted({int(r) for r in target_races})
    )

    base_id = f"{YEAR}{KAI}{PLACE}{DAY}"
    place_name = PLACE_NAMES.get(PLACE, "不明")

    combined_blocks: list[str] = []

    driver = build_driver()

    try:
        st.info("🔑 ログイン中...(競馬ブック)")
        login_keibabook(driver)
        st.success("✅ 競馬ブック ログイン完了")

        # netkeibaは「必要なら」ログイン(失敗しても続行)
        if NETKEIBA_ID and NETKEIBA_PASS:
            st.info("🔑 netkeiba ログイン確認中(必要なら)...")
            netkeiba_logged_in = login_netkeiba(driver)
            if netkeiba_logged_in:
                st.success("✅ netkeiba ログイン完了")
            else:
                st.warning("⚠️ netkeiba ログインは未確認(閲覧可能なら取得できます)")

        for r in race_numbers:
            race_num = f"{r:02}"
            race_id = base_id + race_num

            netkeiba_race_id = keibabook_race_id_to_netkeiba_race_id(YEAR, KAI, PLACE, DAY, race_num)

            st.markdown(f"### {place_name} {r}R")
            
            # ★修正:st.empty()を使って更新可能なコンテナを作成
            status_area = st.empty()
            result_container = st.container()
            
            full_answer = ""

            try:
                status_area.info(f"📡 {place_name}{r}R のデータを収集中...")

                # A-1 danwa + race_info
                _html_danwa, race_info, danwa_dict = fetch_danwa_dict(driver, race_id)

                # A-2 syoin
                zenkoso_dict = fetch_zenkoso_dict(driver, race_id)

                # A-3 cyokyo
                cyokyo_dict = fetch_cyokyo_dict(driver, race_id)

                # A-3.5 syutuba(馬番・馬名・騎手)
                syutuba_dict = fetch_syutuba_dict(driver, race_id)

                if not syutuba_dict:
                    status_area.warning("⚠️ 出馬表が取得できませんでした(全頭保証できない可能性)。")

                # A-4 netkeiba 指数(取れなくても続行)
                speed_dict = {}
                if netkeiba_race_id:
                    try:
                        status_area.info(f"📊 netkeiba 指数を取得中... (race_id: {netkeiba_race_id})")
                        speed_dict = fetch_netkeiba_speed_dict(driver, netkeiba_race_id)
                        if speed_dict:
                            status_area.success(f"✅ netkeiba 指数取得完了 ({len(speed_dict)}頭分)")
                        else:
                            status_area.warning("⚠️ netkeiba 指数が取得できませんでした")
                    except Exception as e:
                        print("netkeiba speed fetch error:", e)
                        status_area.warning(f"⚠️ netkeiba 指数取得エラー: {str(e)}")
                        speed_dict = {}

                # ★A-4.5 netkeiba 馬柱(5走):戦績(取れなくても続行)
                past5_dict = {}
                if netkeiba_race_id:
                    try:
                        status_area.info(f"📝 netkeiba 戦績を取得中...")
                        past5_dict = fetch_netkeiba_past5_dict(driver, netkeiba_race_id)
                        if past5_dict:
                            status_area.success(f"✅ netkeiba 戦績取得完了 ({len(past5_dict)}頭分)")
                    except Exception as e:
                        print("netkeiba past5 fetch error:", e)
                        status_area.warning(f"⚠️ netkeiba 戦績取得エラー: {str(e)}")
                        past5_dict = {}

                # A-5 結合(出馬表ベース)
                merged = []
                umaban_list = (
                    sorted(syutuba_dict.keys(), key=lambda x: int(x))
                    if syutuba_dict
                    else sorted(
                        list(set(danwa_dict.keys()) | set(zenkoso_dict.keys()) | set(cyokyo_dict.keys())),
                        key=lambda x: int(x) if str(x).isdigit() else 999
                    )
                )

                for umaban in umaban_list:
                    sb = syutuba_dict.get(umaban, {})
                    bamei = (sb.get("bamei") or "").strip() or "名称不明"

                    kisyu_raw = (sb.get("kisyu") or "").strip()
                    kisyu_change = bool(sb.get("kisyu_change"))
                    if kisyu_raw:
                        kisyu = f"替・{kisyu_raw}" if kisyu_change else kisyu_raw
                    else:
                        kisyu = "(騎手不明)"

                    # 厩舎の話
                    d_comment = danwa_dict.get(umaban)
                    if not d_comment:
                        alt = _find_by_name_key(danwa_dict, bamei)
                        d_comment = alt if isinstance(alt, str) else None
                    if not d_comment:
                        d_comment = "(情報なし)"

                    # 前走(競馬ブック:前走談話)
                    z_data = zenkoso_dict.get(umaban)
                    if not z_data:
                        alt = _find_by_name_key(zenkoso_dict, bamei)
                        z_data = alt if isinstance(alt, dict) else None
                    z_data = z_data or {}

                    z_prev_info = ""
                    z_comment = ""
                    if z_data:
                        z_prev_info = f"{z_data.get('prev_date_course','')} {z_data.get('prev_class','')} {z_data.get('prev_finish','')}".strip()
                        z_comment = (z_data.get("prev_comment") or "").strip()

                    if z_prev_info or z_comment:
                        prev_block = (
                            f"  【前走情報】 {z_prev_info or '(情報なし)'}\n"
                            f"  【前走談話】 {z_comment or '(無し)'}\n"
                        )
                    else:
                        prev_block = "  【前走】 新馬(前走情報なし)\n"

                    # 調教(競馬ブック)
                    c = cyokyo_dict.get(umaban)
                    if not c:
                        c = _find_by_name_key(cyokyo_dict, bamei)
                    c = c or {}

                    c_tanpyo = (c.get("tanpyo") or "").strip()
                    c_detail = (c.get("detail") or "").strip()

                    if c_tanpyo or c_detail:
                        cyokyo_block = f"  【調教】 短評:{c_tanpyo or '(なし)'} / 詳細:{c_detail or '(なし)'}\n"
                    else:
                        cyokyo_block = "  【調教】 (情報なし)\n"

                    # ★修正:指数(netkeiba)※すべて normalize 済みの dict になってるが念のため再正規化
                    sp = speed_dict.get(umaban, {}) if isinstance(speed_dict, dict) else {}
                    idx1 = normalize_netkeiba_index_cell(sp.get("index1", "無"))
                    idx2 = normalize_netkeiba_index_cell(sp.get("index2", "無"))
                    idx3 = normalize_netkeiba_index_cell(sp.get("index3", "無"))
                    course = normalize_netkeiba_index_cell(sp.get("course", "無"))
                    avg5 = normalize_netkeiba_index_cell(sp.get("avg5", "無"))
                    speed_line = f"  【指数】 前走:{idx1}、2走前:{idx2}、3走前:{idx3}、コース最高:{course}、5走平均:{avg5}\n"

                    # ★戦績(netkeiba 馬柱5走)
                    past_info = past5_dict.get(umaban, {}) if isinstance(past5_dict, dict) else {}
                    past3 = past_info.get("past3") or ["", "", ""]
                    
                    # 不足分は空文字で埋める
                    while len(past3) < 3:
                        past3.append("")
                    past3 = past3[:3]
                    
                    # 戦績ブロック作成(過去走がある分だけ出力)
                    senreki_lines = []
                    labels = ["前走", "2走前", "3走前"]
                    for i, (label, record) in enumerate(zip(labels, past3)):
                        if record:  # 空でなければ出力
                            senreki_lines.append(f"{label}:{record}")
                    
                    if senreki_lines:
                        senreki_block = "  【戦績】" + " ".join(senreki_lines) + "\n"
                    else:
                        senreki_block = "  【戦績】 新馬(過去走なし)\n"

                    text = (
                        f"▼[馬番{umaban}] {bamei} / 騎手:{kisyu}\n"
                        f"  【厩舎の話】 {d_comment}\n"
                        f"{prev_block}"
                        f"{cyokyo_block}"
                        f"{speed_line}"
                        f"{senreki_block}"
                    )
                    merged.append(text)

                if not merged:
                    status_area.warning("⚠️ データが取得できませんでした。スキップします。")
                    st.write("---")
                    continue

                # レースヘッダー
                race_header_lines = []
                if race_info.get("date_meet"):
                    race_header_lines.append(race_info["date_meet"])
                if race_info.get("race_name"):
                    race_header_lines.append(race_info["race_name"])
                if race_info.get("cond1"):
                    race_header_lines.append(race_info["cond1"])
                if race_info.get("course_line"):
                    race_header_lines.append(race_info["course_line"])
                race_header = "\n".join(race_header_lines)

                merged_text = "\n".join(merged)

                full_text = (
                    "■レース情報\n"
                    f"{race_header}\n\n"
                    f"以下は{place_name}{r}Rの全頭データ。\n"
                    "■出走馬詳細データ\n"
                    + merged_text
                )

                status_area.info("🤖 AIが分析・執筆中です...")

                # ★修正:result_containerの中にmarkdownエリアを作成
                with result_container:
                    result_area = st.empty()
                    
                    for chunk in stream_dify_workflow(full_text):
                        if chunk:
                            full_answer += chunk
                            # カーソル表示で進行中を示す
                            result_area.markdown(full_answer + "▌")

                    # 最終出力(カーソル削除)
                    result_area.markdown(full_answer)

                if full_answer.strip():
                    status_area.success("✅ 分析完了")
                    save_history(YEAR, KAI, PLACE, place_name, DAY, race_num, race_id, full_answer)

                    with st.expander("📋 このレースの出力をコピー/保存", expanded=False):
                        dom_id = f"copy_race_{race_id}_{int(time.time()*1000)}"
                        render_copy_button(
                            text=full_answer.strip(),
                            label=f"📋 {place_name}{r}R をコピー(ワンクリック)",
                            dom_id=dom_id,
                        )
                        st.download_button(
                            label=f"⬇️ {place_name}{r}R をtxt保存",
                            data=full_answer.strip(),
                            file_name=f"{YEAR}{KAI}{PLACE}{DAY}_{place_name}_{r}R.txt",
                            mime="text/plain",
                            key=f"dl_race_{race_id}",
                        )

                    combined_blocks.append(f"【{place_name} {r}R】\n{full_answer.strip()}\n")

                else:
                    status_area.error("⚠️ AIからの回答が空でした。")

            except Exception as e:
                err_msg = f"❌ エラー発生 ({place_name} {r}R): {str(e)}"
                print(err_msg)
                import traceback
                traceback.print_exc()
                status_area.error(err_msg)

            st.write("---")

        if combined_blocks:
            combined_text = "\n".join(combined_blocks).strip()
            st.session_state["combined_output"] = combined_text

            st.subheader("📌 全レースまとめ(要求したレースを全部まとめてコピー)")

            dom_id_all = f"copy_all_{base_id}_{int(time.time()*1000)}"
            render_copy_button(
                text=combined_text,
                label="📋 全レースまとめをコピー(ワンクリック)",
                dom_id=dom_id_all,
            )

            st.download_button(
                label="⬇️ 全レースまとめをtxt保存",
                data=combined_text,
                file_name=f"{YEAR}{KAI}{PLACE}{DAY}_{place_name}_ALL.txt",
                mime="text/plain",
                key=f"dl_all_{base_id}",
            )

            with st.expander("👀 まとめ表示(閲覧用)", expanded=False):
                st.text_area(
                    "全レースまとめテキスト",
                    value=combined_text,
                    height=420,
                    key=f"ta_all_{base_id}",
                )
        else:
            st.info("まとめ対象の出力がありませんでした。")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
