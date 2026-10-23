"""P1: 市场全景（盯盘）

实时查看美股和A股大类资产行情
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS, US_SECTORS_SYMBOLS
from core.data.config import MACRO_INDICATORS
from core.data.macro_fetcher import MacroFetcher
from core.data.macro_storage import MacroStorage
from core.data.macro_analyzer import compute_macro_analysis
from core.strategy.indicators import calc_momentum_score, calc_returns, calc_volatility
from app.components.charts import (
    create_candlestick_chart,
    create_line_chart,
    create_multi_line_chart,
)
from app.components.macro_charts import (
    create_macro_indicator_scatter,
    create_macro_timeline,
    render_macro_value_cards,
)
from app.components.tooltips import (
    VIX, MOMENTUM_SCORE, DRAWDOWN, VOLATILITY,
    CORRELATION, NARRATIVE_SHOCK, MACRO_SIMILARITY, MACRO_OUTLOOK,
    RETURN_7D, RETURN_1M, RETURN_3M,
)

# 页面配置
st.set_page_config(
    page_title="市场全景 - Market Pulse",
    page_icon=" ",
    layout="wide",
)

# 缓存 DataManager 实例
@st.cache_resource
def get_data_manager():
    return DataManager()


@st.cache_resource
def get_macro_storage():
    return MacroStorage()


@st.cache_resource
def get_macro_fetcher():
    return MacroFetcher()


@st.cache_data(ttl=3600, show_spinner="计算宏观相似度...")
def run_macro_analysis(_macro_hash: str):
    """宏观相似度分析（缓存 1 小时）"""
    macro_storage = get_macro_storage()
    macro_data = macro_storage.load_all()
    return compute_macro_analysis(macro_data)


def format_price(price: float, symbol: str) -> str:
    """格式化价格显示"""
    if symbol in CN_SYMBOLS or symbol in GLOBAL_SYMBOLS:
        return f"{price:,.2f}"
    return f"${price:,.2f}"


def format_change(change_pct: float) -> str:
    """格式化涨跌幅显示"""
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def get_change_color(change_pct: float) -> str:
    """获取涨跌幅颜色（A股习惯：涨红跌绿）"""
    return "#ef5350" if change_pct >= 0 else "#26a69a"


def create_sparkline(df: pd.DataFrame, color: str, symbol: str) -> go.Figure:
    """创建迷你走势图（可交互，带坐标轴）"""
    # 转换日期为字符串
    dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
    closes = df["close"].tolist()

    # 计算涨跌幅（以第一天为基准）
    base_price = closes[0]
    pct_changes = [(p / base_price - 1) * 100 for p in closes]

    # 格式化 hover 文本（显示涨跌幅 + 实际价格）
    if symbol in CN_SYMBOLS or symbol in GLOBAL_SYMBOLS:
        hovertemplate = "%{x}<br>涨跌幅: %{y:.2f}%<br>价格: %{customdata:,.2f}<extra></extra>"
    else:
        hovertemplate = "%{x}<br>涨跌幅: %{y:.2f}%<br>价格: $%{customdata:,.2f}<extra></extra>"

    # 计算 Y 轴范围（极值区间 + 5% padding）
    y_min = min(pct_changes)
    y_max = max(pct_changes)
    y_padding = (y_max - y_min) * 0.05

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=pct_changes,
            customdata=closes,
            mode="lines",
            line=dict(color=color, width=2),
            showlegend=False,
            hovertemplate=hovertemplate,
        )
    )

    fig.update_layout(
        height=240,
        margin=dict(l=40, r=10, t=5, b=25),
        xaxis=dict(
            visible=True,
            showgrid=False,
            showticklabels=True,
            tickformat="%m/%d",
            tickfont=dict(size=16, color="#999"),
        ),
        yaxis=dict(
            visible=True,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.05)",
            showticklabels=True,
            tickfont=dict(size=16, color="#999"),
            ticksuffix="%",
            range=[y_min - y_padding, y_max + y_padding],
            side="right",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x",
        hoverlabel=dict(
            bgcolor="white",
            font_size=10,
            font_color="#333",
            bordercolor="#e0e0e0",
        ),
    )

    return fig


def render_asset_card(
    title: str,
    price: float,
    change_pct: float,
    symbol: str,
    sparkline_df: pd.DataFrame = None,
):
    """渲染资产卡片（卡片 + 走势图）"""
    color = get_change_color(change_pct)
    formatted_price = format_price(price, symbol)
    formatted_change = format_change(change_pct)

    # 卡片
    st.markdown(
        f"""
        <div style="
            border-radius: 0.5rem;
            border: 1px solid #e0e0e0;
            background: white;
            padding: 0.8rem 1rem;
            text-align: center;
        ">
            <div style="font-size: 0.85rem; color: #666; margin-bottom: 0.3rem;">{title}</div>
            <div style="font-size: 1.3rem; font-weight: bold; margin-bottom: 0.15rem;">{formatted_price}</div>
            <div style="font-size: 0.9rem; color: {color}; font-weight: 500;">{formatted_change}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 走势图（卡片下方）
    if sparkline_df is not None and not sparkline_df.empty:
        fig = create_sparkline(sparkline_df, color, symbol)
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key=f"sparkline_{symbol}",
        )


