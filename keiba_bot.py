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

# デフォルト設定
YEAR = "2026"
KAI = "01"
PLACE = "05"  # 中山
DAY = "03"

BASE_URL = "https://s.keibabook.co.jp"

PLACE_NAMES = {
    "00": "京都",
    "01": "阪神",
    "02": "中京",
    "03": "小倉",
    "04": "東京",
    "05": "中山",
    "06": "福島",
    "07": "新潟",
    "08": "札幌",
    "09": "函館",
}

# 競馬ブック PLACEコード → netkeiba 競馬場コード
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
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)


def get_current_params():
    return YEAR, KAI, PLACE, DAY


# ==================================================
# ワンクリックコピー
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
            msg.textContent = "コピー失敗";
            setTimeout(() => msg.textContent = "", 2200);
          }}
        }});
      }})();
    </script>
    """
    components.html(html, height=54)


# ==================================================
# Selenium / Login
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
        raise RuntimeError("KEIBA_ID / KEIBA_PASS が未設定です（st.secrets）")
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
    time.sleep(1.0)


# ==================================================
# Utility
# ==================================================
def _clean_text_ja(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u3000", " ")  # 全角スペース
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_missing_marker(s: str) -> bool:
    t = _clean_text_ja(s)
    return t in {"－", "-", "—", "―", "‐", ""}


# ==================================================
# 競馬ブック：厩舎の話（danwa）→ レース情報 + 馬番/馬名/厩舎コメント
#  HTML構造を積極利用して確実に抽出
# ==================================================
def parse_race_info_from_danwa(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    racetitle = soup.find("div", class_="racetitle")
    if not racetitle:
        return {"date_meet": "", "race_name": "", "cond1": "", "course_line": ""}

    racemei = racetitle.find("div", class_="racemei")
    date_meet, race_name = "", ""
    if racemei:
        ps = racemei.find_all("p")
        if len(ps) >= 1:
            date_meet = ps[0].get_text(strip=True)
        if len(ps) >= 2:
            race_name = ps[1].get_text(strip=True)

    racetitle_sub = racetitle.find("div", class_="racetitle_sub")
    cond1, course_line = "", ""
    if racetitle_sub:
        sub_ps = racetitle_sub.find_all("p")
        if len(sub_ps) >= 1:
            cond1 = sub_ps[0].get_text(strip=True)
        if len(sub_ps) >= 2:
            course_line = racetitle_sub.find_all("p")[1].get_text(" ", strip=True)

    return {
        "date_meet": _clean_text_ja(date_meet),
        "race_name": _clean_text_ja(race_name),
        "cond1": _clean_text_ja(cond1),
        "course_line": _clean_text_ja(course_line),
    }


def parse_danwa_horses(html: str) -> dict:
    """
    返り値:
      {
        "1": {"name":"ラフレードピエル", "danwa":"○ラフレードピエル..."},
        ...
      }
    """
    soup = BeautifulSoup(html, "html.parser")

    # まずテーブルを特定（class="default danwa" 想定）
    table = soup.find("table", class_=lambda c: c and "danwa" in str(c))
    if not table or not table.tbody:
        return {}

    horses = {}
    current_umaban = None

    # danwaは「馬行」→「コメント行」→ spacer の繰り返しが多い
    rows = table.tbody.find_all("tr", recursive=False)
    if not rows:
        rows = table.tbody.find_all("tr")

    for tr in rows:
        if "spacer" in (tr.get("class") or []):
            continue

        # 馬行：td.umaban + td.left(馬名リンク)
        umaban_td = tr.find("td", class_="umaban")
        bamei_td = tr.find("td", class_=lambda c: c and "left" in str(c))
        if umaban_td and bamei_td:
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            a = bamei_td.find("a")
            name = a.get_text(strip=True) if a else bamei_td.get_text(strip=True)
            name = _clean_text_ja(name)
            if umaban:
                current_umaban = umaban
                horses[current_umaban] = {"name": name, "danwa": ""}
            continue

        # コメント行：td.danwa
        danwa_td = tr.find("td", class_="danwa")
        if danwa_td and current_umaban:
            p = danwa_td.find("p")
            txt = p.get_text("\n", strip=True) if p else danwa_td.get_text("\n", strip=True)
            txt = _clean_text_ja(txt)
            horses[current_umaban]["danwa"] = txt if txt else "(情報なし)"
            current_umaban = None
            continue

    return horses


def fetch_keibabook_danwa(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/danwa/0/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.danwa"))
        )
    except Exception:
        pass

    html = driver.page_source
    race_info = parse_race_info_from_danwa(html)
    horses = parse_danwa_horses(html)
    return race_info, horses


# ==================================================
# 競馬ブック：前走インタビュー（syoin）
# 提示されたHTML構造を前提に、以下を厳密に定義
# - td.syoin 内にある「div.syoindata」はメタ情報（前走レース）→コメント抽出対象外
# - コメントは div.syoindata の外にある <p> のみ
# - <p>－</p> は「インタビューなし」
# ==================================================
def parse_zenkoso_interview(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "syoin" in str(c))
    if not table or not table.tbody:
        return {}

    interview = {}
    current_umaban = None

    rows = table.tbody.find_all("tr", recursive=False)
    if not rows:
        rows = table.tbody.find_all("tr")

    for tr in rows:
        if "spacer" in (tr.get("class") or []):
            continue

        # 馬行：td.umaban + td.left.bamei
        umaban_td = tr.find("td", class_="umaban")
        bamei_td = tr.find("td", class_=lambda c: c and "bamei" in str(c))
        if umaban_td and bamei_td:
            u = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            current_umaban = u if u else None
            continue

        # コメント行：td.syoin
        syoin_td = tr.find("td", class_="syoin")
        if syoin_td and current_umaban:
            # コメント候補は「div.syoindata の外」にある <p> のみ
            candidates = []
            for p in syoin_td.find_all("p"):
                # syoindata内のp（前走日付等）を除外
                if p.find_parent("div", class_="syoindata") is not None:
                    continue
                t = _clean_text_ja(p.get_text(" ", strip=True))
                candidates.append(t)

            # 有効なコメントのみ採用（－は無視）
            chosen = ""
            for t in candidates:
                if _is_missing_marker(t):
                    continue
                # コメントらしさ最低ライン（短いゴミ除去）
                if len(t) < 8:
                    continue
                chosen = t
                break

            if chosen:
                interview[current_umaban] = chosen

            current_umaban = None

    return interview


def fetch_zenkoso_interview(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/syoin/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.default.syoin"))
        )
    except Exception:
        pass
    return parse_zenkoso_interview(driver.page_source)


# ==================================================
# 競馬ブック：CPU予想（指数/ファクター）
# ==================================================
def parse_keibabook_cpu(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    # スピード指数
    speed_table = soup.find("table", id="cpu_speed_sort_table")
    if speed_table and speed_table.tbody:
        for tr in speed_table.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td:
                continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban:
                continue

            tds = tr.find_all("td")
            if len(tds) < 8:
                continue

            def get_val(idx):
                p = tds[idx].find("p")
                if not p:
                    return 0
                txt = re.sub(r"\D", "", p.get_text(strip=True))
                if not txt:
                    return 0
                v = int(txt)
                # 「未」や「-」が 1000 表記になるケース → 無扱い
                if v == 1000:
                    return 0
                return v

            val_last = get_val(-1)
            val_2ago = get_val(-2)
            val_3ago = get_val(-3)
            valid_scores = [v for v in [val_last, val_2ago, val_3ago] if v > 0]
            avg = round(sum(valid_scores) / len(valid_scores)) if valid_scores else 0

            if umaban not in data:
                data[umaban] = {}
            data[umaban].update(
                {
                    "speed_last": val_last if val_last > 0 else "無",
                    "speed_2ago": val_2ago if val_2ago > 0 else "無",
                    "speed_3ago": val_3ago if val_3ago > 0 else "無",
                    "speed_avg": avg if avg > 0 else "無",
                }
            )

    # ファクター（captionに「ファクター」）
    factor_table = None
    for tbl in soup.find_all("table"):
        cap = tbl.find("caption")
        if cap and "ファクター" in cap.get_text():
            factor_table = tbl
            break

    if factor_table and factor_table.tbody:
        for tr in factor_table.tbody.find_all("tr"):
            umaban_td = tr.find("td", class_="umaban")
            if not umaban_td:
                continue
            umaban = re.sub(r"\D", "", umaban_td.get_text(strip=True))
            if not umaban:
                continue

            tds = tr.find_all("td")
            if len(tds) < 8:
                continue

            def get_mark(idx):
                p = tds[idx].find("p")
                if not p:
                    return "無"
                txt = p.get_text(strip=True)
                return txt if txt else "無"

            if umaban not in data:
                data[umaban] = {}
            data[umaban].update(
                {
                    "factor_course": get_mark(5),
                    "factor_dist": get_mark(6),
                    "factor_zenso": get_mark(7),
                }
            )

    return data


def fetch_keibabook_cpu_data(driver, race_id: str):
    url = f"{BASE_URL}/cyuou/cpu/{race_id}"
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "cpu_speed_sort_table"))
        )
    except Exception:
        pass
    return parse_keibabook_cpu(driver.page_source)


# ==================================================
# netkeiba（出馬表：現在騎手 + 過去走）
# ==================================================
def keibabook_race_id_to_netkeiba_race_id(year, kai, place, day, race_num_2):
    nk_place = KEIBABOOK_TO_NETKEIBA_PLACE.get(place)
    if not nk_place:
        return ""
    return (
        f"{str(year)}{nk_place}{str(kai).zfill(2)}{str(day).zfill(2)}"
        f"{str(race_num_2).zfill(2)}"
    )


def _clean_jockey_name(name: str) -> str:
    return re.sub(r"^[▲△☆◇★◎○▲△\s]+", "", name).strip()


def _extract_jockey_from_data03(data03_tag) -> str:
    if data03_tag is None:
        return ""
    # 1) リンク優先
    for a in data03_tag.find_all("a"):
        txt = a.get_text(strip=True)
        href = (a.get("href") or "")
        if txt and ("/jockey/" in href or "jockey" in href):
            return _clean_jockey_name(txt)

    # 2) テキストフォールバック
    d3_text = data03_tag.get_text(" ", strip=True)
    if not d3_text:
        return ""
    weights = list(re.finditer(r"\b\d{2,3}\.\d\b", d3_text))
    if weights:
        w = weights[-1]
        before = d3_text[: w.start()].strip()
        parts = before.split()
        if parts:
            cand = _clean_jockey_name(parts[-1])
            return cand
    return ""


def _extract_race_result_from_past_td(past_td) -> str:
    if past_td is None:
        return ""
    text = past_td.get_text(" ", strip=True)
    if len(text) < 5:
        return ""

    date_match = re.search(r"(\d{4}[./]\d{1,2}[./]\d{1,2})", text)
    date_str = date_match.group(1).replace("/", ".") if date_match else ""

    rank_match = re.search(r"(\d{1,2})着", text)
    if not rank_match:
        num_span = past_td.find("span", class_="Num")
        rank = num_span.get_text(strip=True) if num_span else ""
    else:
        rank = rank_match.group(1)

    data02 = past_td.find("div", class_="Data02")
    race_name = data02.get_text(strip=True) if data02 else ""

    data05 = past_td.find("div", class_="Data05")
    course = ""
    if data05:
        c_text = data05.get_text(strip=True)
        cm = re.search(r"(芝|ダ)\d+", c_text)
        if cm:
            course = cm.group(0)

    data03 = past_td.find("div", class_="Data03")
    jockey = _extract_jockey_from_data03(data03)

    data06 = past_td.find("div", class_="Data06")
    passing = ""
    if data06:
        pm = re.search(r"(\d{1,2}-\d{1,2}(?:-\d{1,2})*)", data06.get_text(strip=True))
        if pm:
            passing = pm.group(1)

    parts = []
    if date_str:
        parts.append(date_str)
    if race_name:
        parts.append(race_name)
    if course:
        parts.append(course)
    if jockey:
        parts.append(f"[{jockey}]")
    if passing:
        parts.append(f"({passing})")
    if rank:
        parts.append(f"{rank}着")

    return " ".join(parts) if parts else ""


def parse_netkeiba_shutuba_past(html: str, take_last_n: int = 3) -> dict:
    """
    返り値:
      {
        "1": {"jockey": "丹内", "past3": ["前走..", "2走前..", "3走前.."]},
        ...
      }
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_=lambda c: c and "Shutuba_Past5_Table" in str(c))
    if not table:
        return {}

    out = {}
    rows = table.find_all("tr", class_=lambda c: c and "HorseList" in str(c))
    for tr in rows:
        # 馬番
        waku_tds = tr.find_all("td", class_=lambda c: c and "Waku" in str(c))
        umaban = ""
        for td in waku_tds:
            if td.get("class") == ["Waku"]:
                umaban = re.sub(r"\D", "", td.get_text(strip=True))
                break
        if not umaban and len(waku_tds) >= 2:
            umaban = re.sub(r"\D", "", waku_tds[1].get_text(strip=True))
        if not umaban:
            continue

        # 現在の騎手（td.Jockey a）
        jockey = ""
        jockey_td = tr.find("td", class_=lambda c: c and "Jockey" in str(c))
        if jockey_td:
            a = jockey_td.find("a")
            if a:
                jockey = a.get_text(strip=True)
        jockey = _clean_jockey_name(jockey)

        # 過去走
        past_tds = tr.find_all("td", class_=lambda c: c and "Past" in str(c))
        past_tds = [td for td in past_tds if "Rest" not in str(td.get("class", []))]

        summaries = []
        for td in past_tds[:take_last_n]:
            summaries.append(_extract_race_result_from_past_td(td))
        while len(summaries) < take_last_n:
            summaries.append("")

        out[umaban] = {"jockey": jockey, "past3": summaries}

    return out


