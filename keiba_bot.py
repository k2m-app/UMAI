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

# デフォルト設定（app.py 側で set_race_params が呼ばれると書き換わる）
YEAR = "2025"
KAI = "04"
PLACE = "02"
DAY = "02"

BASE_URL = "https://s.keibabook.co.jp"

PLACE_NAMES = {
    "00": "京都", "01": "阪神", "02": "中京", "03": "小倉", "04": "東京",
    "05": "中山", "06": "福島", "07": "新潟", "08": "札幌", "09": "函館",
}


def set_race_params(year, kai, place, day):
    """app.py から開催情報を差し替えるための関数"""
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)


def get_current_params():
    """現在のパラメータ（UI表示用）"""
    return YEAR, KAI, PLACE, DAY


# ==================================================
# ワンクリックコピー（components.html + clipboard）
# ※ Streamlit環境によって components.html が key 引数を受け付けないため
#   components.html(..., key=...) は使わない
# ==================================================
def render_copy_button(text: str, label: str, dom_id: str):
    """
    dom_id をHTML側の id として使い、ページ内でユニークにする。
    """
    safe_text = json.dumps(text)  # JS文字列として安全に埋め込む
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
            msg.textContent = "コピーに失敗（ブラウザ制限の可能性）";
            setTimeout(() => msg.textContent = "", 2200);
          }}
        }});
      }})();
    </script>
    """
    # ★key引数は渡さない（あなたの環境ではエラーになる）
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
    """history テーブルに 1 レース分の予想を保存する。"""
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


# ==================================================
# Parser：共通
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
    """
    厩舎の話を取得。
    戻り値：{ "1": "コメント", ... }（馬番優先。無理なら馬名キー混在）
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="danwa")
    if not table or not table.tbody:
        return {}

    danwa_dict = {}
    current_key = None

    for row in table.tbody.find_all("tr"):
        uma_td = row.find("td", class_="umaban")
        bamei_td = row.find("td", class_="bamei")

        # 馬番
        if uma_td:
            text = re.sub(r"\D", "", uma_td.get_text(strip=True))
            if text:
                current_key = text
                continue

        # 馬名（新馬等の揺れ対策）
        if bamei_td and not current_key:
            text = bamei_td.get_text(strip=True)
            if text:
                current_key = text
                continue

        # コメント本体
        danwa_td = row.find("td", class_="danwa")
        if danwa_td and current_key:
            danwa_dict[current_key] = danwa_td.get_text(strip=True)
            current_key = None

    return danwa_dict


def parse_zenkoso_interview(html: str):
    """
    前走インタビュー（syoin）を取得。無い場合は {}。
    戻り値：{ "1": {...}, ... }（馬番キー）
    """
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
    """
    調教データを取得。
    戻り値：
      - 馬番キー: { "1": {"tanpyo":"", "detail":"", "bamei_hint":""}, ... }
      - 馬番が取れない場合：馬名キーで入ることがある（救済用）
    """
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
    """
    確定出馬(出馬表)ページから
    { "1": {"umaban":"1","bamei":"ケイベエ","kisyu":"木幡巧","kisyu_change":True}, ... }
    を返す。馬番を主キーにする。
    """
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
# fetch（Selenium）
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
# 直近開催：複数候補検出
# ==================================================
def detect_meet_candidates(driver, max_candidates: int = 12):
    """
    Keibabook内ページから syutuba racekey を拾い、
    開催単位（YYYYKAIPLACEDAY = 10桁）でユニーク化して候補リストを返す。
    """
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
# Dify（Streaming）
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
            timeout=300,
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

    except Exception as e:
        yield f"⚠️ Request Error: {str(e)}"


