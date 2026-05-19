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
from core.strategy.indicators import calc_momentum_score, calc_returns
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


def get_asset_data_with_history(dm: DataManager, symbol: str, desc: str, days: int = 90) -> dict:
    """获取资产数据（含历史走势）"""
    # 计算日期范围
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
    """从 API 获取所有资产数据 + 宏观数据"""
    import time

    results = {"us": {}, "cn": {}, "global": {}, "sectors": {}, "macro": {}}

    # 1. 美股大类资产（Twelve Data，限速 8 次/分钟）
    us_symbols = list(US_SYMBOLS.keys())
    total_steps = len(us_symbols) + len(US_SECTORS_SYMBOLS) + 3  # +3: A股/亚太/宏观
    step = 0

    for i, symbol in enumerate(us_symbols):
        try:
            progress_bar.progress(
                step / total_steps, text=f"美股 {symbol} ({i+1}/{len(us_symbols)})..."
            )
            df = dm.twelvedata.get_time_series(symbol, outputsize=2000)
            count = dm.storage.save(df, symbol, "twelvedata")
            results["us"][symbol] = count
            if i < len(us_symbols) - 1:
                time.sleep(8)
        except Exception as e:
            results["us"][symbol] = f"错误: {e}"
        step += 1

    # 2. 美股板块 ETF（Twelve Data）
    sector_symbols = list(US_SECTORS_SYMBOLS.keys())
    for i, symbol in enumerate(sector_symbols):
        try:
            progress_bar.progress(
                step / total_steps,
                text=f"板块 {symbol} ({i+1}/{len(sector_symbols)})...",
            )
            df = dm.twelvedata.get_time_series(symbol, outputsize=2000)
            count = dm.storage.save(df, symbol, "twelvedata")
            results["sectors"][symbol] = count
            if i < len(sector_symbols) - 1:
                time.sleep(8)
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


def main():
    st.title("  市场全景")
    st.caption("大类资产实时行情与走势")

    dm = get_data_manager()
    macro_storage = get_macro_storage()

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

        macro_stats_df = macro_storage.get_stats()
        if not macro_stats_df.empty:
            last_macro_date = macro_stats_df["last_date"].max()
            st.caption(f"宏观数据更新至: {last_macro_date}")

        # 一键获取按钮（指数 + 宏观）
        if st.button("  一键获取所有数据", use_container_width=True, type="primary"):
            st.warning("首次获取需要约 3 分钟（美股限速），请耐心等待...")
            progress_bar = st.progress(0, text="开始获取...")

            results = refresh_all_data(dm, progress_bar)

            # 统计结果
            us_ok = sum(1 for v in results["us"].values() if isinstance(v, int) and v > 0)
            sectors_ok = sum(1 for v in results["sectors"].values() if isinstance(v, int) and v > 0)
            cn_ok = sum(1 for v in results.get("cn", {}).values() if isinstance(v, int) and v > 0)
            global_ok = sum(1 for v in results.get("global", {}).values() if isinstance(v, int) and v > 0)
            macro_ok = sum(1 for v in results.get("macro", {}).values() if isinstance(v, int) and v > 0)

            st.success(
                f"美股 {us_ok}/{len(US_SYMBOLS)} | "
                f"板块 {sectors_ok}/{len(US_SECTORS_SYMBOLS)} | "
                f"A股 {cn_ok}/{len(CN_SYMBOLS)} | "
                f"亚太 {global_ok}/{len(GLOBAL_SYMBOLS)} | "
                f"宏观 {macro_ok}/5"
            )
            st.rerun()

        st.divider()
        st.caption(f"页面加载时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ============================================================
    # 宏观观测（顶部）
    # ============================================================
    st.subheader("  宏观观测")
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
                        display_df["相似度"] = display_df["distance"].apply(lambda x: f"{x:.2f}")

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
                        st.dataframe(display_df, use_container_width=True, hide_index=True)

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
                    data = get_asset_data_with_history(dm, symbol, desc, days=90)
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
    # A股主要指数
    # ============================================================
    st.subheader("  A股主要指数")

    try:
        cols = st.columns(len(CN_SYMBOLS))
        for col, (symbol, name) in zip(cols, CN_SYMBOLS.items()):
            with col:
                data = get_asset_data_with_history(dm, symbol, name, days=90)
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

    st.divider()

    # ============================================================
    # 亚太指数
    # ============================================================
    st.subheader("  亚太指数")

    try:
        cols = st.columns(len(GLOBAL_SYMBOLS))
        for col, (symbol, name) in zip(cols, GLOBAL_SYMBOLS.items()):
            with col:
                data = get_asset_data_with_history(dm, symbol, name, days=90)
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
    # 美股板块热度
    # ============================================================
    st.subheader("  美股板块热度")

    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")

        sector_data = []
        for symbol, name in US_SECTORS_SYMBOLS.items():
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

            st.dataframe(display_sector, use_container_width=True)

            top_sectors = sector_df.head(3)
            hot_names = "、".join(top_sectors["板块"].tolist())
            st.info(f"近 3 个月热门板块：{hot_names}")
        else:
            st.warning("暂无板块数据")

    except Exception as e:
        st.error(f"板块数据获取失败: {e}")

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
