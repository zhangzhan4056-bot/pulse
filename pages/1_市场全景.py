"""P1: 市场全景（盯盘）

实时查看美股和A股大类资产行情
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from app.components.charts import (
    create_candlestick_chart,
    create_line_chart,
    create_multi_line_chart,
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

    # 格式化 hover 文本
    if symbol in CN_SYMBOLS or symbol in GLOBAL_SYMBOLS:
        hovertemplate = "%{x}<br>%{y:,.2f}<extra></extra>"
    else:
        hovertemplate = "%{x}<br>$%{y:,.2f}<extra></extra>"

    # 计算 Y 轴范围（极值区间 + 5% padding）
    y_min = min(closes)
    y_max = max(closes)
    y_padding = (y_max - y_min) * 0.05

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=closes,
            mode="lines",
            line=dict(color=color, width=2),
            showlegend=False,
            hovertemplate=hovertemplate,
        )
    )

    fig.update_layout(
        height=120,
        margin=dict(l=40, r=10, t=5, b=25),
        xaxis=dict(
            visible=True,
            showgrid=False,
            showticklabels=True,
            tickformat="%m/%d",
            tickfont=dict(size=8, color="#999"),
        ),
        yaxis=dict(
            visible=True,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.05)",
            showticklabels=True,
            tickfont=dict(size=8, color="#999"),
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


def refresh_us_data(dm: DataManager, progress_bar) -> dict:
    """从 API 获取最新美股数据（近1年）"""
    import time

    results = {}
    symbols = list(US_SYMBOLS.keys())

    for i, symbol in enumerate(symbols):
        try:
            # 更新进度
            progress_bar.progress((i) / len(symbols), text=f"获取 {symbol}...")

            # 获取最新价格
            price = dm.twelvedata.get_latest_price(symbol)

            # 获取近1年历史数据（约250个交易日）
            df = dm.twelvedata.get_time_series(symbol, outputsize=250)
            count = dm.storage.save(df, symbol, "twelvedata")

            results[symbol] = {"price": price, "count": count}

            # 等待 8 秒（确保不超过每分钟 8 次限制）
            if i < len(symbols) - 1:
                time.sleep(8)

        except Exception as e:
            results[symbol] = {"error": str(e)}

    progress_bar.progress(1.0, text="完成")
    return results


def main():
    st.title("  市场全景")
    st.caption("大类资产实时行情与走势")

    dm = get_data_manager()

    # 侧边栏：数据控制
    with st.sidebar:
        st.subheader("数据控制")

        # 显示数据最后更新时间
        last_updated = get_data_last_updated(dm)
        st.caption(f"数据更新至: {last_updated}")

        # 刷新按钮
        if st.button("  刷新美股数据", use_container_width=True):
            st.warning("正在获取近1年历史数据，请等待约 40 秒...")
            progress_bar = st.progress(0, text="开始获取...")

            results = refresh_us_data(dm, progress_bar)

            # 显示结果
            for symbol, result in results.items():
                if "error" in result:
                    st.error(f"{symbol}: {result['error']}")
                else:
                    st.success(f"{symbol}: ${result['price']:.2f} (写入 {result['count']} 条)")

            st.rerun()

        st.divider()
        st.caption(f"页面加载时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ============================================================
    # 美股大类资产
    # ============================================================
    st.subheader("  美股大类资产")

    try:
        # 从数据库获取美股数据（含3个月走势）
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
                    st.caption("点击侧边栏「刷新美股数据」获取")

    except Exception as e:
        st.error(f"美股数据获取失败: {e}")

    st.divider()

    # ============================================================
    # A股主要指数
    # ============================================================
    st.subheader("  A股主要指数")

    try:
        # 从数据库获取 A 股数据（含3个月走势）
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
    # 走势图表
    # ============================================================
    st.subheader("  走势图表")

    # 资产选择（使用 symbol 作为 key）
    symbol_options = list(US_SYMBOLS.keys()) + list(CN_SYMBOLS.keys()) + list(GLOBAL_SYMBOLS.keys())
    symbol_labels = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    selected_symbols = st.multiselect(
        "选择资产",
        options=symbol_options,
        default=["SPY", "QQQ"],
        format_func=lambda x: f"{symbol_labels.get(x, x)} ({x})",
    )

    # 时间范围（下拉框）
    time_range = st.selectbox(
        "时间范围",
        options=["1周", "1月", "3月", "6月", "1年"],
        index=2,  # 默认 3月
    )

    # 计算日期范围
    days_map = {"1周": 7, "1月": 30, "3月": 90, "6月": 180, "1年": 365}
    days = days_map[time_range]
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 获取历史数据
    chart_data = {}
    for symbol in selected_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            chart_data[symbol] = df

    if chart_data:
        # 多资产对比图
        fig = create_multi_line_chart(chart_data, title="资产走势对比")
        st.plotly_chart(fig, use_container_width=True)

        # 单资产详情
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