def get_data_last_updated(dm: DataManager) -> str:
    """获取数据最后更新时间"""
    stats = dm.get_stats()
    if stats.empty:
        return "暂无数据"
    # 获取最新的更新时间
    last_dates = stats["last_date"].tolist()
    if last_dates:
        return max(last_dates)
    return "暂无数据"


@st.cache_data(ttl=300)
def get_asset_data_with_history(symbol: str, desc: str, days: int = 90) -> dict:
    """获取资产数据（含历史走势）— 5分钟缓存"""
    dm = get_data_manager()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = dm.load(symbol, start_date, end_date)
    if df.empty:
        return None

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    price = latest["close"]
    change_pct = ((price - prev["close"]) / prev["close"]) * 100

    return {
        "desc": desc,
        "price": price,
        "change_pct": change_pct,
        "history": df,
    }


def refresh_all_data(dm: DataManager, progress_bar) -> dict:
    """从 API 获取所有资产数据 + 宏观数据

    逻辑：检查每个 symbol 的最新日期，今日已有数据的跳过。
    Twelve Data 限速由 @rate_limit 装饰器处理，无需手动 sleep。
    """
    from datetime import date as _date

    results = {"us": {}, "cn": {}, "global": {}, "sectors": {}, "macro": {}}
    today_str = _date.today().strftime("%Y-%m-%d")

    # 1. 美股大类资产（Twelve Data，限速 8 次/分钟）
    us_symbols = list(US_SYMBOLS.keys())
    total_steps = len(us_symbols) + len(US_SECTORS_SYMBOLS) + 3  # +3: A股/亚太/宏观
    step = 0

    for i, symbol in enumerate(us_symbols):
        last_date = dm.storage.get_last_date(symbol)
        if last_date == today_str:
            results["us"][symbol] = 0  # 已是最新，跳过
            step += 1
            continue
        try:
            progress_bar.progress(
                step / total_steps, text=f"美股 {symbol} ({i+1}/{len(us_symbols)})..."
            )
            df = dm.twelvedata.get_time_series(symbol, outputsize=2000)
            count = dm.storage.save(df, symbol, "twelvedata")
            results["us"][symbol] = count
        except Exception as e:
            results["us"][symbol] = f"错误: {e}"
        step += 1

    # 2. 美股板块 ETF（Twelve Data）
    sector_symbols = list(US_SECTORS_SYMBOLS.keys())
    for i, symbol in enumerate(sector_symbols):
        last_date = dm.storage.get_last_date(symbol)
        if last_date == today_str:
            results["sectors"][symbol] = 0
            step += 1
            continue
        try:
            progress_bar.progress(
                step / total_steps,
                text=f"板块 {symbol} ({i+1}/{len(sector_symbols)})...",
            )
            df = dm.twelvedata.get_time_series(symbol, outputsize=2000)
            count = dm.storage.save(df, symbol, "twelvedata")
            results["sectors"][symbol] = count
        except Exception as e:
            results["sectors"][symbol] = f"错误: {e}"
        step += 1

    # 3. A股指数（AkShare，无严格限速）
    step += 1
    progress_bar.progress(step / total_steps, text="获取 A 股指数...")
    try:
        cn_results = dm.fetch_cn_assets()
        results["cn"] = cn_results
    except Exception as e:
        results["cn"] = {"error": str(e)}

    # 4. 全球指数（AkShare）
    step += 1
    progress_bar.progress(step / total_steps, text="获取亚太指数...")
    try:
        global_results = dm.fetch_global_assets()
        results["global"] = global_results
    except Exception as e:
        results["global"] = {"error": str(e)}

    # 5. 宏观数据（AkShare，无严格限速）
    step += 1
    progress_bar.progress(step / total_steps, text="获取宏观数据...")
    try:
        macro_fetcher = get_macro_fetcher()
        macro_data = macro_fetcher.fetch_all()
        macro_storage = get_macro_storage()
        macro_results = macro_storage.save_all(macro_data)
        results["macro"] = macro_results
        run_macro_analysis.clear()
    except Exception as e:
        results["macro"] = {"error": str(e)}

    progress_bar.progress(1.0, text="完成")
    return results


