import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests
import re
import threading
from pathlib import Path
from io import StringIO
from datetime import date, datetime, timedelta

st.set_page_config(page_title="績效比較工具", layout="wide", page_icon="📊")

# ── 常用指數 / 海外標的（不在 TWSE 清單內）──────────────────────
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

# ── 台股對照表（本地 CSV 快取，只有第一次或手動更新才重抓）────────
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
            # 掃所有列，不依賴固定 header 行數
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
    # 去重（同代碼以第一筆為準）
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

    # Enter 鍵觸發：query 與上次搜尋不同時自動執行
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
            # fallback: Yahoo Finance Search
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
@st.cache_data(ttl=3600)
def load_series(ticker: str) -> pd.Series:
    raw = yf.download(ticker, start="1990-01-01", interval="1d",
                      auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"Yahoo Finance 未回傳 {ticker} 的資料，請稍後再試")
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.squeeze()
    s = pd.Series(s.values, index=s.index, name=ticker)
    s.index = s.index.tz_localize(None) if s.index.tz else s.index
    if s.dropna().empty:
        raise ValueError(f"{ticker} 資料全為空值，請確認代碼是否正確")
    return s

# ── 台股清單背景自動更新 ──────────────────────────────────────────
def _bg_fetch_worker():
    """背景執行緒：抓台股清單並存 CSV"""
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
        st.session_state["_tw_bg_count"] = None   # None = 進行中

_start_bg_update_if_needed()

# 讀取（背景更新完成後下次互動自動取到新資料）
tw_stocks = load_tw_stocks()

# ── 側邊欄 ────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 查詢設定")

    # ── 台股清單狀態列（頂部）────────────────────────────────────
    bg_count = st.session_state.get("_tw_bg_count")
    bg_thread = st.session_state.get("_tw_bg_thread")
    if bg_thread is not None and bg_thread.is_alive():
        st.info("🔄 台股清單更新中，可繼續操作…", icon=None)
    elif bg_count is not None and bg_count > 0 and len(tw_stocks) == 0:
        # 背景剛完成，清快取重載
        load_tw_stocks.clear()
        tw_stocks = load_tw_stocks()
        st.success(f"✅ 台股清單已載入 {len(tw_stocks)} 支")
    elif tw_stocks:
        st.caption(f"✅ 台股清單：{len(tw_stocks)} 支")
    else:
        st.caption("台股清單尚未就緒")

    st.divider()
    st.subheader("📌 標的 A（真實價格）")
    ticker1, label1 = ticker_widget("a", "^TWII", "加權指數", tw_stocks)
    try:
        _s1 = load_series(ticker1).dropna()
        st.caption(f"歷史資料：{_s1.index[0].date()} ～ {_s1.index[-1].date()}  \n共 {len(_s1)} 個交易日")
    except Exception as e:
        st.caption(f"⚠️ 無法載入：{e}")

    st.divider()
    st.subheader("📌 標的 B（右軸 %）")
    ticker2, label2 = ticker_widget("b", "00631L.TW", "00631L 元大台灣50正2", tw_stocks)
    try:
        _s2 = load_series(ticker2).dropna()
        st.caption(f"歷史資料：{_s2.index[0].date()} ～ {_s2.index[-1].date()}  \n共 {len(_s2)} 個交易日")
    except Exception as e:
        st.caption(f"⚠️ 無法載入：{e}")

    st.divider()
    st.caption("資料來源：Yahoo Finance / TWSE")
    st.caption(f"更新時間：{datetime.now().strftime('%H:%M:%S')}")
    if st.button("🔄 重新整理"):
        st.cache_data.clear()
        st.rerun()

with st.spinner("載入資料中..."):
    try:
        s1 = load_series(ticker1)
        s2 = load_series(ticker2)
    except Exception as e:
        st.error(f"資料抓取失敗：{e}")
        st.stop()


df = pd.DataFrame({"__A__": s1, "__B__": s2}).ffill().dropna()

if df.empty:
    st.error("找不到資料，請確認代碼是否正確")
    st.stop()