# ==================================================
# 結合用：馬名キー救済
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
# メイン処理（複数レース）
# ==================================================
def run_all_races(target_races=None):
    """
    target_races: None -> 1~12
                 list/set -> 指定レース番号だけ実行

    仕様：
      - レース単位で出力表示
      - 各レース「ワンクリックコピー」＋txt保存
      - 最後に「全レースまとめ」をワンクリックコピー＋txt保存＋閲覧用text_area
    """
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
        st.info("🔑 ログイン中...")
        login_keibabook(driver)
        st.success("✅ ログイン完了")

        for r in race_numbers:
            race_num = f"{r:02}"
            race_id = base_id + race_num

            st.markdown(f"### {place_name} {r}R")
            status_area = st.empty()
            result_area = st.empty()
            full_answer = ""

            try:
                status_area.info(f"📡 {place_name}{r}R のデータを収集中...")

                # A-1 danwa + race_info
                _html_danwa, race_info, danwa_dict = fetch_danwa_dict(driver, race_id)

                # A-2 syoin
                zenkoso_dict = fetch_zenkoso_dict(driver, race_id)

                # A-3 cyokyo
                cyokyo_dict = fetch_cyokyo_dict(driver, race_id)

                # A-3.5 syutuba（馬番・馬名・騎手）
                syutuba_dict = fetch_syutuba_dict(driver, race_id)

                if not syutuba_dict:
                    status_area.warning("⚠️ 出馬表が取得できませんでした（全頭保証できない可能性）。")

                # A-4 結合（出馬表ベース）
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
                        kisyu = "（騎手不明）"

                    # 厩舎の話
                    d_comment = danwa_dict.get(umaban)
                    if not d_comment:
                        alt = _find_by_name_key(danwa_dict, bamei)
                        d_comment = alt if isinstance(alt, str) else None
                    if not d_comment:
                        d_comment = "（情報なし）"

                    # 前走
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
                            f"  【前走情報】 {z_prev_info or '（情報なし）'}\n"
                            f"  【前走談話】 {z_comment or '（無し）'}\n"
                        )
                    else:
                        prev_block = "  【前走】 新馬（前走情報なし）\n"

                    # 調教
                    c = cyokyo_dict.get(umaban)
                    if not c:
                        c = _find_by_name_key(cyokyo_dict, bamei)
                    c = c or {}

                    c_tanpyo = (c.get("tanpyo") or "").strip()
                    c_detail = (c.get("detail") or "").strip()

                    if c_tanpyo or c_detail:
                        cyokyo_block = f"  【調教】 短評:{c_tanpyo or '（なし）'} / 詳細:{c_detail or '（なし）'}\n"
                    else:
                        cyokyo_block = "  【調教】 （情報なし）\n"

                    text = (
                        f"▼[馬番{umaban}] {bamei} / 騎手:{kisyu}\n"
                        f"  【厩舎の話】 {d_comment}\n"
                        f"{prev_block}"
                        f"{cyokyo_block}"
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

                for chunk in stream_dify_workflow(full_text):
                    if chunk:
                        full_answer += chunk
                        result_area.markdown(full_answer + "▌")

                result_area.markdown(full_answer)

                if full_answer.strip():
                    status_area.success("✅ 分析完了")
                    save_history(YEAR, KAI, PLACE, place_name, DAY, race_num, race_id, full_answer)

                    # レース単位：コピー＆保存
                    with st.expander("📋 このレースの出力をコピー/保存", expanded=False):
                        # dom_idをユニーク化（再描画対策で時刻も混ぜる）
                        dom_id = f"copy_race_{race_id}_{int(time.time()*1000)}"
                        render_copy_button(
                            text=full_answer.strip(),
                            label=f"📋 {place_name}{r}R をコピー（ワンクリック）",
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
                status_area.error(err_msg)

            st.write("---")

        # 全レースまとめ：コピー＆保存
        if combined_blocks:
            combined_text = "\n".join(combined_blocks).strip()
            st.session_state["combined_output"] = combined_text

            st.subheader("📌 全レースまとめ（要求したレースを全部まとめてコピー）")

            dom_id_all = f"copy_all_{base_id}_{int(time.time()*1000)}"
            render_copy_button(
                text=combined_text,
                label="📋 全レースまとめをコピー（ワンクリック）",
                dom_id=dom_id_all,
            )

            st.download_button(
                label="⬇️ 全レースまとめをtxt保存",
                data=combined_text,
                file_name=f"{YEAR}{KAI}{PLACE}{DAY}_{place_name}_ALL.txt",
                mime="text/plain",
                key=f"dl_all_{base_id}",
            )

            with st.expander("👀 まとめ表示（閲覧用）", expanded=False):
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
