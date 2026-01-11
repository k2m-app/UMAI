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

# デフォルト設定 (グローバル変数)
YEAR = "2026"
KAI = "01"
PLACE = "05"  # 中山
DAY = "03"

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
# パラメータ設定・取得関数
# ==================================================
def set_race_params(year, kai, place, day):
    """UIから開催パラメータを設定するための関数"""
    global YEAR, KAI, PLACE, DAY
    YEAR = str(year)
    KAI = str(kai).zfill(2)
    PLACE = str(place).zfill(2)
    DAY = str(day).zfill(2)

def get_current_params():
    """現在のパラメータを返す関数"""
    return YEAR, KAI, PLACE, DAY

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
# スピード指数（競馬予想として最も扱いやすい形）
# ==================================================
def compute_speed_metrics(cpu_data: dict, T: float = 55.0, k: float = 4.0) -> dict:
    """
    【狙い】
    - クラス差（未勝利/重賞）でスピード指数の影響が歪む問題を解消するため、同レース内の相対評価にする
    - ただし「上位が複数いると偏差値が固まる」問題を避けるため、上位側だけメリハリを付ける

    【手順】
    1) スピード基礎値 raw =（最高値×3 ＋ 前走 ＋ 平均）÷5
    2) raw を同レース内で偏差値 dev（平均50, SD10）にする
    3) dev を “上位強調スコア” score（0〜100）に変換
       score = 100 / (1 + exp(-(dev - T)/k))
       - T: 上位の入口（55推奨）
       - k: メリハリ（4推奨、小さいほど強調）

    return:
      {
        umaban: {
          "raw": float,
          "dev": float,    # 偏差値（0〜100クリップ）
          "score": float   # 0〜100（上位強調）
        }
      }
    """
    raw_scores = {}

    for umaban, d in cpu_data.items():
        last = _safe_int(d.get("sp_last"), 0)
        two  = _safe_int(d.get("sp_2"), 0)
        thr  = _safe_int(d.get("sp_3"), 0)

        vals = [v for v in [last, two, thr] if v > 0]
        if not vals:
            continue

        avg = sum(vals) / len(vals)
        max_v = max(vals)

        raw = (max_v * 3 + last + avg) / 5.0
        raw_scores[umaban] = raw

    if not raw_scores:
        return {}

    values = list(raw_scores.values())
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    out = {}
    for umaban, raw in raw_scores.items():
        if std == 0:
            dev = 50.0
        else:
            dev = 50.0 + 10.0 * (raw - mean) / std

        # 偏差値は表示・監査用として0〜100にクリップ
        dev_clip = max(0.0, min(100.0, round(dev, 1)))

        # 上位強調スコア（0〜100）
        x = (dev - float(T)) / float(k)
        score = 100.0 / (1.0 + math.exp(-x))
        score = max(0.0, min(100.0, round(score, 1)))

        out[umaban] = {"raw": round(raw, 2), "dev": dev_clip, "score": score}

    return out

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
# 競馬ブック：厩舎の話 (Danwa)
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

    rows = table.tbody.find_all("tr", recursive=False)
    for tr in rows:
        classes = tr.get("class", [])
        if "spacer" in classes:
            continue

        umaban_td = tr.find("td", class_="umaban")
        bamei_td = tr.find("td", class_="left")

        if umaban_td and bamei_td:
            raw_umaban = umaban_td.get_text(strip=True)
            current_umaban = re.sub(r"\D", "", raw_umaban)

            anchor = bamei_td.find("a")
            if anchor:
                raw_name = anchor.get_text(strip=True)
            else:
                raw_name = bamei_td.get_text(strip=True)

            clean_name = _clean_text_ja(raw_name)
            if current_umaban:
                horses[current_umaban] = {"name": clean_name, "danwa": ""}
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
# 競馬ブック：調教 (Chokyo)
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
# 競馬ブック：前走インタビュー (Syoin)
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
# 競馬ブック：CPU予想 (新馬対応版)
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
                # 1000等の異常値は欠損扱い
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
    """netkeibaの過去走セル（td.Past）を解析して文字列化"""
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
# Main Execution
# ==================================================
def run_all_races(target_races=None):
    race_nums = target_races if target_races else list(range(1, 13))
    race_nums = [int(r) for r in race_nums]

    base_id = f"{YEAR}{KAI}{PLACE}{DAY}"

    driver = build_driver()
    try:
        st.info(f"ログイン中... (ID: {KEIBA_ID[:2]}**)")
        login_keibabook(driver)
        st.success("ログイン成功")

        combined_text = ""

        for r in race_nums:
            race_num_str = f"{r:02}"
            race_id = base_id + race_num_str

            st.markdown(f"### {PLACE_NAMES.get(PLACE, '場')} {r}R")
            status = st.empty()
            status.text("データ収集中...")

            # 1. 厩舎の話
            header_info, danwa_data = fetch_keibabook_danwa(driver, race_id)
            if not danwa_data:
                st.error("馬データが見つかりませんでした (厩舎の話ページ取得失敗)")
                continue

            # --- 新馬戦判定 ---
            race_title = header_info.get("header_text", "")
            is_shinba = ("新馬" in race_title) or ("メイクデビュー" in race_title)
            if is_shinba:
                st.caption("🌱 新馬戦(メイクデビュー)モードで解析します")

            # 2. CPU予想
            cpu_data = fetch_keibabook_cpu_data(driver, race_id, is_shinba=is_shinba)

            # ★スピード指標を算出（偏差値dev + 上位強調score）
            # 競馬予想の見た目として自然な「メリハリ」は score を使うのがおすすめ
            speed_metrics = compute_speed_metrics(cpu_data, T=55.0, k=4.0)

            # 3. 前走インタビュー
            interview_data = fetch_zenkoso_interview(driver, race_id)

            # 4. 調教データ
            chokyo_data = fetch_keibabook_chokyo(driver, race_id)

            # 5. Netkeiba (騎手・戦績)
            nk_data = fetch_netkeiba_data(driver, YEAR, KAI, PLACE, DAY, race_num_str)

            # --- データ統合 ---
            lines = []
            for umaban in sorted(danwa_data.keys(), key=int):
                d_info = danwa_data[umaban]
                c_info = cpu_data.get(umaban, {})
                i_text = interview_data.get(umaban, "なし")
                k_info = chokyo_data.get(umaban, {"tanpyo": "-", "details": "-"})
                n_info = nk_data.get(umaban, {})

                # 戦績テキスト
                past_list = n_info.get("past", [])
                past_str = " / ".join(past_list) if past_list else "情報なし"

                # スピード（表示用score + 監査用dev + raw）
                sm = speed_metrics.get(umaban, {})
                sp_score = sm.get("score", "-")  # 0〜100（上位強調）
                sp_dev = sm.get("dev", "-")      # 偏差値（0〜100クリップ）
                sp_raw = sm.get("raw", "-")      # 基礎値

                # 指数テキスト（元の前/2/3/平は保持 + スピード指標を付与）
                # LLMには「score」を主に使わせ、dev/rawは補助情報として残す
                sp_str = (
                    f"指数(前/2/3/平):{c_info.get('sp_last','-')}/{c_info.get('sp_2','-')}/{c_info.get('sp_3','-')}/{c_info.get('sp_avg','-')} "
                    f"スピード指数:{sp_score} 偏差値:{sp_dev} 基礎値:{sp_raw}"
                )

                # ファクターテキスト分岐
                if is_shinba:
                    fac_str = f"F(出脚/血統/動き):{c_info.get('fac_deashi','-')}/{c_info.get('fac_kettou','-')}/{c_info.get('fac_ugoki','-')}"
                else:
                    fac_str = f"F(コ/距/前):{c_info.get('fac_crs','-')}/{c_info.get('fac_dis','-')}/{c_info.get('fac_zen','-')}"

                cpu_str = f"{sp_str} {fac_str}"

                # 調教テキスト
                chokyo_str = f"短評:{k_info['tanpyo']} / 詳細:{k_info['details']}"

                # 入力（LLM側のプロンプト要件に合わせて、HTMLタグは使わない）
                line = (
                    f"▼馬番{umaban} {d_info['name']} (騎手:{n_info.get('jockey','-')})\n"
                    f"【厩舎の話】{d_info['danwa']}\n"
                    f"【前走インタビュー】{i_text}\n"
                    f"【近走】{past_str}\n"
                    f"【データ】{cpu_str}\n"
                    f"【調教】{chokyo_str}\n"
                )
                lines.append(line)

            full_prompt = (
                f"■レース情報\n{header_info.get('header_text','')}\n\n"
                f"■各馬詳細\n" + "\n".join(lines)
            )

            # AI生成
            status.text("AI分析中...")
            result_area = st.empty()
            ai_output = ""
            for chunk in stream_dify_workflow(full_prompt):
                ai_output += chunk
                result_area.markdown(ai_output + "▌")
            result_area.markdown(ai_output)

            combined_text += f"\n\n--- {r}R ---\n{ai_output}"

            render_copy_button(ai_output, f"{r}R コピー", f"copy_btn_{r}")
            status.success("完了")

        st.subheader("全レースまとめ")
        render_copy_button(combined_text, "全レースコピー", "copy_btn_all")
        st.text_area("出力結果", combined_text, height=300)

    finally:
        driver.quit()

if __name__ == "__main__":
    st.title("🏇 競馬AI予想データ生成")
    run_all_races()
