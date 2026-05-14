"""P2: 机会扫描（分析）

基于双重动量策略的大类资产分析
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

import streamlit as st
import pandas as pd

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.strategy.indicators import (
    calc_returns,
    calc_momentum_score,
    calc_ma_alignment,
    calc_rsi,
    calc_rsi_status,
    calc_macd,
    calc_macd_signal,
    calc_max_drawdown,
    calc_current_drawdown,
    calc_volatility,
    calc_correlation_matrix,
    calc_drawdown,
    detect_market_regime,
    calc_core_satellite_allocation,
    generate_rotation_signals,
    CORE_ASSETS,
    SATELLITE_ASSETS,
)
from app.components.charts import (
    create_indicator_chart,
    create_correlation_heatmap,
    create_drawdown_chart,
)

# 页面配置
st.set_page_config(
    page_title="机会扫描 - Market Pulse",
    page_icon=" ",
    layout="wide",
)

# 缓存 DataManager 实例
@st.cache_resource
def get_data_manager():
    return DataManager()


def load_all_assets(dm: DataManager, days: int = 365) -> Dict[str, pd.DataFrame]:
    """加载全部资产数据"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    data = {}

    for symbol in all_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            data[symbol] = df

    return data


def build_momentum_table(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """构建动量排名表"""
    rows = []
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS}

    for symbol, df in data.items():
        close = df["close"]

        # 多周期收益率
        returns = calc_returns(close, [21, 63, 126])
        ret_dict = returns.to_dict("records")[0]

        # 综合动量评分
        score = calc_momentum_score(close)

        # 均线排列
        alignment = calc_ma_alignment(close)

        rows.append({
            "资产": all_symbols.get(symbol, symbol),
            "代码": symbol,
            "1月%": ret_dict.get("1M"),
            "3月%": ret_dict.get("3M"),
            "6月%": ret_dict.get("6M"),
            "动量评分": score,
            "趋势": alignment,
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("动量评分", ascending=False, na_position="last")
    df = df.reset_index(drop=True)
    df.index = df.index + 1  # 排名从 1 开始
    df.index.name = "排名"

    return df


def get_trend_status(df: pd.DataFrame) -> dict:
    """获取资产趋势状态"""
    close = df["close"]

    # 均线排列
    alignment = calc_ma_alignment(close)

    # RSI
    rsi = calc_rsi(close)
    rsi_value = round(rsi.iloc[-1], 1) if not rsi.empty else None
    rsi_status = calc_rsi_status(rsi_value) if rsi_value else "N/A"

    # MACD
    dif, dea, hist = calc_macd(close)
    macd_signal = calc_macd_signal(dif, dea)

    # 回撤
    current_dd = calc_current_drawdown(close)
    max_dd = calc_max_drawdown(close)

    # 波动率
    volatility = calc_volatility(close)

    return {
        "均线排列": alignment,
        "RSI": rsi_value,
        "RSI状态": rsi_status,
        "MACD信号": macd_signal,
        "当前回撤": current_dd,
        "最大回撤": max_dd,
        "年化波动率": volatility,
    }


def render_status_card(status: dict):
    """渲染趋势状态卡片"""
    # 均线排列颜色
    alignment = status["均线排列"]
    alignment_color = {
        "多头排列": "#ef5350",
        "空头排列": "#26a69a",
        "交织": "#999",
    }.get(alignment, "#999")

    # RSI 颜色
    rsi_status = status["RSI状态"]
    rsi_color = {
        "超买": "#ef5350",
        "超卖": "#26a69a",
        "中性": "#999",
    }.get(rsi_status, "#999")

    # MACD 颜色
    macd_signal = status["MACD信号"]
    macd_color = {
        "金叉": "#ef5350",
        "死叉": "#26a69a",
        "无信号": "#999",
    }.get(macd_signal, "#999")

    # 回撤颜色（超过 -8% 警告）
    dd = status["当前回撤"]
    dd_color = "#ef5350" if dd < -8 else "#ff9800" if dd < -5 else "#999"

    st.markdown(
        f"""
        <div style="
            border-radius: 0.5rem;
            border: 1px solid #e0e0e0;
            background: white;
            padding: 1.2rem;
        ">
            <div style="font-size: 1rem; font-weight: bold; margin-bottom: 0.8rem;">趋势状态</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem;">
                <div>
                    <div style="font-size: 0.8rem; color: #666;">均线排列</div>
                    <div style="font-size: 1rem; color: {alignment_color}; font-weight: 500;">{alignment}</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #666;">RSI (14)</div>
                    <div style="font-size: 1rem; color: {rsi_color}; font-weight: 500;">{status['RSI']} ({rsi_status})</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #666;">MACD</div>
                    <div style="font-size: 1rem; color: {macd_color}; font-weight: 500;">{macd_signal}</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #666;">当前回撤</div>
                    <div style="font-size: 1rem; color: {dd_color}; font-weight: 500;">{dd}%</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #666;">最大回撤</div>
                    <div style="font-size: 1rem; color: #999; font-weight: 500;">{status['最大回撤']}%</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #666;">年化波动率</div>
                    <div style="font-size: 1rem; color: #999; font-weight: 500;">{status['年化波动率']}%</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def generate_signals(data: Dict[str, pd.DataFrame]) -> list:
    """生成关键信号"""
    signals = []
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS}

    # 1. 股债分歧（SPY vs TLT）
    if "SPY" in data and "TLT" in data:
        spy_score = calc_momentum_score(data["SPY"]["close"])
        tlt_score = calc_momentum_score(data["TLT"]["close"])
        if spy_score and tlt_score:
            if spy_score > tlt_score + 10:
                signals.append({
                    "icon": " ",
                    "text": "股债轮动：SPY 动量显著强于 TLT → 风险偏好上升，资金流向股市",
                    "type": "info",
                })
            elif tlt_score > spy_score + 10:
                signals.append({
                    "icon": " ️",
                    "text": "股债轮动：TLT 动量显著强于 SPY → 避险情绪升温，资金流向债市",
                    "type": "warning",
                })

    # 2. 油价异动
    if "CL" in data:
        cl_vol = calc_volatility(data["CL"]["close"])
        cl_ret = calc_returns(data["CL"]["close"], [21])
        cl_1m = cl_ret.to_dict("records")[0].get("1M", 0)
        if cl_1m and abs(cl_1m) > 10:
            direction = "上涨" if cl_1m > 0 else "下跌"
            signals.append({
                "icon": "⛽",
                "text": f"油价异动：CL 近 1 月 {direction} {abs(cl_1m):.1f}%，波动率 {cl_vol:.1f}% → 关注通胀/能源风险",
                "type": "warning" if cl_1m > 0 else "info",
            })

    # 3. 中美分化
    if "SPY" in data and "000300" in data:
        spy_score = calc_momentum_score(data["SPY"]["close"])
        hs300_score = calc_momentum_score(data["000300"]["close"])
        if spy_score and hs300_score:
            if spy_score > hs300_score + 15:
                signals.append({
                    "icon": " ",
                    "text": "中美分化：美股动量明显强于 A 股 → A 股可能滞后跟随或继续走弱",
                    "type": "info",
                })
            elif hs300_score > spy_score + 15:
                signals.append({
                    "icon": " ",
                    "text": "中美分化：A 股动量明显强于美股 → A 股独立行情，关注政策驱动",
                    "type": "info",
                })

    # 4. 超买超卖警告
    for symbol, df in data.items():
        rsi = calc_rsi(df["close"])
        if not rsi.empty:
            rsi_value = rsi.iloc[-1]
            name = all_symbols.get(symbol, symbol)
            if rsi_value >= 75:
                signals.append({
                    "icon": " ",
                    "text": f"超买警告：{name} RSI = {rsi_value:.0f} → 短期回调风险较高",
                    "type": "error",
                })
            elif rsi_value <= 25:
                signals.append({
                    "icon": " ",
                    "text": f"超卖机会：{name} RSI = {rsi_value:.0f} → 可能存在反弹机会",
                    "type": "success",
                })

    # 5. 回撤警告
    for symbol, df in data.items():
        dd = calc_current_drawdown(df["close"])
        name = all_symbols.get(symbol, symbol)
        if dd < -10:
            signals.append({
                "icon": " ",
                "text": f"回撤警告：{name} 当前回撤 {dd}% → 超过组合止损线，需关注",
                "type": "error",
            })

    if not signals:
        signals.append({
            "icon": "✅",
            "text": "当前无异常信号，市场运行平稳",
            "type": "success",
        })

    return signals


def render_regime_card(regime: dict):
    """渲染市场环境卡片"""
    regime_name = regime["regime"]
    # 环境颜色
    colors = {
        "风险偏好": "#ef5350",
        "避险": "#2196f3",
        "危机": "#f44336",
        "滞胀担忧": "#ff9800",
        "震荡": "#9e9e9e",
        "中性": "#999",
    }
    color = colors.get(regime_name, "#999")

    st.markdown(
        f"""
        <div style="
            border-radius: 0.5rem;
            border: 2px solid {color};
            background: white;
            padding: 1.2rem;
            margin-bottom: 1rem;
        ">
            <div style="display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.6rem;">
                <span style="font-size: 1.5rem;"> </span>
                <span style="font-size: 1.3rem; font-weight: bold; color: {color};">{regime_name}</span>
            </div>
            <div style="font-size: 0.95rem; color: #555; margin-bottom: 0.8rem;">{regime["description"]}</div>
            <div style="display: flex; gap: 1.5rem; font-size: 0.85rem; color: #666;">
                <span>SPY 动量: <b>{regime["spy_score"]}</b></span>
                <span>TLT 动量: <b>{regime["tlt_score"]}</b></span>
                <span>CL 动量: <b>{regime["cl_score"]}</b></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_allocation_card(allocation: dict, all_symbols: dict):
    """渲染核心卫星配置建议卡片"""
    core = allocation["core"]
    satellite = allocation["satellite"]
    cash = allocation["cash"]

    # 构建配置表格
    rows = []
    for symbol, pct in core.items():
        name = all_symbols.get(symbol, symbol)
        rows.append({"类别": "核心", "资产": name, "代码": symbol, "比例": f"{pct}%"})
    for symbol, pct in satellite.items():
        name = all_symbols.get(symbol, symbol)
        rows.append({"类别": "卫星", "资产": name, "代码": symbol, "比例": f"{pct}%"})
    if cash > 0:
        rows.append({"类别": "现金", "资产": "现金", "代码": "-", "比例": f"{cash}%"})

    df = pd.DataFrame(rows)

    st.markdown("**核心卫星配置建议**")
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_rotation_signals(signals: list, all_symbols: dict):
    """渲染轮动信号"""
    if not signals:
        st.info("当前无轮动信号")
        return

    for sig in signals:
        action = sig["action"]
        symbol = sig["symbol"]
        name = all_symbols.get(symbol, symbol)
        reason = sig["reason"]
        strength = sig["strength"]

        # 颜色和图标
        if action == "买入":
            icon = " "
            color = "success"
        elif action == "卖出":
            icon = " "
            color = "error"
        elif action == "轮动":
            icon = " "
            color = "warning"
        else:
            icon = " "
            color = "info"

        text = f"{icon} **{action} {name}**（{strength}信号）— {reason}"

        if color == "success":
            st.success(text)
        elif color == "error":
            st.error(text)
        elif color == "warning":
            st.warning(text)
        else:
            st.info(text)


def main():
    st.title("  机会扫描")
    st.caption("基于双重动量+核心卫星策略的大类资产分析")

    dm = get_data_manager()

    # 加载数据
    with st.spinner("加载资产数据..."):
        data = load_all_assets(dm, days=365)

    if not data:
        st.error("暂无数据，请先在「市场全景」页面刷新数据")
        return

    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS}

    # ============================================================
    # 市场环境判断
    # ============================================================
    st.subheader("  市场环境")

    if "SPY" in data and "TLT" in data and "CL" in data:
        regime = detect_market_regime(
            data["SPY"]["close"],
            data["TLT"]["close"],
            data["CL"]["close"],
        )
        render_regime_card(regime)
    else:
        regime = {"regime": "中性", "description": "数据不足，无法判断"}
        st.warning("缺少 SPY/TLT/CL 数据，无法判断市场环境")

    st.divider()

    # ============================================================
    # 核心卫星配置
    # ============================================================
    st.subheader("  核心卫星配置")

    # 计算所有资产动量评分
    momentum_scores = {}
    for symbol, df in data.items():
        score = calc_momentum_score(df["close"])
        momentum_scores[symbol] = score if score else 50

    allocation = calc_core_satellite_allocation(regime["regime"], momentum_scores)
    render_allocation_card(allocation, all_symbols)

    # 轮动信号
    st.markdown("**卫星轮动信号**")
    rotation_signals = generate_rotation_signals(momentum_scores)
    render_rotation_signals(rotation_signals, all_symbols)

    st.divider()

    # ============================================================
    # 动量排名
    # ============================================================
    st.subheader("  动量排名")

    momentum_df = build_momentum_table(data)

    # 格式化显示
    display_df = momentum_df.copy()
    for col in ["1月%", "3月%", "6月%"]:
        display_df[col] = display_df[col].apply(
            lambda x: f"+{x:.1f}%" if x and x > 0 else f"{x:.1f}%" if x else "N/A"
        )
    display_df["动量评分"] = display_df["动量评分"].apply(
            lambda x: f"{x:.0f}" if x else "N/A"
    )

    st.dataframe(
        display_df[["资产", "代码", "1月%", "3月%", "6月%", "动量评分", "趋势"]],
        use_container_width=True,
    )

    st.divider()

    # ============================================================
    # 趋势详情
    # ============================================================
    st.subheader("  趋势详情")

    # 资产选择
    available_symbols = list(data.keys())
    selected = st.selectbox(
        "选择资产",
        options=available_symbols,
        format_func=lambda x: f"{all_symbols.get(x, x)} ({x})",
    )

    if selected:
        df = data[selected]

        col_chart, col_status = st.columns([2, 1])

        with col_chart:
            # 带均线的 K 线图
            fig = create_indicator_chart(df, selected)
            st.plotly_chart(fig, use_container_width=True)

        with col_status:
            # 趋势状态卡片
            status = get_trend_status(df)
            render_status_card(status)

    st.divider()

    # ============================================================
    # 风险面板
    # ============================================================
    st.subheader("  风险面板")

    col_dd, col_corr = st.columns(2)

    with col_dd:
        # 回撤对比图
        st.markdown("**回撤对比**")
        fig = create_drawdown_chart(data)
        st.plotly_chart(fig, use_container_width=True)

    with col_corr:
        # 相关性热力图
        st.markdown("**资产相关性**")
        corr = calc_correlation_matrix(data)
        if not corr.empty:
            # 重命名索引和列为中文名
            name_map = {s: all_symbols.get(s, s) for s in corr.columns}
            corr = corr.rename(index=name_map, columns=name_map)
            fig = create_correlation_heatmap(corr)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ============================================================
    # 关键信号
    # ============================================================
    st.subheader("  关键信号")

    signals = generate_signals(data)

    for signal in signals:
        signal_type = signal["type"]
        if signal_type == "error":
            st.error(f"{signal['icon']} {signal['text']}")
        elif signal_type == "warning":
            st.warning(f"{signal['icon']} {signal['text']}")
        elif signal_type == "success":
            st.success(f"{signal['icon']} {signal['text']}")
        else:
            st.info(f"{signal['icon']} {signal['text']}")


if __name__ == "__main__":
    main()