def _render_overview_strip(dm: DataManager):
    """页面顶部总览 — 结论先行：关键资产方向 + 热门行业。"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")

    key_assets = [
        ("SPY", "标普500"), ("QQQ", "纳指100"), ("000300", "沪深300"),
        ("399813", "半导体"), ("TLT", "国债"), ("GLD", "黄金"),
    ]

    st.subheader("  一眼总览")
    cols = st.columns(len(key_assets))
    for col, (symbol, label) in zip(cols, key_assets):
        df = dm.load(symbol, start_date, end_date)
        with col:
            if df.empty or len(df) < 2:
                st.caption(f"{label}: 暂无数据")
                continue
            close = df["close"]
            ret = float(close.iloc[-1] / close.iloc[0] - 1.0) * 100
            mom = calc_momentum_score(close)
            color = "#ef5350" if ret >= 0 else "#26a69a"
            st.markdown(
                f'<div style="text-align:center;padding:0.55rem;background:#fafafa;border-radius:0.4rem;">'
                f'<div style="font-size:0.8rem;color:#666;">{label}</div>'
                f'<div style="font-size:1.15rem;font-weight:700;color:{color};">{ret:+.1f}%</div>'
                f'<div style="font-size:0.75rem;color:#888;">4月 · 动量 {mom:.0f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # 行业冷热一句话
    heat_items = []
    for symbol, name in list(US_SECTORS_SYMBOLS.items()) + [
        ("399813", "半导体"), ("399986", "银行"), ("399989", "医疗"),
        ("000932", "消费"), ("399396", "有色"), ("399809", "煤炭"),
        ("399393", "地产"),
    ]:
        df = dm.load(symbol, start_date, end_date)
        if df.empty or len(df) < 30:
            continue
        heat_items.append((name, calc_momentum_score(df["close"])))

    if heat_items:
        heat_items.sort(key=lambda x: -x[1])
        hot = "、".join(n for n, _ in heat_items[:3])
        cold = "、".join(n for n, _ in heat_items[-2:])
        st.info(f"**行业冷热**：最热 {hot}　|　最弱 {cold}（按动量评分）")


def main():
    st.title("  市场全景")
    st.caption("大类资产实时行情与走势 · 先看总览，再看明细")

    dm = get_data_manager()
    macro_storage = get_macro_storage()

    # 如果首页自动刷新过宏观数据，清除缓存以加载最新
    if st.session_state.get("startup_refreshed") and not st.session_state.get("macro_cache_cleared"):
        st.session_state["macro_cache_cleared"] = True
        run_macro_analysis.clear()

    # 检查数据库是否有数据
    stats = dm.get_stats()
    db_empty = stats.empty

    # 数据库为空时自动触发获取（仅触发一次）
    if db_empty and "data_fetch_triggered" not in st.session_state:
        st.session_state["data_fetch_triggered"] = True
        st.info("首次使用，正在自动获取数据（美股限速，约 3 分钟）...")
        progress_bar = st.progress(0, text="开始获取...")
        try:
            results = refresh_all_data(dm, progress_bar)
            st.rerun()
        except Exception as e:
            st.error(f"自动获取失败: {e}")
            st.caption("请在侧边栏点击「一键获取所有数据」重试")

    # 侧边栏：数据控制
    with st.sidebar:
        st.subheader("数据控制")

        # 显示数据最后更新时间
        last_updated = get_data_last_updated(dm)
        st.caption(f"指数数据更新至: {last_updated}")
        if last_updated != "暂无数据":
            idx_days = (datetime.now() - datetime.strptime(last_updated, "%Y-%m-%d")).days
            if idx_days >= 7:
                st.error(f"指数数据已 {idx_days} 天未更新")
            elif idx_days >= 3:
                st.warning(f"指数数据已 {idx_days} 天未更新")

        macro_stats_df = macro_storage.get_stats()
        if not macro_stats_df.empty:
            last_macro_date = macro_stats_df["last_date"].max()
            st.caption(f"宏观数据更新至: {last_macro_date}")
            macro_days = (datetime.now() - datetime.strptime(last_macro_date, "%Y-%m-%d")).days
            if macro_days >= 90:
                st.error(f"宏观数据已 {macro_days} 天未更新（月度数据正常滞后 30-60 天）")
            elif macro_days >= 60:
                st.warning(f"宏观数据已 {macro_days} 天未更新")

        # 一键获取按钮（指数 + 宏观）
        if st.button("  一键获取所有数据", use_container_width=True, type="primary"):
            progress_bar = st.progress(0, text="开始获取...")

            results = refresh_all_data(dm, progress_bar)

            # 统计结果（>0 = 新获取，0 = 已跳过，str = 错误）
            us_ok = sum(1 for v in results["us"].values() if isinstance(v, int) and v > 0)
            us_skip = sum(1 for v in results["us"].values() if v == 0)
            sectors_ok = sum(1 for v in results["sectors"].values() if isinstance(v, int) and v > 0)
            sectors_skip = sum(1 for v in results["sectors"].values() if v == 0)
            cn_ok = sum(1 for v in results.get("cn", {}).values() if isinstance(v, int) and v > 0)
            global_ok = sum(1 for v in results.get("global", {}).values() if isinstance(v, int) and v > 0)
            macro_ok = sum(1 for v in results.get("macro", {}).values() if isinstance(v, int) and v > 0)

            total_new = us_ok + sectors_ok + cn_ok + global_ok + macro_ok
            total_skip = us_skip + sectors_skip
            if total_new == 0 and total_skip > 0:
                st.info(f"所有数据已是最新，跳过 {total_skip} 个 symbol")
            else:
                parts = [f"美股 {us_ok}/{len(US_SYMBOLS)}"]
                if us_skip:
                    parts[0] += f" (跳过{us_skip})"
                parts.append(f"板块 {sectors_ok}/{len(US_SECTORS_SYMBOLS)}")
                if sectors_skip:
                    parts[-1] += f" (跳过{sectors_skip})"
                parts.extend([
                    f"A股 {cn_ok}/{len(CN_SYMBOLS)}",
                    f"亚太 {global_ok}/{len(GLOBAL_SYMBOLS)}",
                    f"宏观 {macro_ok}/5",
                ])
                st.success(" | ".join(parts))
            st.rerun()

        st.divider()
        st.caption(f"页面加载时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ============================================================
    # 一眼总览（结论）
    # ============================================================
    if not db_empty:
        _render_overview_strip(dm)
        st.divider()

    # ============================================================
    # 宏观观测
    # ============================================================
    st.subheader("  宏观观测", help=MACRO_SIMILARITY)
    st.caption("通过对比当前宏观环境与历史相似时期，预判后续市场走势")

    if not macro_storage.has_data():
        st.info("暂无宏观数据，请点击侧边栏「一键获取所有数据」")
    else:
        try:
            macro_stats = macro_storage.get_stats()
            macro_hash = str(macro_stats["last_date"].max())

            analysis = run_macro_analysis(macro_hash)

            if "error" in analysis:
                st.warning(analysis["error"])
            else:
                # 1. 当前宏观指标值卡片
                render_macro_value_cards(analysis)

                # 2. 散点图 + 相似月份表格（左右布局）
                col_chart, col_table = st.columns([3, 2])

                with col_chart:
                    indicator_options = list(MACRO_INDICATORS.keys())
                    col_x, col_y = st.columns(2)
                    with col_x:
                        x_ind = st.selectbox(
                            "X 轴",
                            options=indicator_options,
                            format_func=lambda x: MACRO_INDICATORS[x]["name"],
                            index=indicator_options.index("cpi_yoy"),
                            key="macro_scatter_x",
                        )
                    with col_y:
                        y_ind = st.selectbox(
                            "Y 轴",
                            options=indicator_options,
                            format_func=lambda x: MACRO_INDICATORS[x]["name"],
                            index=indicator_options.index("treasury_10y"),
                            key="macro_scatter_y",
                        )

                    fig = create_macro_indicator_scatter(
                        analysis["matrix"],
                        analysis["current_month"],
                        analysis["similar"],
                        x_indicator=x_ind,
                        y_indicator=y_ind,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                with col_table:
                    st.markdown("**历史相似时期**")
                    similar = analysis["similar"]
                    forward = analysis["forward_returns"]

                    if not similar.empty and not forward.empty:
                        merged = similar.merge(forward, on="month", how="left")
                        display_df = merged[["rank", "month", "distance"]].copy()
                        display_df["月份"] = display_df["month"].dt.strftime("%Y-%m")
                        # 距离转相似度百分比（距离0=100%，距离100+=0%）
                        display_df["相似度"] = display_df["distance"].apply(
                            lambda x: f"{max(0, 100 - x):.0f}%"
                        )

                        for col in ["spy_6m", "spy_12m"]:
                            if col in merged.columns:
                                display_df[col] = merged[col].apply(
                                    lambda x: f"+{x:.1f}%" if x and x > 0 else f"{x:.1f}%" if x else "N/A"
                                )

                        display_df = display_df.rename(columns={
                            "rank": "#",
                            "spy_6m": "SPY 6M",
                            "spy_12m": "SPY 12M",
                        })
                        display_df = display_df[["#", "月份", "相似度", "SPY 6M", "SPY 12M"]]
                        st.dataframe(display_df, use_container_width=True, hide_index=True,
                                     column_config={
                                         "相似度": st.column_config.TextColumn(help=MACRO_SIMILARITY),
                                         "SPY 6M": st.column_config.TextColumn(help="相似月份后 6 个月的 SPY 实际收益率"),
                                         "SPY 12M": st.column_config.TextColumn(help="相似月份后 12 个月的 SPY 实际收益率"),
                                     })

                        # 结论摘要
                        avg_spy_6m = forward["spy_6m"].dropna().mean()
                        avg_spy_12m = forward["spy_12m"].dropna().mean()
                        if not np.isnan(avg_spy_6m):
                            if avg_spy_6m > 5:
                                outlook = "偏乐观"
                            elif avg_spy_6m > 0:
                                outlook = "中性偏多"
                            elif avg_spy_6m > -5:
                                outlook = "中性偏空"
                            else:
                                outlook = "偏悲观"
                            st.info(
                                f"**宏观展望: {outlook}** — "
                                f"SPY 6M 均值 {avg_spy_6m:+.1f}%，12M 均值 {avg_spy_12m:+.1f}%"
                            )
                            st.caption(f"  {MACRO_OUTLOOK}")

                # 3. 百分位走势图
                ranked = analysis["ranked"]
                time_min = ranked.index.min().date()
                time_max = ranked.index.max().date()

                # 预设时间区间快捷按钮
                now = pd.Timestamp.now()
                range_options = {
                    "全部": (time_min, time_max),
                    "近 10 年": ((now - pd.DateOffset(years=10)).date(), time_max),
                    "近 20 年": ((now - pd.DateOffset(years=20)).date(), time_max),
                    "2000 至今": (pd.Timestamp("2000-01-01").date(), time_max),
                    "1990-2010": (pd.Timestamp("1990-01-01").date(), pd.Timestamp("2010-12-31").date()),
                }
                range_col1, range_col2 = st.columns([1, 3])
                with range_col1:
                    range_key = st.selectbox(
                        "时间区间",
                        options=list(range_options.keys()),
                        index=0,
                        key="macro_time_range",
                    )
                date_start, date_end = range_options[range_key]

                # 过滤数据到选定区间
                ranked_filtered = ranked.loc[str(date_start):str(date_end)]
                spy_m = analysis.get("spy_monthly")
                qqq_m = analysis.get("qqq_monthly")
                spy_filtered = spy_m.loc[str(date_start):str(date_end)] if spy_m is not None and not spy_m.empty else None
                qqq_filtered = qqq_m.loc[str(date_start):str(date_end)] if qqq_m is not None and not qqq_m.empty else None

                # 过滤相似月份
                similar = analysis["similar"]
                similar_filtered = similar[
                    (similar["month"].dt.date >= date_start) &
                    (similar["month"].dt.date <= date_end)
                ] if not similar.empty else similar

                fig_timeline = create_macro_timeline(
                    ranked_filtered,
                    analysis["current_month"],
                    similar_filtered,
                    spy_monthly=spy_filtered,
                    qqq_monthly=qqq_filtered,
                )
                st.plotly_chart(fig_timeline, use_container_width=True)

        except Exception as e:
            st.error(f"宏观分析失败: {e}")

    st.divider()

    # ============================================================
    # 美股大类资产
    # ============================================================
    st.subheader("  美股大类资产")

    if db_empty:
        st.info("首次使用请点击侧边栏「一键获取所有数据」，约 3 分钟完成")
    else:
        try:
            cols = st.columns(len(US_SYMBOLS))
            for col, (symbol, desc) in zip(cols, US_SYMBOLS.items()):
                with col:
                    data = get_asset_data_with_history(symbol, desc, days=90)
                    if data:
                        render_asset_card(
                            data["desc"],
                            data["price"],
                            data["change_pct"],
                            symbol,
                            sparkline_df=data["history"],
                        )
                    else:
                        st.warning(f"{desc}: 暂无数据")

        except Exception as e:
            st.error(f"美股数据获取失败: {e}")

    st.divider()

    # ============================================================
    # A股：宽基 + 行业
    # ============================================================
    st.subheader("  A股宽基指数")
    cn_broad = ["399006", "000688", "000300"]
    try:
        cols = st.columns(len(cn_broad))
        for col, symbol in zip(cols, cn_broad):
            name = CN_SYMBOLS.get(symbol, symbol)
            with col:
                data = get_asset_data_with_history(symbol, name, days=90)
                if data:
                    render_asset_card(
                        data["desc"],
                        data["price"],
                        data["change_pct"],
                        symbol,
                        sparkline_df=data["history"],
                    )
                else:
                    st.warning(f"{name}: 暂无数据")
    except Exception as e:
        st.error(f"A股数据获取失败: {e}")

    st.subheader("  A股行业指数")
    cn_industry = [
        "399813", "399986", "399989", "000932",
        "399396", "399809", "399393",
    ]
    try:
        cols = st.columns(len(cn_industry))
        for col, symbol in zip(cols, cn_industry):
            name = CN_SYMBOLS.get(symbol, symbol)
            with col:
                data = get_asset_data_with_history(symbol, name, days=90)
                if data:
                    render_asset_card(
                        data["desc"],
                        data["price"],
                        data["change_pct"],
                        symbol,
                        sparkline_df=data["history"],
                    )
                else:
                    st.warning(f"{name}: 暂无数据")
    except Exception as e:
        st.error(f"A股行业数据获取失败: {e}")

    st.divider()

    # ============================================================
    # 亚太指数
    # ============================================================
    st.subheader("  亚太指数")

    try:
        cols = st.columns(len(GLOBAL_SYMBOLS))
        for col, (symbol, name) in zip(cols, GLOBAL_SYMBOLS.items()):
            with col:
                data = get_asset_data_with_history(symbol, name, days=90)
                if data:
                    render_asset_card(
                        data["desc"],
                        data["price"],
                        data["change_pct"],
                        symbol,
                        sparkline_df=data["history"],
                    )
                else:
                    st.warning(f"{name}: 暂无数据")

    except Exception as e:
        st.error(f"亚太指数数据获取失败: {e}")

    st.divider()

    # ============================================================
    # 行业热度（美股板块 + A股行业）
    # ============================================================
    st.subheader("  行业热度", help="美股板块 ETF + A股行业指数，按动量评分排序")

    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")

        heat_universe = [
            *[(s, f"美·{n}") for s, n in US_SECTORS_SYMBOLS.items()],
            ("399813", "A·半导体"), ("399986", "A·银行"), ("399989", "A·医疗"),
            ("000932", "A·消费"), ("399396", "A·有色"), ("399809", "A·煤炭"),
            ("399393", "A·地产"),
        ]
        sector_data = []
        for symbol, name in heat_universe:
            df = dm.load(symbol, start_date, end_date)
            if not df.empty:
                score = calc_momentum_score(df["close"])
                returns = calc_returns(df["close"], [21, 63])
                ret_dict = returns.to_dict("records")[0]
                sector_data.append({
                    "板块": name,
                    "代码": symbol,
                    "1月%": ret_dict.get("1M"),
                    "3月%": ret_dict.get("3M"),
                    "动量评分": score,
                })

        if sector_data:
            sector_df = pd.DataFrame(sector_data)
            sector_df = sector_df.sort_values("动量评分", ascending=False, na_position="last")
            sector_df = sector_df.reset_index(drop=True)
            sector_df.index = sector_df.index + 1
            sector_df.index.name = "排名"

            display_sector = sector_df.copy()
            for col in ["1月%", "3月%"]:
                display_sector[col] = display_sector[col].apply(
                    lambda x: f"+{x:.1f}%" if x and x > 0 else f"{x:.1f}%" if x else "N/A"
                )
            display_sector["动量评分"] = display_sector["动量评分"].apply(
                lambda x: f"{x:.0f}" if x else "N/A"
            )

            st.dataframe(display_sector, use_container_width=True,
                         column_config={
                             "动量评分": st.column_config.NumberColumn(help=MOMENTUM_SCORE),
                             "1月%": st.column_config.TextColumn(help=RETURN_1M),
                             "3月%": st.column_config.TextColumn(help=RETURN_3M),
                         })

            top_sectors = sector_df.head(3)
            hot_names = "、".join(top_sectors["板块"].tolist())
            st.info(f"近 3 个月热门板块：{hot_names}")
        else:
            st.warning("暂无板块数据")

    except Exception as e:
        st.error(f"板块数据获取失败: {e}")

    st.divider()

    # ============================================================
    # VIX 恐慌指数 + 事件日历
    # ============================================================
    col_vix, col_calendar = st.columns([1, 2])

    with col_vix:
        st.subheader("  VIX 恐慌指数", help=VIX)
        try:
            macro_storage = get_macro_storage()
            vix_df = macro_storage.load("vix")
            if not vix_df.empty:
                vix_value = vix_df.iloc[-1]["value"]
                vix_date = vix_df.iloc[-1]["date"]

                if vix_value > 25:
                    vix_color = "#d32f2f"
                    vix_label = "恐慌"
                    vix_bg = "#ffebee"
                elif vix_value > 20:
                    vix_color = "#e65100"
                    vix_label = "警惕"
                    vix_bg = "#fff3e0"
                elif vix_value > 15:
                    vix_color = "#f9a825"
                    vix_label = "正常"
                    vix_bg = "#fffde7"
                else:
                    vix_color = "#2e7d32"
                    vix_label = "平静"
                    vix_bg = "#e8f5e9"

                # 5 日变化
                vix_change = ""
                if len(vix_df) >= 5:
                    vix_5d = vix_df.iloc[-5]["value"]
                    if vix_5d > 0:
                        change = (vix_value - vix_5d) / vix_5d * 100
                        vix_change = f"5日 {change:+.0f}%"

                st.markdown(
                    f'<div style="padding:1rem;background:{vix_bg};border-radius:0.5rem;'
                    f'border-left:5px solid {vix_color};text-align:center;">'
                    f'<div style="font-size:2.5rem;font-weight:bold;color:{vix_color};">{vix_value:.1f}</div>'
                    f'<div style="font-size:1.1rem;color:{vix_color};font-weight:600;">{vix_label}</div>'
                    f'<div style="color:#666;font-size:0.85rem;">{vix_date} {vix_change}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("暂无 VIX 数据")
        except Exception as e:
            st.warning(f"VIX 数据加载失败: {e}")

    with col_calendar:
        st.subheader("  未来 14 天风险日历")
        try:
            from core.data.event_calendar import get_upcoming_events, get_current_risk_level

            level, reasons = get_current_risk_level()
            events = get_upcoming_events(days=14)

            # 当前风险等级
            if level == "high":
                st.error(f"当前风险等级：**高** — 未来 3 天内有重要事件")
            elif level == "medium":
                st.warning(f"当前风险等级：**中** — 未来 7 天内有事件")
            else:
                st.success("当前风险等级：**低**")

            if events:
                event_data = []
                for e in events:
                    days_until = (datetime.strptime(e.date, "%Y-%m-%d").date() - datetime.now().date()).days
                    event_data.append({
                        "日期": e.date,
                        "距今": f"{days_until}天" if days_until > 0 else "今天",
                        "事件": e.name,
                        "风险": "  高" if e.risk_level == "high" else "  中",
                        "类别": e.category,
                    })
                st.dataframe(pd.DataFrame(event_data), use_container_width=True, hide_index=True)
            else:
                st.info("未来 14 天无重要事件")
        except Exception as e:
            st.warning(f"事件日历加载失败: {e}")

    st.divider()

    # ============================================================
    # 持仓关注（风险仪表盘）
    # ============================================================
    st.subheader("  持仓关注")
    st.caption("关注的板块和基金实时状态 + 风险仪表盘")

    try:
        from core.data.config import WATCHED_HOLDINGS
        from core.scanner.sector_alert import detect_sector_alerts

        # 加载关注资产数据
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        # 收集所有持仓数据用于相关性计算
        holdings_close = {}  # {symbol: pd.Series}
        holdings_meta = {}   # {symbol: {info, df, ...}}

        for symbol, info in WATCHED_HOLDINGS.items():
            df = dm.load(symbol, start_date, end_date)
            if not df.empty:
                holdings_close[symbol] = df["close"].reset_index(drop=True)
                holdings_meta[symbol] = {"info": info, "df": df}

        # ---- 第一行：各持仓卡片 ----
        if holdings_meta:
            watch_cols = st.columns(len(holdings_meta))
            for i, (symbol, meta) in enumerate(holdings_meta.items()):
                with watch_cols[i]:
                    info = meta["info"]
                    df = meta["df"]
                    close = df["close"]
                    latest_price = close.iloc[-1]
                    prev_price = close.iloc[-2] if len(close) >= 2 else latest_price
                    change_pct = (latest_price / prev_price - 1) * 100

                    # 7 日收益
                    ret_7d = (close.iloc[-1] / close.iloc[-7] - 1) * 100 if len(close) >= 7 else 0

                    # 动量评分
                    mom = calc_momentum_score(close)
                    mom_str = f"{mom:.0f}" if mom else "N/A"

                    # 波动率
                    vol = calc_volatility(close) if len(close) >= 20 else None

                    # 当前回撤（从历史最高点）
                    peak = close.expanding().max()
                    drawdown = (close.iloc[-1] / peak.iloc[-1] - 1) * 100

                    # 距离止损线（-15%）的距离
                    stop_loss = -15.0
                    distance_to_stop = drawdown - stop_loss  # 正数=安全，负数=已突破

                    if info["type"] == "fund":
                        st.metric(
                            label=info["name"],
                            value=f"{latest_price:.4f}",
                            delta=f"{change_pct:+.2f}%",
                        )
                    else:
                        st.metric(
                            label=info["name"],
                            value=f"${latest_price:.2f}",
                            delta=f"{change_pct:+.2f}%",
                        )

                    # 回撤与止损线
                    if distance_to_stop < 0:
                        dd_color = "#d32f2f"
                        dd_label = "已突破止损!"
                    elif distance_to_stop < 3:
                        dd_color = "#e65100"
                        dd_label = "接近止损"
                    elif distance_to_stop < 6:
                        dd_color = "#f9a825"
                        dd_label = "需关注"
                    else:
                        dd_color = "#2e7d32"
                        dd_label = "安全"

                    st.markdown(
                        f'<div style="padding:0.3rem 0.5rem;background:#f8f9fa;border-radius:0.3rem;'
                        f'border-left:3px solid {dd_color};margin:0.3rem 0;">'
                        f'<span style="font-size:0.8rem;color:#555;">回撤: </span>'
                        f'<span style="font-weight:600;color:{dd_color};">{drawdown:.1f}%</span>'
                        f'<span style="font-size:0.8rem;color:#888;"> / 止损 {stop_loss:.0f}%</span>'
                        f'<span style="font-size:0.75rem;color:{dd_color};margin-left:0.5rem;">{dd_label}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    vol_str = f"{vol:.0f}%" if vol else "N/A"
                    st.caption(f"7日: {ret_7d:+.1f}% | 动量: {mom_str} | 年化波动: {vol_str}")

        # ---- 第二行：相关性热力图 + 波动率状态 ----
        if len(holdings_close) >= 2:
            col_corr, col_vol_regime = st.columns([3, 2])

            with col_corr:
                st.markdown("**  持仓相关性热力图**", help=CORRELATION)
                st.caption("相关性 > 0.8 = 本质上是同一笔赌注")

                # 构建收益率 DataFrame
                returns_df = pd.DataFrame()
                name_map = {}
                for sym, s in holdings_close.items():
                    name = WATCHED_HOLDINGS[sym]["name"]
                    name_map[sym] = name
                    returns_df[name] = s.pct_change().dropna()

                corr_matrix = returns_df.corr()

                # Plotly 热力图
                labels = [name_map.get(s, s) for s in holdings_close.keys()]
                fig_corr = go.Figure(data=go.Heatmap(
                    z=corr_matrix.values,
                    x=corr_matrix.columns.tolist(),
                    y=corr_matrix.index.tolist(),
                    colorscale=[
                        [0, "#26a69a"],
                        [0.5, "#fff9c4"],
                        [0.8, "#ff9800"],
                        [1, "#d32f2f"],
                    ],
                    zmin=0, zmax=1,
                    text=[[f"{v:.2f}" for v in row] for row in corr_matrix.values],
                    texttemplate="%{text}",
                    textfont=dict(size=14),
                    hovertemplate="%{x} vs %{y}<br>相关系数: %{z:.3f}<extra></extra>",
                ))
                fig_corr.update_layout(
                    height=300,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(tickfont=dict(size=12)),
                    yaxis=dict(tickfont=dict(size=12), autorange="reversed"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_corr, use_container_width=True, config={"displayModeBar": False})

                # 相关性警告
                high_corr_pairs = []
                cols_list = corr_matrix.columns.tolist()
                for ii in range(len(cols_list)):
                    for jj in range(ii + 1, len(cols_list)):
                        c = corr_matrix.iloc[ii, jj]
                        if c > 0.8:
                            high_corr_pairs.append(f"{cols_list[ii]} ↔ {cols_list[jj]}: {c:.2f}")
                if high_corr_pairs:
                    st.warning("高相关性持仓（分散化不足）:\n" + "\n".join(f"- {p}" for p in high_corr_pairs))

            with col_vol_regime:
                st.markdown("**  波动率状态**", help=VOLATILITY)
                st.caption("当前波动率 vs 历史中位数")

                for sym, meta in holdings_meta.items():
                    info = meta["info"]
                    close = meta["df"]["close"]
                    name = info["name"]

                    if len(close) < 60:
                        continue

                    # 当前 20 日波动率
                    daily_ret = close.pct_change().dropna()
                    vol_20d = daily_ret.tail(20).std() * np.sqrt(252) * 100

                    # 历史 60 日中位数波动率（滚动 20 日窗口）
                    rolling_vol = daily_ret.rolling(20).std() * np.sqrt(252) * 100
                    vol_median = rolling_vol.tail(60).median()

                    if vol_median > 0:
                        vol_ratio = vol_20d / vol_median
                    else:
                        vol_ratio = 1.0

                    # 分类
                    if vol_ratio > 1.8:
                        regime = "极端"
                        regime_color = "#d32f2f"
                        regime_bg = "#ffebee"
                    elif vol_ratio > 1.3:
                        regime = "偏高"
                        regime_color = "#e65100"
                        regime_bg = "#fff3e0"
                    elif vol_ratio < 0.7:
                        regime = "偏低"
                        regime_color = "#1565c0"
                        regime_bg = "#e3f2fd"
                    else:
                        regime = "正常"
                        regime_color = "#2e7d32"
                        regime_bg = "#e8f5e9"

                    short_name = name[:6] if len(name) > 6 else name
                    st.markdown(
                        f'<div style="padding:0.4rem 0.6rem;background:{regime_bg};border-radius:0.3rem;'
                        f'margin-bottom:0.3rem;display:flex;justify-content:space-between;align-items:center;">'
                        f'<span style="font-size:0.85rem;color:#333;">{short_name}</span>'
                        f'<span style="font-weight:600;color:{regime_color};">{regime} ({vol_20d:.0f}%)</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # ---- 第三行：叙事冲击检测 ----
        st.markdown("**  叙事冲击检测**", help=NARRATIVE_SHOCK)
        st.caption("5 日跌幅 > 历史日均跌幅 2 倍 = 可能存在叙事冲击（非基本面变化）")

        shock_found = False
        for sym, meta in holdings_meta.items():
            close = meta["df"]["close"]
            name = meta["info"]["name"]

            if len(close) < 60:
                continue

            daily_ret = close.pct_change().dropna()

            # 历史平均日跌幅（只计下跌日）
            down_days = daily_ret[daily_ret < 0]
            if len(down_days) < 10:
                continue
            avg_daily_drop = down_days.mean()  # 负数

            # 最近 5 日跌幅
            ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0

            # 叙事冲击判定：5 日跌幅 > 2 倍历史日均跌幅（绝对值）
            if ret_5d < 0 and abs(ret_5d) > abs(avg_daily_drop) * 2:
                shock_found = True
                severity = "critical" if abs(ret_5d) > abs(avg_daily_drop) * 3 else "warning"
                icon = " " if severity == "critical" else " "
                color = "#d32f2f" if severity == "critical" else "#e65100"

                st.markdown(
                    f'<div style="padding:0.5rem 0.8rem;background:#fff3e0;border-left:4px solid {color};'
                    f'border-radius:0 0.3rem 0.3rem 0;margin-bottom:0.3rem;">'
                    f'{icon} <span style="font-weight:600;color:{color};">{name}</span>'
                    f'<span style="color:#555;"> — 5日跌幅 {ret_5d*100:+.1f}%，'
                    f'历史日均跌幅 {avg_daily_drop*100:.1f}%，'
                    f'偏离 {abs(ret_5d/avg_daily_drop):.1f}x</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if not shock_found:
            st.success("当前无叙事冲击信号")

        # ---- 板块异常检测 ----
        sector_data = {}
        for symbol in US_SECTORS_SYMBOLS:
            df = dm.load(symbol, start_date, end_date)
            if not df.empty:
                sector_data[symbol] = df

        spy_df = dm.load("SPY", start_date, end_date)
        if sector_data and not spy_df.empty:
            alerts = detect_sector_alerts(sector_data, spy_df)
            if alerts:
                st.markdown("**  近期板块异常信号：**")
                for a in alerts[:5]:
                    icon = " " if a.severity == "critical" else " "
                    st.markdown(f"- {icon} {a.message}")
            else:
                st.success("板块无异常信号")

    except Exception as e:
        st.warning(f"持仓关注加载失败: {e}")

    st.divider()

    # ============================================================
    # 走势图表
    # ============================================================
    st.subheader("  走势图表")

    symbol_options = list(US_SYMBOLS.keys()) + list(CN_SYMBOLS.keys()) + list(GLOBAL_SYMBOLS.keys())
    symbol_labels = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    selected_symbols = st.multiselect(
        "选择资产",
        options=symbol_options,
        default=["SPY", "QQQ"],
        format_func=lambda x: f"{symbol_labels.get(x, x)} ({x})",
    )

    time_range = st.selectbox(
        "时间范围",
        options=["1周", "1月", "3月", "6月", "1年"],
        index=2,
    )

    days_map = {"1周": 7, "1月": 30, "3月": 90, "6月": 180, "1年": 365}
    days = days_map[time_range]
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    chart_data = {}
    for symbol in selected_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            chart_data[symbol] = df

    if chart_data:
        fig = create_multi_line_chart(chart_data, title="资产走势对比")
        st.plotly_chart(fig, use_container_width=True)

        if len(chart_data) == 1:
            symbol = list(chart_data.keys())[0]
            df = chart_data[symbol]
            if symbol in ["SPY", "QQQ"]:
                fig = create_candlestick_chart(df, symbol)
            else:
                fig = create_line_chart(df, symbol)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("请选择资产并确保数据已加载")


if __name__ == "__main__":
    main()
