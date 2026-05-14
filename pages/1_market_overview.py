"""P1: 市场全景（盯盘）

实时查看美股和A股大类资产行情
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS
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
    if symbol in ["000001", "399001", "000300"]:
        return f"{price:,.2f}"
    return f"${price:,.2f}"


def format_change(change_pct: float) -> str:
    """格式化涨跌幅显示"""
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def get_change_color(change_pct: float) -> str:
    """获取涨跌幅颜色（A股习惯：涨红跌绿）"""
    return "#ef5350" if change_pct >= 0 else "#26a69a"


def render_metric_card(
    title: str,
    price: float,
    change_pct: float,
    symbol: str,
):
    """渲染指标卡片"""
    color = get_change_color(change_pct)
    formatted_price = format_price(price, symbol)
    formatted_change = format_change(change_pct)

    st.markdown(
        f"""
        <div style="
            padding: 1rem;
            border-radius: 0.5rem;
            border: 1px solid #e0e0e0;
            background: white;
            text-align: center;
        ">
            <div style="font-size: 0.875rem; color: #666; margin-bottom: 0.5rem;">{title}</div>
            <div style="font-size: 1.5rem; font-weight: bold; margin-bottom: 0.25rem;">{formatted_price}</div>
            <div style="font-size: 1rem; color: {color}; font-weight: 500;">{formatted_change}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_us_asset_data(dm: DataManager) -> dict:
    """从数据库获取美股资产数据（避免 API 限流）"""
    result = {}
    for symbol, desc in US_SYMBOLS.items():
        df = dm.load(symbol)
        if not df.empty:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            price = latest["close"]
            change_pct = ((price - prev["close"]) / prev["close"]) * 100
            result[symbol] = {
                "desc": desc,
                "price": price,
                "change_pct": change_pct,
            }
    return result


def main():
    st.title("  市场全景")
    st.caption("大类资产实时行情与走势")

    dm = get_data_manager()

    # 侧边栏：数据刷新
    with st.sidebar:
        st.subheader("数据控制")
        if st.button("  刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ============================================================
    # 美股大类资产
    # ============================================================
    st.subheader("  美股大类资产")

    try:
        # 从数据库获取美股数据（避免 API 限流）
        us_data = get_us_asset_data(dm)

        if us_data:
            cols = st.columns(len(US_SYMBOLS))
            for col, (symbol, desc) in zip(cols, US_SYMBOLS.items()):
                with col:
                    if symbol in us_data:
                        data = us_data[symbol]
                        render_metric_card(
                            data["desc"],
                            data["price"],
                            data["change_pct"],
                            symbol,
                        )
                    else:
                        st.warning(f"{desc}: 暂无数据，请先运行数据获取脚本")
        else:
            st.warning("美股数据为空，请先运行数据获取脚本")

    except Exception as e:
        st.error(f"美股数据获取失败: {e}")

    st.divider()

    # ============================================================
    # A股主要指数
    # ============================================================
    st.subheader("  A股主要指数")

    try:
        # 从数据库获取 A 股数据
        cn_symbols = {"000001": "上证综指", "399001": "深证成指", "000300": "沪深300"}
        cols = st.columns(len(cn_symbols))

        for col, (symbol, name) in zip(cols, cn_symbols.items()):
            with col:
                df = dm.load(symbol)
                if not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
                    price = latest["close"]
                    change_pct = ((price - prev["close"]) / prev["close"]) * 100
                    render_metric_card(name, price, change_pct, symbol)
                else:
                    st.warning(f"{name}: 暂无数据")

    except Exception as e:
        st.error(f"A股数据获取失败: {e}")

    st.divider()

    # ============================================================
    # 走势图表
    # ============================================================
    st.subheader("  走势图表")

    # 资产选择（使用 symbol 作为 key）
    symbol_options = list(US_SYMBOLS.keys()) + list(CN_SYMBOLS.keys())
    symbol_labels = {**US_SYMBOLS, **CN_SYMBOLS}

    selected_symbols = st.multiselect(
        "选择资产",
        options=symbol_options,
        default=["SPY", "QQQ"],
        format_func=lambda x: f"{symbol_labels.get(x, x)} ({x})",
    )

    # 时间范围
    time_range = st.select_slider(
        "时间范围",
        options=["1周", "1月", "3月", "6月", "1年"],
        value="3月",
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
