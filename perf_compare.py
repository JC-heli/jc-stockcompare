import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests
import re
import threading
import uuid
from pathlib import Path
from io import StringIO
from datetime import date, datetime, timedelta

st.set_page_config(page_title="績效比較工具", layout="wide", page_icon="📊")

COLORS = ["#ffd54f", "#42a5f5", "#66bb6a", "#ef5350", "#ab47bc", "#26c6da"]
MAX_TARGETS = 6

@st.dialog("⚠️ 偵測到異常資料，建議調整區間")
def _anom_dialog(rows, _active_key, _pending_key, _last_date, _seen_key, _expand_key):
    st.markdown("""
    <style>
    [data-testid="stDialog"] [data-testid="stButton"] > button[kind="primary"] {
        background-color: #ef5350 !important;
        border-color: #ef5350 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    st.warning(
        "以下時段單日漲跌 > 50%，可能為 Yahoo Finance 髒資料。\n\n"
        "若分析區間包含這些日期，**報酬率、MDD 等指標會嚴重失真**。"
    )
    for row in rows:
        ca, cb = st.columns([3, 2])
        ca.markdown(f"**{row['標的']}** · {row['日期']} · `{row['單日變動%']:+.2f}%`")
        if cb.button(f"跳到 {row['skip_date']} 開始", key=f"dlg_{row['標的']}_{row['日期']}", type="primary"):
            st.session_state[_active_key]  = None
            st.session_state[_pending_key] = (row["skip_date"], _last_date)
            st.session_state[_seen_key]    = True
            st.rerun()
    st.divider()
    if st.button("我知道了，先不調整區間", type="secondary", use_container_width=True):
        st.session_state[_seen_key]    = True
        st.session_state[_expand_key]  = True
        st.rerun()

# ── 常用指數 / 海外標的 ───────────────────────────────────────────
COMMON_INDICES = [
    {"code": "^TWII",  "name": "加權指數",        "ticker": "^TWII",  "market": "指數"},
    {"code": "^TAIEX", "name": "台灣加權指數",     "ticker": "^TWII",  "market": "指數"},
    {"code": "^GSPC",  "name": "S&P 500",         "ticker": "^GSPC",  "market": "指數"},
    {"code": "^NDX",   "name": "Nasdaq 100",       "ticker": "^NDX",   "market": "指數"},
    {"code": "^DJI",   "name": "道瓊工業指數",     "ticker": "^DJI",   "market": "指數"},
    {"code": "SPY",    "name": "SPDR S&P500 ETF",  "ticker": "SPY",    "market": "美股"},
    {"code": "QQQ",    "name": "Nasdaq 100 ETF",   "ticker": "QQQ",    "market": "美股"},
    {"code": "GLD",    "name": "黃金 ETF",          "ticker": "GLD",    "market": "美股"},
    {"code": "BTC-USD","name": "比特幣",            "ticker": "BTC-USD","market": "加密"},
]

TW_CSV = str(Path(__file__).parent / "tw_stocks.csv")

def fetch_tw_stocks_remote():
    stocks = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for mode, suffix, market in [("2", ".TW", "上市"), ("4", ".TWO", "上櫃"), ("5", ".TWO", "興櫃")]:
        try:
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            resp = requests.get(url, timeout=20, headers=headers)
            resp.encoding = "big5"
            tables = pd.read_html(StringIO(resp.text))
            for tbl in tables:
                for val in tbl.iloc[:, 0].dropna().astype(str):
                    val = val.strip()
                    m = re.match(r'^([A-Za-z0-9]{4,8})([^\x00-\x7F].*)$', val)
                    if m:
                        code, name = m.group(1), m.group(2).strip()
                        if name and len(code) >= 4:
                            stocks.append({"code": code, "name": name,
                                           "ticker": code + suffix, "market": market})
        except Exception:
            pass
    seen = set()
    result = []
    for s in stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            result.append(s)
    return result

@st.cache_data(show_spinner=False)
def load_tw_stocks():
    candidates = [
        Path(__file__).parent / "tw_stocks.csv",
        Path("tw_stocks.csv"),
        Path("tools/tw_stocks.csv"),
    ]
    for p in candidates:
        try:
            df = pd.read_csv(p, dtype=str)
            if not df.empty:
                return df.to_dict("records")
        except FileNotFoundError:
            pass
    return []

def search_local(query: str, tw_stocks: list, top: int = 15):
    q = query.strip().upper()
    all_stocks = COMMON_INDICES + tw_stocks
    by_code = [s for s in all_stocks if s["code"].upper().startswith(q)]
    by_name = [s for s in all_stocks if query in s["name"] and s not in by_code]
    return (by_code + by_name)[:top]

# ── 搜尋 Widget ───────────────────────────────────────────────────
def ticker_widget(prefix, default_ticker, default_label, tw_stocks):
    if f"{prefix}_ticker" not in st.session_state:
        st.session_state[f"{prefix}_ticker"] = default_ticker
    if f"{prefix}_label" not in st.session_state:
        st.session_state[f"{prefix}_label"] = default_label

    col_q, col_btn = st.columns([3, 1])
    with col_q:
        query = st.text_input(
            "🔍 搜尋（代碼或中文名稱）",
            placeholder="例：00631L、台積電、SPY",
            key=f"{prefix}_query",
        )
    with col_btn:
        st.write("")
        do_search = st.button("搜尋", key=f"{prefix}_btn")

    last_q = st.session_state.get(f"{prefix}_last_q", "")
    if query and (do_search or query != last_q):
        st.session_state[f"{prefix}_last_q"] = query
        hits = search_local(query, tw_stocks)
        if hits:
            st.session_state[f"{prefix}_results"] = [
                {"label": f"{s['code']} {s['name']} [{s['market']}]",
                 "ticker": s["ticker"],
                 "display_name": f"{s['code']} {s['name']}" if s["market"] in ("上市","上櫃","興櫃") else s["name"]}
                for s in hits
            ]
        else:
            try:
                yf_res = yf.Search(query, max_results=8)
                yf_hits = [q for q in yf_res.quotes if q.get("symbol")]
                st.session_state[f"{prefix}_results"] = [
                    {"label": f"{q['symbol']} {q.get('shortname','')} [{q.get('exchDisp','')}]",
                     "ticker": q["symbol"],
                     "display_name": q["symbol"]}
                    for q in yf_hits
                ]
            except Exception as e:
                st.session_state[f"{prefix}_results"] = []
                st.caption(f"搜尋失敗：{e}")

    results = st.session_state.get(f"{prefix}_results", [])
    if results:
        opts = {r["label"]: r for r in results}
        chosen_label = st.selectbox("↓ 選擇", list(opts.keys()), key=f"{prefix}_sel")
        chosen = opts[chosen_label]
        st.session_state[f"{prefix}_ticker"] = chosen["ticker"]
        st.session_state[f"{prefix}_label"]  = chosen["display_name"]
        st.session_state[f"{prefix}_lbl"]    = chosen["display_name"]
    elif st.session_state.get(f"{prefix}_results") == []:
        st.caption("查無結果")

    st.caption(f"✅ 已選：`{st.session_state[f'{prefix}_ticker']}`")
    label_input = st.text_input(
        "顯示名稱",
        value=st.session_state[f"{prefix}_label"],
        key=f"{prefix}_lbl",
    )
    return st.session_state[f"{prefix}_ticker"], label_input

# ── 資料抓取 ──────────────────────────────────────────────────────
def _to_series(df_or_series, ticker: str) -> pd.Series:
    close = df_or_series["Close"] if hasattr(df_or_series, "columns") else df_or_series
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.squeeze()
    s = pd.Series(s.values, index=s.index, name=ticker)
    s.index = s.index.tz_localize(None) if s.index.tz else s.index
    return s

_twse_cache: dict = {}   # module-level，避免巢狀 @st.cache_data 失效問題

def _fetch_twse_month(stock_code: str, year_month: str) -> dict:
    """從 TWSE 抓單月每日收盤，回傳 {pd.Timestamp: close}。"""
    key = (stock_code, year_month)
    if key in _twse_cache:
        return _twse_cache[key]
    result = {}
    try:
        resp = requests.get(
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
            params={"response": "json", "date": year_month + "01", "stockNo": stock_code},
            timeout=10, headers={"User-Agent": "Mozilla/5.0"},
        )
        data = resp.json()
        if data.get("stat") == "OK":
            for row in data.get("data", []):
                try:
                    roc = row[0].split("/")           # 民國年 "113/01/02"
                    ts  = pd.Timestamp(int(roc[0]) + 1911, int(roc[1]), int(roc[2]))
                    close_str = row[6].replace(",", "")
                    if close_str and close_str != "--":
                        result[ts] = float(close_str)
                except Exception:
                    pass
    except Exception:
        pass
    _twse_cache[key] = result
    return result

def _build_ref_prices(ticker: str, bad_idx) -> dict:
    """
    建立參考收盤價字典 {Timestamp: close}。
    .TW 上市股 → TWSE 官方；其他 → Yahoo auto_adjust=False。
    """
    ref = {}
    if ticker.endswith(".TW"):
        stock_code = ticker[:-3]
        year_months = set()
        for dt in bad_idx:
            year_months.add(dt.strftime("%Y%m"))
            # 也抓前一個月（確保 bad_idx 第一天的前一天資料也在）
            year_months.add((dt - pd.DateOffset(months=1)).strftime("%Y%m"))
        for ym in sorted(year_months):
            ref.update(_fetch_twse_month(stock_code, ym))

    if not ref:
        # fallback：Yahoo 原始收盤
        try:
            raw_df = yf.download(ticker, start="1990-01-01", interval="1d",
                                 auto_adjust=False, progress=False)
            if not raw_df.empty:
                raw = _to_series(raw_df, ticker)
                for dt in raw.index:
                    ref[dt] = float(raw.loc[dt])
        except Exception:
            pass
    return ref

def _fix_bad_adjustments(adj: pd.Series, ticker: str, threshold: float = 0.50) -> pd.Series:
    """
    用官方收盤（TWSE 或 Yahoo raw）驗證 auto_adjust 是否有假調整。
    若官方顯示正常而 adj 有大幅跳空 → 乘回正確倍數。

    prev_ref 用 ref 裡最近一個在異常日前的日期，
    避免台灣補假日不在 TWSE 資料中導致查不到而跳過。
    """
    pct = adj.pct_change()
    bad_idx = adj.index[pct.abs() > threshold]
    if len(bad_idx) == 0:
        return adj

    ref = _build_ref_prices(ticker, bad_idx)
    if not ref:
        return adj

    ref_dates_sorted = sorted(ref.keys())

    result = adj.copy().astype(float)
    for dt in bad_idx:
        loc = result.index.get_loc(dt)
        if loc == 0:
            continue

        curr_ref = ref.get(dt)
        if curr_ref is None:
            continue

        # 找 ref 裡最近一個在 dt 之前的日期（跨越台灣補假日）
        prev_ref_dates = [d for d in ref_dates_sorted if d < dt]
        if not prev_ref_dates:
            continue
        prev_ref = ref[prev_ref_dates[-1]]
        if prev_ref == 0:
            continue

        ref_pct = curr_ref / prev_ref - 1
        if abs(ref_pct) >= threshold:
            continue  # 官方也有大幅變動 → 真實事件，不動

        # 官方正常 → Yahoo 假調整：把異常日起所有資料乘回正確倍數
        corrected = result.iloc[loc - 1] * (1 + ref_pct)
        scale     = corrected / result.iloc[loc]
        result.iloc[loc:] *= scale
    return result

@st.cache_data(ttl=3600)
def _load_series_raw(ticker: str) -> pd.Series:
    """下載原始還原值，不套用修正（獨立快取）。"""
    df = yf.download(ticker, start="1990-01-01", interval="1d",
                     auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"Yahoo Finance 未回傳 {ticker} 的資料，請稍後再試")
    s = _to_series(df, ticker)
    if s.dropna().empty:
        raise ValueError(f"{ticker} 資料全為空值，請確認代碼是否正確")
    return s

@st.cache_data(ttl=3600)
def load_series(ticker: str) -> pd.Series:
    """載入並修正還原值（兩層快取：raw 下載 + 修正各自獨立）。"""
    s = _load_series_raw(ticker)
    return _fix_bad_adjustments(s, ticker)

# ── 台股清單背景自動更新 ──────────────────────────────────────────
def _bg_fetch_worker():
    try:
        rows = fetch_tw_stocks_remote()
        if rows:
            pd.DataFrame(rows).to_csv(TW_CSV, index=False)
            st.session_state["_tw_bg_count"] = len(rows)
        else:
            st.session_state["_tw_bg_count"] = 0
    except Exception:
        st.session_state["_tw_bg_count"] = 0

def _start_bg_update_if_needed():
    csv_path = Path(TW_CSV)
    stale = (not csv_path.exists() or
             datetime.now() - datetime.fromtimestamp(csv_path.stat().st_mtime) > timedelta(hours=23))
    if stale and "_tw_bg_thread" not in st.session_state:
        t = threading.Thread(target=_bg_fetch_worker, daemon=True)
        t.daemon = True
        t.start()
        st.session_state["_tw_bg_thread"] = t
        st.session_state["_tw_bg_count"] = None

_start_bg_update_if_needed()
tw_stocks = load_tw_stocks()

# ── 初始化 targets ────────────────────────────────────────────────
if "targets" not in st.session_state:
    st.session_state["targets"] = [
        {"id": "a", "ticker": "^TWII",    "label": "加權指數"},
        {"id": "b", "ticker": "0050.TW",  "label": "0050 元大台灣50"},
        {"id": "c", "ticker": "0051.TW",  "label": "0051 元大中型100"},
        {"id": "d", "ticker": "00631L.TW","label": "00631L 元大台灣50正2"},
        {"id": "e", "ticker": "^IXIC",    "label": "Nasdaq Composite"},
        {"id": "f", "ticker": "^SOX",     "label": "Philadelphia SOX"},
    ]

# ── 側邊欄 ────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 查詢設定")

    bg_count = st.session_state.get("_tw_bg_count")
    bg_thread = st.session_state.get("_tw_bg_thread")
    if bg_thread is not None and bg_thread.is_alive():
        st.info("🔄 台股清單更新中，可繼續操作…", icon=None)
    elif bg_count is not None and bg_count > 0 and len(tw_stocks) == 0:
        load_tw_stocks.clear()
        tw_stocks = load_tw_stocks()
        st.success(f"✅ 台股清單已載入 {len(tw_stocks)} 支")
    elif tw_stocks:
        st.caption(f"✅ 台股清單：{len(tw_stocks)} 支")
    else:
        st.caption("台股清單尚未就緒")

    targets = st.session_state["targets"]
    to_delete = None

    for idx, t in enumerate(targets):
        st.divider()
        color = COLORS[idx % len(COLORS)]
        if len(targets) > 2:
            col_title, col_del = st.columns([4, 1])
            with col_title:
                st.markdown(
                    f"<span style='color:{color};font-size:16px'>●</span> **標的 {idx+1}**",
                    unsafe_allow_html=True,
                )
            with col_del:
                st.write("")
                if st.button("✕", key=f"del_{t['id']}", help="移除此標的"):
                    to_delete = idx
        else:
            st.markdown(
                f"<span style='color:{color};font-size:16px'>●</span> **標的 {idx+1}**",
                unsafe_allow_html=True,
            )

        ticker, label = ticker_widget(t["id"], t["ticker"], t["label"], tw_stocks)
        t["ticker"] = ticker
        t["label"]  = label

        try:
            _s = load_series(ticker).dropna()
            st.caption(f"歷史資料：{_s.index[0].date()} ～ {_s.index[-1].date()}  \n共 {len(_s)} 個交易日")
        except Exception as e:
            st.caption(f"⚠️ 無法載入：{e}")

    if to_delete is not None:
        st.session_state["targets"].pop(to_delete)
        st.rerun()

    st.divider()
    if len(targets) < MAX_TARGETS:
        if st.button("➕ 新增標的", use_container_width=True):
            new_id = uuid.uuid4().hex[:8]
            st.session_state["targets"].append({"id": new_id, "ticker": "SPY", "label": "SPY"})
            st.rerun()

    st.caption("資料來源：Yahoo Finance / TWSE")
    st.caption(f"更新時間：{datetime.now().strftime('%H:%M:%S')}")
    if st.button("🔄 重新整理"):
        st.cache_data.clear()
        st.rerun()

# ── 資料載入 ──────────────────────────────────────────────────────
targets   = st.session_state["targets"]
tickers   = [t["ticker"] for t in targets]
labels    = [t["label"]  for t in targets]
col_names = [f"__T{i}__" for i in range(len(tickers))]

series_list = []
with st.spinner("載入資料中..."):
    for i, ticker in enumerate(tickers):
        try:
            series_list.append(load_series(ticker))
        except Exception as e:
            st.error(f"標的 {i+1}（{ticker}）資料抓取失敗：{e}")
            st.stop()

df = pd.DataFrame({col: s for col, s in zip(col_names, series_list)}).ffill().dropna()

if df.empty:
    st.error("找不到共同交易日資料，請確認代碼是否正確")
    st.stop()
if len(df) < 2:
    st.error("資料不足，請確認代碼是否正確")
    st.stop()

# ── 異常資料偵測 ──────────────────────────────────────────────────
def detect_anomalies(series: pd.Series, threshold: float = 0.50) -> pd.DataFrame:
    pct = series.pct_change()
    mask = pct.abs() > threshold
    if not mask.any():
        return pd.DataFrame(columns=["日期", "前日收盤", "當日收盤", "單日變動%"])
    rows = []
    for d in series.index[mask]:
        loc = series.index.get_loc(d)
        prev = series.iloc[loc - 1] if loc > 0 else float("nan")
        curr = series.iloc[loc]
        rows.append({
            "日期": d.date(),
            "前日收盤": round(float(prev), 4),
            "當日收盤": round(float(curr), 4),
            "單日變動%": round(float(pct.loc[d]) * 100, 2),
        })
    return pd.DataFrame(rows)

anom_list = [detect_anomalies(df[col]) for col in col_names]

# ── 績效計算 ──────────────────────────────────────────────────────
def calc_metrics(series: pd.Series, name: str) -> dict:
    arr = series.to_numpy(dtype=float)
    v0, v1 = arr[0], arr[-1]
    years     = (series.index[-1] - series.index[0]).days / 365.25
    total_ret = (v1 / v0 - 1) * 100
    ann_ret   = ((v1 / v0) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    drawdown  = (series / series.cummax()) - 1
    mdd       = float(drawdown.min()) * 100
    trough_dt = drawdown.idxmin()
    peak_dt   = series.loc[:trough_dt].idxmax()
    mdd_range = f"{peak_dt.date()} ～ {trough_dt.date()}"
    vol       = float(series.pct_change().dropna().std()) * np.sqrt(252) * 100
    sharpe    = ann_ret / vol if vol > 0 else 0.0
    return {"名稱": name, "總報酬": total_ret, "年化報酬": ann_ret,
            "MDD": mdd, "MDD區間": mdd_range, "年化波動": vol, "Sharpe": sharpe}

# ── 標題 ──────────────────────────────────────────────────────────
if len(labels) == 2:
    st.title(f"📊 {labels[0]}  vs  {labels[1]}  績效比較")
else:
    st.title(f"📊 {len(labels)} 支標的  績效比較")

# ── 區間分析滑桿 ──────────────────────────────────────────────────
all_dates    = df.index.date.tolist()
tickers_key  = "_".join(tickers)
slider_key   = f"slider_{tickers_key}"
pending_key  = f"pending_range_{tickers_key}"
active_key   = f"active_preset_{tickers_key}"
di_start_key = f"di_start_{tickers_key}"
di_end_key   = f"di_end_{tickers_key}"
first_date, last_date = all_dates[0], all_dates[-1]

def snap_date(d):
    return min(all_dates, key=lambda x: abs((x - d).days))

if pending_key in st.session_state:
    ns, ne = st.session_state.pop(pending_key)
    st.session_state[slider_key]   = (ns, ne)
    st.session_state[di_start_key] = ns
    st.session_state[di_end_key]   = ne

prev_slider_key = f"prev_slider_{tickers_key}"

v_start, v_end = st.select_slider(
    "拖拉選取分析區間 ── 圖表會同步縮放",
    options=all_dates,
    value=(first_date, last_date),
    key=slider_key,
)

prev_slider  = st.session_state.get(prev_slider_key)
slider_moved = prev_slider is not None and prev_slider != (v_start, v_end)
st.session_state[prev_slider_key] = (v_start, v_end)
if slider_moved:
    st.session_state[di_start_key] = v_start
    st.session_state[di_end_key]   = v_end

# ── 快速選取列 ────────────────────────────────────────────────────
presets = [
    ("5日",   timedelta(days=5)),
    ("1個月", timedelta(days=30)),
    ("3個月", timedelta(days=91)),
    ("6個月", timedelta(days=182)),
    ("今年",  "ytd"),
    ("5年",   timedelta(days=365 * 5)),
    ("10年",  timedelta(days=365 * 10)),
]
active_preset = st.session_state.get(active_key, None)
p_cols = st.columns([1, 1.2, 1.2, 1.2, 1, 1, 1.3, 0.6, 2, 0.4, 2])
for i, (lbl, delta) in enumerate(presets):
    btn_type = "primary" if lbl == active_preset else "secondary"
    if p_cols[i].button(lbl, use_container_width=True, type=btn_type, key=f"preset_{lbl}_{tickers_key}"):
        if delta == "ytd":
            ns = snap_date(max(date(last_date.year, 1, 1), first_date))
        else:
            ns = snap_date(max(last_date - delta, first_date))
        st.session_state[active_key]  = lbl
        st.session_state[pending_key] = (ns, last_date)
        st.rerun()

p_cols[7].markdown("<div style='text-align:center;padding-top:8px'></div>", unsafe_allow_html=True)
d_start = p_cols[8].date_input("", value=v_start, min_value=first_date, max_value=last_date,
                                key=di_start_key, label_visibility="collapsed")
p_cols[9].markdown("<div style='text-align:center;padding-top:8px'>～</div>", unsafe_allow_html=True)
d_end   = p_cols[10].date_input("", value=v_end, min_value=first_date, max_value=last_date,
                                 key=di_end_key, label_visibility="collapsed")
if (d_start, d_end) != (v_start, v_end):
    ns, ne = snap_date(d_start), snap_date(d_end)
    if ns < ne:
        st.session_state[active_key]  = None
        st.session_state[pending_key] = (ns, ne)
        st.rerun()

# ── 異常時段說明 ──────────────────────────────────────────────────
all_anom_rows = []
for i, anom in enumerate(anom_list):
    for _, row in anom.iterrows():
        if v_start <= row["日期"] <= v_end:
            skip_date = snap_date(row["日期"] + timedelta(days=1))
            all_anom_rows.append({"標的": labels[i], **row, "skip_date": skip_date})

if all_anom_rows:
    # Fingerprint 只看異常內容（標的+日期），與 ticker 組合無關
    # → 新增不相干標的不會重複彈出已讀過的異常
    _fp = "_".join(f"{r['標的']}_{r['日期']}"
                   for r in sorted(all_anom_rows, key=lambda x: str(x["日期"])))
    anom_seen_key   = f"anom_seen_{_fp}"
    anom_expand_key = f"anom_expand_{_fp}"
    if not st.session_state.get(anom_seen_key):
        _anom_dialog(all_anom_rows, active_key, pending_key, last_date, anom_seen_key, anom_expand_key)

    total = len(all_anom_rows)
    with st.expander(f"⚠️ 偵測到 {total} 筆異常資料（單日漲跌 > 50%，可能為 Yahoo Finance 髒資料）",
                     expanded=True):
        st.caption("建議選取分析區間時避開以下時段，否則報酬率、MDD 等指標會嚴重失真。")
        hdr = st.columns([2, 1.2, 1.2, 1.2, 1.2, 1.5])
        for txt, col in zip(["標的", "日期", "前日收盤", "當日收盤", "單日變動%", ""], hdr):
            col.markdown(f"**{txt}**")
        for row in all_anom_rows:
            c1, c2, c3, c4, c5, c6 = st.columns([2, 1.2, 1.2, 1.2, 1.2, 1.5])
            c1.write(row["標的"])
            c2.write(str(row["日期"]))
            c3.write(f"{row['前日收盤']:.4f}")
            c4.write(f"{row['當日收盤']:.4f}")
            c5.write(f"{row['單日變動%']:+.2f}%")
            if c6.button(f"從 {row['skip_date']} 開始", key=f"skip_{row['標的']}_{row['日期']}"):
                st.session_state[active_key]  = None
                st.session_state[pending_key] = (row["skip_date"], last_date)
                st.rerun()

# ── 區間績效指標 ──────────────────────────────────────────────────
view_mask = (df.index.date >= v_start) & (df.index.date <= v_end)
df_view   = df.loc[view_mask]

if len(df_view) >= 2:
    st.caption(f"{v_start} ～ {v_end}　共 {len(df_view)} 個交易日")
    metrics_view = [calc_metrics(df_view[col], labels[i]) for i, col in enumerate(col_names)]
    n = len(metrics_view)

    if n == 2:
        mv1, mv2 = metrics_view[0], metrics_view[1]
        lbl1, lbl2 = labels[0], labels[1]

        def pos_delta(val, ref, pct=True, suffix=""):
            diff = val - ref
            if diff <= 0:
                return None
            fmt = "+.1f" if pct else "+.2f"
            unit = "%" if pct else ""
            return f"{diff:{fmt}}{unit}{suffix}"

        def inv_delta(val, ref):
            diff = val - ref
            return f"{diff:+.1f}%" if diff < 0 else None

        lbl_col, c1, c2, c3, c4, c5 = st.columns([1.2, 2, 2, 2, 2, 2])
        lbl_col.markdown(f"**{lbl1}**")
        c1.metric("總報酬",   f"{mv1['總報酬']:+.1f}%",   pos_delta(mv1['總報酬'],   mv2['總報酬'],   suffix=f" vs {lbl2}"))
        c2.metric("年化報酬", f"{mv1['年化報酬']:+.1f}%", pos_delta(mv1['年化報酬'], mv2['年化報酬'], suffix=f" vs {lbl2}"))
        c3.metric(f"MDD（{mv1['MDD區間']}）", f"{mv1['MDD']:.1f}%", inv_delta(mv1['MDD'], mv2['MDD']), delta_color="inverse")
        c4.metric("年化波動", f"{mv1['年化波動']:.1f}%",  inv_delta(mv1['年化波動'],mv2['年化波動']), delta_color="inverse")
        c5.metric("Sharpe",  f"{mv1['Sharpe']:.2f}",     pos_delta(mv1['Sharpe'],  mv2['Sharpe'],   pct=False, suffix=f" vs {lbl2}"))

        lbl_col2, d1, d2, d3, d4, d5 = st.columns([1.2, 2, 2, 2, 2, 2])
        lbl_col2.markdown(f"**{lbl2}**")
        d1.metric("總報酬",   f"{mv2['總報酬']:+.1f}%",   pos_delta(mv2['總報酬'],   mv1['總報酬'],   suffix=f" vs {lbl1}"))
        d2.metric("年化報酬", f"{mv2['年化報酬']:+.1f}%", pos_delta(mv2['年化報酬'], mv1['年化報酬'], suffix=f" vs {lbl1}"))
        d3.metric(f"MDD（{mv2['MDD區間']}）", f"{mv2['MDD']:.1f}%", inv_delta(mv2['MDD'], mv1['MDD']), delta_color="inverse")
        d4.metric("年化波動", f"{mv2['年化波動']:.1f}%",  inv_delta(mv2['年化波動'],mv1['年化波動']), delta_color="inverse")
        d5.metric("Sharpe",  f"{mv2['Sharpe']:.2f}",     pos_delta(mv2['Sharpe'],  mv1['Sharpe'],   pct=False, suffix=f" vs {lbl1}"))

    else:
        # 3+ 標的：表格顯示
        rows = []
        for mv in metrics_view:
            rows.append({
                "標的":    mv["名稱"],
                "總報酬%": mv["總報酬"],
                "年化報酬%": mv["年化報酬"],
                "MDD%":   mv["MDD"],
                "MDD區間": mv["MDD區間"],
                "年化波動%": mv["年化波動"],
                "Sharpe": mv["Sharpe"],
            })
        mdf = pd.DataFrame(rows)

        def _color_ret(v):
            return "color: #ef5350" if v < 0 else "color: #26a69a"

        st.dataframe(
            mdf.style
               .format({"總報酬%": "{:+.1f}%", "年化報酬%": "{:+.1f}%",
                        "MDD%": "{:.1f}%", "年化波動%": "{:.1f}%", "Sharpe": "{:.2f}"})
               .map(_color_ret, subset=["總報酬%", "年化報酬%"]),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ── 圖1：報酬率走勢圖 ─────────────────────────────────────────────
view_mask_chart = (df.index.date >= v_start) & (df.index.date <= v_end)
fig1 = go.Figure()

for i, (col, label, color) in enumerate(zip(col_names, labels, COLORS)):
    base = df.loc[view_mask_chart, col].iloc[0]
    pct  = (df[col] / base - 1) * 100
    fig1.add_trace(go.Scatter(
        x=df.index, y=pct, name=label,
        line=dict(color=color, width=1.8),
    ))
    end_val = pct.loc[view_mask_chart].iloc[-1]
    end_x   = df.loc[view_mask_chart].index[-1]
    fig1.add_annotation(
        x=end_x, y=end_val, text=f"<b>{end_val:+.1f}%</b>",
        showarrow=False, xanchor="left", yanchor="middle",
        font=dict(color=color, size=12), xshift=6,
    )

fig1.add_hline(y=0, line_dash="dot", line_color="gray", line_width=0.8,
               annotation_text="±0%", annotation_position="right")

anom_fill_colors = ["red", "orange", "lightgreen", "hotpink", "plum", "cyan"]
for i, anom in enumerate(anom_list):
    fill_color = anom_fill_colors[i % len(anom_fill_colors)]
    for _, row in anom.iterrows():
        d = row["日期"]
        fig1.add_vrect(
            x0=str(d - timedelta(days=1)), x1=str(d + timedelta(days=1)),
            fillcolor=fill_color, opacity=0.25, line_width=0,
            annotation_text=f"⚠️{i+1}", annotation_position="top left",
            annotation_font=dict(color=fill_color, size=10),
        )

fig1.update_layout(
    template="plotly_dark",
    title=f"報酬率比較（以所選區間起點 {v_start} 為基準）",
    height=460,
    xaxis=dict(range=[str(v_start), str(v_end)], rangeslider_visible=False),
    yaxis=dict(tickformat="+.0f", ticksuffix="%", showgrid=True),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    margin=dict(l=70, r=90, t=60, b=40),
)
st.plotly_chart(fig1, use_container_width=True)

# ── 圖2：逐年報酬長條圖 ───────────────────────────────────────────
def annual_ret(series):
    ann = series.resample("YE").last().pct_change() * 100
    ann.index = ann.index.year
    return ann.dropna()

ann_list = [annual_ret(df[col]) for col in col_names]
years_common = ann_list[0].index
for ann in ann_list[1:]:
    years_common = years_common.intersection(ann.index)

if len(years_common) > 0:
    fig2 = go.Figure()
    for i, (ann, label, color) in enumerate(zip(ann_list, labels, COLORS)):
        vals = ann.loc[years_common]
        fig2.add_trace(go.Bar(
            x=years_common, y=vals, name=label,
            marker_color=color, opacity=0.85,
            text=[f"{v:+.1f}%" for v in vals],
            textposition="outside",
        ))
    fig2.add_hline(y=0, line_color="white", line_width=0.6)
    fig2.update_layout(
        template="plotly_dark",
        title="逐年報酬率比較",
        barmode="group",
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=60, r=60, t=60, b=40),
        yaxis_title="%",
        xaxis=dict(tickmode="linear", dtick=1),
    )
    st.plotly_chart(fig2, use_container_width=True)

    st.subheader("逐年報酬明細")
    ann_df = pd.DataFrame({labels[i]: ann_list[i] for i in range(len(labels))}).loc[years_common]

    def color_val(v):
        return "color: #ef5350" if v < 0 else "color: #26a69a"

    st.dataframe(
        ann_df.style.format("{:+.1f}%").map(color_val),
        use_container_width=True,
    )