if len(df) < 2:
    st.error("資料不足，請確認代碼是否正確")
    st.stop()

# ── 異常資料偵測（單日漲跌 > 50%）────────────────────────────────
def detect_anomalies(series: pd.Series, threshold: float = 0.50) -> pd.DataFrame:
    pct = series.pct_change()
    mask = pct.abs() > threshold
    if not mask.any():
        return pd.DataFrame(columns=["日期", "前日收盤", "當日收盤", "單日變動%"])
    rows = []
    for i, d in enumerate(series.index[mask]):
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

m1 = calc_metrics(df["__A__"], label1)
m2 = calc_metrics(df["__B__"], label2)

# 偵測兩個標的的異常區間
anom1 = detect_anomalies(df["__A__"])
anom2 = detect_anomalies(df["__B__"])

# ── 標題 + 指標卡 ─────────────────────────────────────────────────
st.title(f"📊 {label1}  vs  {label2}  績效比較")

# ── 區間分析滑桿 ──────────────────────────────────────────────────
all_dates = df.index.date.tolist()
slider_key  = f"slider_{ticker1}_{ticker2}"
pending_key = f"pending_range_{ticker1}_{ticker2}"
active_key  = f"active_preset_{ticker1}_{ticker2}"
di_start_key = f"di_start_{ticker1}_{ticker2}"
di_end_key   = f"di_end_{ticker1}_{ticker2}"
first_date, last_date = all_dates[0], all_dates[-1]

def snap_date(d):
    return min(all_dates, key=lambda x: abs((x - d).days))

# 按鈕設好的 pending range，在 slider 渲染前套入（同步 date input）
if pending_key in st.session_state:
    ns, ne = st.session_state.pop(pending_key)
    st.session_state[slider_key]  = (ns, ne)
    st.session_state[di_start_key] = ns
    st.session_state[di_end_key]   = ne

prev_slider_key = f"prev_slider_{ticker1}_{ticker2}"

v_start, v_end = st.select_slider(
    "拖拉選取分析區間 ── 圖表會同步縮放",
    options=all_dates,
    value=(first_date, last_date),
    key=slider_key,
)

# 滑桿被拖動時，同步 date input（避免 date input 舊值觸發反向覆蓋）
prev_slider = st.session_state.get(prev_slider_key)
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
    if p_cols[i].button(lbl, use_container_width=True, type=btn_type, key=f"preset_{lbl}_{ticker1}_{ticker2}"):
        if delta == "ytd":
            ns = snap_date(max(date(last_date.year, 1, 1), first_date))
        else:
            ns = snap_date(max(last_date - delta, first_date))
        st.session_state[active_key]  = lbl
        st.session_state[pending_key] = (ns, last_date)
        st.rerun()

p_cols[7].markdown("<div style='text-align:center;padding-top:8px'></div>", unsafe_allow_html=True)
d_start = p_cols[8].date_input("", value=v_start, min_value=first_date, max_value=last_date,
                                key=f"di_start_{ticker1}_{ticker2}", label_visibility="collapsed")
p_cols[9].markdown("<div style='text-align:center;padding-top:8px'>～</div>", unsafe_allow_html=True)
d_end   = p_cols[10].date_input("", value=v_end, min_value=first_date, max_value=last_date,
                                 key=f"di_end_{ticker1}_{ticker2}", label_visibility="collapsed")
if (d_start, d_end) != (v_start, v_end):
    ns, ne = snap_date(d_start), snap_date(d_end)
    if ns < ne:
        st.session_state[active_key]  = None
        st.session_state[pending_key] = (ns, ne)
        st.rerun()

# ── 異常時段說明（滑桿正下方）────────────────────────────────────
all_anom_rows = []
for _, row in anom1.iterrows():
    if v_start <= row["日期"] <= v_end:
        all_anom_rows.append({"標的": label1, **row})
for _, row in anom2.iterrows():
    if v_start <= row["日期"] <= v_end:
        all_anom_rows.append({"標的": label2, **row})