def fetch_netkeiba_shutuba_past(driver, netkeiba_race_id: str) -> dict:
    url = (
        "https://race.netkeiba.com/race/shutuba_past.html"
        f"?race_id={netkeiba_race_id}&rf=shutuba_submenu"
    )
    driver.get(url)
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.Shutuba_Past5_Table"))
        )
    except Exception:
        pass
    return parse_netkeiba_shutuba_past(driver.page_source, take_last_n=3)


# ==================================================
# Dify Streaming
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
            timeout=600,
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
            except Exception:
                continue

            # workflow_finished の outputs がある場合
            event = data.get("event")
            if event == "workflow_finished":
                outputs = data.get("data", {}).get("outputs", {})
                for _, value in outputs.items():
                    if isinstance(value, str) and value:
                        yield value + "\n"

            # streaming answer
            chunk = data.get("answer", "")
            if chunk:
                yield chunk

    except Exception as e:
        yield f"\n\n⚠️ Request Error: {str(e)}"


# ==================================================
# メイン処理 (run_all_races)
# 訪れるページ:
# - keibabook: 厩舎の話(danwa) / 前走インタビュー(syoin) / CPU予想(cpu)
# - netkeiba: 馬柱(shutuba_past)
# ==================================================
def run_all_races(target_races=None):
    race_numbers = (
        list(range(1, 13))
        if target_races is None
        else sorted({int(r) for r in target_races})
    )
    base_id = f"{YEAR}{KAI}{PLACE}{DAY}"
    place_name = PLACE_NAMES.get(PLACE, "不明")

    combined_blocks = []
    driver = build_driver()

    try:
        st.info("🔑 ログイン中...(競馬ブック)")
        login_keibabook(driver)
        st.success("✅ 競馬ブック ログイン完了")

        for r in race_numbers:
            race_num = f"{r:02}"
            race_id = base_id + race_num  # Keibabook Race ID
            netkeiba_race_id = keibabook_race_id_to_netkeiba_race_id(
                YEAR, KAI, PLACE, DAY, race_num
            )

            st.markdown(f"### {place_name} {r}R")
            status_area = st.empty()
            result_container = st.container()
            full_answer = ""

            try:
                status_area.info(f"📡 {place_name}{r}R のデータを収集中...")

                # 1) 厩舎の話：レース情報 + 馬名 + 厩舎コメント（ここを主キーにする）
                status_area.info("🗣️ 競馬ブック 厩舎の話（レース情報/馬名/コメント）取得中...")
                race_info, danwa_horses = fetch_keibabook_danwa(driver, race_id)

                if not danwa_horses:
                    status_area.error("厩舎の話から馬データを取得できませんでした（table.danwa未検出）")
                    st.write("---")
                    continue

                # 2) CPU（指数/ファクター）
                cpu_dict = {}
                try:
                    status_area.info("📊 競馬ブック 指数・ファクター取得中...")
                    cpu_dict = fetch_keibabook_cpu_data(driver, race_id)
                except Exception as e:
                    print(f"CPU fetch error: {e}")

                # 3) 前走インタビュー（コメントがあるものだけ）
                zenkoso_dict = {}
                try:
                    status_area.info("🎤 前走インタビュー取得中...")
                    zenkoso_dict = fetch_zenkoso_interview(driver, race_id)
                except Exception as e:
                    print(f"Zenkoso fetch error: {e}")

                # 4) netkeiba：現在騎手 + 過去走
                nk_dict = {}
                if netkeiba_race_id:
                    try:
                        status_area.info("📝 netkeiba（現在騎手・戦績）取得中...")
                        nk_dict = fetch_netkeiba_shutuba_past(driver, netkeiba_race_id)
                    except Exception as e:
                        print(f"Netkeiba fetch error: {e}")

                # ---------
                # 結合（馬番順：厩舎の話に出ている馬番を正）
                # ---------
                umaban_list = sorted(danwa_horses.keys(), key=lambda x: int(x))

                merged = []
                for umaban in umaban_list:
                    bamei = (danwa_horses.get(umaban, {}) or {}).get("name") or "名称不明"
                    kyusha_comment = (danwa_horses.get(umaban, {}) or {}).get("danwa") or "(情報なし)"

                    # 騎手：netkeiba 優先
                    kisyu = (nk_dict.get(umaban, {}) or {}).get("jockey", "") if nk_dict else ""
                    kisyu = kisyu if kisyu else "不明"

                    # 指数・ファクター（CPU）
                    cpu = cpu_dict.get(umaban, {}) if cpu_dict else {}
                    sp_last = cpu.get("speed_last", "無")
                    sp_2ago = cpu.get("speed_2ago", "無")
                    sp_3ago = cpu.get("speed_3ago", "無")
                    sp_avg = cpu.get("speed_avg", "無")
                    fac_crs = cpu.get("factor_course", "無")
                    fac_dis = cpu.get("factor_dist", "無")
                    fac_zen = cpu.get("factor_zenso", "無")

                    index_line = (
                        f"  【指数】 前走:{sp_last}、2走前:{sp_2ago}、3走前:{sp_3ago}"
                        f"（3走平均:{sp_avg}）【ファクター】 コース：{fac_crs} 距離：{fac_dis} 前走：{fac_zen}\n"
                    )

                    # 前走インタビュー（<p>－</p> は入ってこない設計）
                    zenkoso_line = ""
                    if zenkoso_dict and umaban in zenkoso_dict:
                        zenkoso_line = f"  【前走インタビュー】 {zenkoso_dict[umaban]}\n"

                    # 戦績（netkeiba）
                    if nk_dict and umaban in nk_dict:
                        p_info = nk_dict[umaban].get("past3", ["", "", ""])
                    else:
                        p_info = ["", "", ""]
                    labels = ["前走", "2走前", "3走前"]
                    recs = []
                    for lab, rec in zip(labels, p_info):
                        if rec:
                            recs.append(f"{lab}:{rec}")
                    senreki_line = "  【戦績】 " + (" ".join(recs) if recs else "(情報なし)") + "\n"

                    text = (
                        f"▼[馬番{umaban}] {bamei} / 騎手:{kisyu}\n"
                        f"  【厩舎の話】 {kyusha_comment}\n"
                        f"{zenkoso_line}"
                        f"{index_line}"
                        f"{senreki_line}"
                    )
                    merged.append(text)

                # AI送信テキスト作成（レース情報は厩舎の話ページから）
                header_txt = (
                    f"{race_info.get('date_meet','')}\n"
                    f"{race_info.get('race_name','')}\n"
                    f"{race_info.get('cond1','')}\n"
                    f"{race_info.get('course_line','')}"
                )
                full_text = (
                    f"■レース情報\n{header_txt}\n\n"
                    f"以下は{place_name}{r}Rの全頭データ。\n"
                    f"■出走馬詳細データ\n" + "\n".join(merged)
                )

                status_area.info("🤖 AI分析中...")
                with result_container:
                    r_area = st.empty()
                    for chunk in stream_dify_workflow(full_text):
                        full_answer += chunk
                        r_area.markdown(full_answer + "▌")
                    r_area.markdown(full_answer)

                if full_answer:
                    status_area.success("✅ 完了")
                    combined_blocks.append(f"【{place_name} {r}R】\n{full_answer.strip()}\n")
                    render_copy_button(full_answer.strip(), f"📋 {place_name}{r}R コピー", f"copy_{race_id}")
                else:
                    status_area.error("回答なし")

            except Exception as e:
                status_area.error(f"エラー: {e}")
                import traceback
                traceback.print_exc()

            st.write("---")

        if combined_blocks:
            combined_text = "\n".join(combined_blocks).strip()
            st.subheader("📌 全レースまとめ")
            render_copy_button(combined_text, "📋 全てコピー", "copy_all_races")
            st.text_area("まとめ", combined_text, height=300)

    finally:
        try:
            driver.quit()
        except Exception:
            pass