if all_anom_rows:
    total = len(all_anom_rows)
    with st.expander(f"⚠️ 偵測到 {total} 筆異常資料（單日漲跌 > 50%，可能為 Yahoo Finance 髒資料）", expanded=True):
        st.caption("建議選取分析區間時避開以下時段，否則報酬率、MDD 等指標會嚴重失真。")
        anom_df = pd.DataFrame(all_anom_rows)[["標的", "日期", "前日收盤", "當日收盤", "單日變動%"]]
        st.dataframe(
            anom_df.style.format({"前日收盤": "{:.4f}", "當日收盤": "{:.4f}", "單日變動%": "{:+.2f}%"}),
            use_container_width=True,
            hide_index=True,
        )

# ── 區間績效指標 ──────────────────────────────────────────────────
view_mask = (df.index.date >= v_start) & (df.index.date <= v_end)
df_view   = df.loc[view_mask]

if len(df_view) >= 2:
    st.caption(f"{v_start} ～ {v_end}　共 {len(df_view)} 個交易日")
    mv1 = calc_metrics(df_view["__A__"], label1)
    mv2 = calc_metrics(df_view["__B__"], label2)

    def pos_delta(val, ref, pct=True, suffix=""):
        diff = val - ref
        if diff <= 0:
            return None
        unit = "%" if pct else ""
        fmt = "+.1f" if pct else "+.2f"
        return f"{diff:{fmt}}{unit}{suffix}"

    def inv_delta(val, ref):
        diff = val - ref
        return f"{diff:+.1f}%" if diff < 0 else None

    lbl_col, c1, c2, c3, c4, c5 = st.columns([1.2, 2, 2, 2, 2, 2])
    lbl_col.markdown(f"**{label1}**")
    c1.metric("總報酬",   f"{mv1['總報酬']:+.1f}%",   pos_delta(mv1['總報酬'],   mv2['總報酬'],   suffix=f" vs {label2}"))
    c2.metric("年化報酬", f"{mv1['年化報酬']:+.1f}%", pos_delta(mv1['年化報酬'], mv2['年化報酬'], suffix=f" vs {label2}"))
    c3.metric(f"MDD（{mv1['MDD區間']}）", f"{mv1['MDD']:.1f}%", inv_delta(mv1['MDD'], mv2['MDD']), delta_color="inverse")
    c4.metric("年化波動", f"{mv1['年化波動']:.1f}%",  inv_delta(mv1['年化波動'],mv2['年化波動']), delta_color="inverse")
    c5.metric("Sharpe",  f"{mv1['Sharpe']:.2f}",     pos_delta(mv1['Sharpe'],  mv2['Sharpe'],   pct=False, suffix=f" vs {label2}"))

    lbl_col2, d1, d2, d3, d4, d5 = st.columns([1.2, 2, 2, 2, 2, 2])
    lbl_col2.markdown(f"**{label2}**")
    d1.metric("總報酬",   f"{mv2['總報酬']:+.1f}%",   pos_delta(mv2['總報酬'],   mv1['總報酬'],   suffix=f" vs {label1}"))
    d2.metric("年化報酬", f"{mv2['年化報酬']:+.1f}%", pos_delta(mv2['年化報酬'], mv1['年化報酬'], suffix=f" vs {label1}"))
    d3.metric(f"MDD（{mv2['MDD區間']}）", f"{mv2['MDD']:.1f}%", inv_delta(mv2['MDD'], mv1['MDD']), delta_color="inverse")
    d4.metric("年化波動", f"{mv2['年化波動']:.1f}%",  inv_delta(mv2['年化波動'],mv1['年化波動']), delta_color="inverse")
    d5.metric("Sharpe",  f"{mv2['Sharpe']:.2f}",     pos_delta(mv2['Sharpe'],  mv1['Sharpe'],   pct=False, suffix=f" vs {label1}"))

st.divider()

# ── 圖1：報酬率走勢圖（統一單軸，以所選區間起點為基準）────────────
view_mask_chart = (df.index.date >= v_start) & (df.index.date <= v_end)
base_a = df.loc[view_mask_chart, "__A__"].iloc[0]
base_b = df.loc[view_mask_chart, "__B__"].iloc[0]
pct1 = (df["__A__"] / base_a - 1) * 100
pct2 = (df["__B__"] / base_b - 1) * 100

fig1 = go.Figure()
fig1.add_trace(go.Scatter(
    x=df.index, y=pct1, name=label1,
    line=dict(color="#ffd54f", width=1.8),
))
fig1.add_trace(go.Scatter(
    x=df.index, y=pct2, name=label2,
    line=dict(color="#42a5f5", width=1.8),
))
fig1.add_hline(y=0, line_dash="dot", line_color="gray", line_width=0.8,
               annotation_text="±0%", annotation_position="right")

# 區間終點標註各自的%數
end_mask = view_mask_chart
end_a = pct1.loc[end_mask].iloc[-1]
end_b = pct2.loc[end_mask].iloc[-1]
end_x = df.loc[end_mask].index[-1]
fig1.add_annotation(x=end_x, y=end_a, text=f"<b>{end_a:+.1f}%</b>",
    showarrow=False, xanchor="left", yanchor="middle",
    font=dict(color="#ffd54f", size=12), xshift=6)
fig1.add_annotation(x=end_x, y=end_b, text=f"<b>{end_b:+.1f}%</b>",
    showarrow=False, xanchor="left", yanchor="middle",
    font=dict(color="#42a5f5", size=12), xshift=6)

# 異常標記：標的 A 紅色，標的 B 橘色
for _, row in anom1.iterrows():
    d = row["日期"]
    fig1.add_vrect(
        x0=str(d - timedelta(days=1)), x1=str(d + timedelta(days=1)),
        fillcolor="red", opacity=0.3, line_width=0,
        annotation_text="⚠️A", annotation_position="top left",
        annotation_font=dict(color="#ff6b6b", size=10),
    )
for _, row in anom2.iterrows():
    d = row["日期"]
    fig1.add_vrect(
        x0=str(d - timedelta(days=1)), x1=str(d + timedelta(days=1)),
        fillcolor="orange", opacity=0.25, line_width=0,
        annotation_text="⚠️B", annotation_position="top right",
        annotation_font=dict(color="#ffb74d", size=10),
    )

fig1.update_layout(
    template="plotly_dark",
    title=f"報酬率比較（以所選區間起點 {v_start} 為基準）",
    height=460,
    xaxis=dict(
        range=[str(v_start), str(v_end)],
        rangeslider_visible=False,
    ),
    yaxis=dict(
        tickformat="+.0f",
        ticksuffix="%",
        showgrid=True,
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    margin=dict(l=70, r=90, t=60, b=40),
)
st.plotly_chart(fig1, use_container_width=True)

# ── 圖2：逐年報酬長條圖 ───────────────────────────────────────────
def annual_ret(series):
    ann = series.resample("YE").last().pct_change() * 100
    ann.index = ann.index.year
    return ann.dropna()

ann1 = annual_ret(df["__A__"])
ann2 = annual_ret(df["__B__"])
years_common = ann1.index.intersection(ann2.index)

if len(years_common) > 0:
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=years_common, y=ann1.loc[years_common], name=label1,
        marker_color="#ffd54f", opacity=0.85,
        text=[f"{v:+.1f}%" for v in ann1.loc[years_common]],
        textposition="outside",
    ))
    fig2.add_trace(go.Bar(
        x=years_common, y=ann2.loc[years_common], name=label2,
        marker_color="#42a5f5", opacity=0.85,
        text=[f"{v:+.1f}%" for v in ann2.loc[years_common]],
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
    ann_df = pd.DataFrame({label1: ann1, label2: ann2}).loc[years_common]

    def color_val(v):
        return "color: #ef5350" if v < 0 else "color: #26a69a"

    st.dataframe(
        ann_df.style.format("{:+.1f}%").map(color_val),
        use_container_width=True,
    )
