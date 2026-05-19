"""P2: 全市场机会扫描

扫描所有资产，发现趋势、均值回归、规避机会。
底部可展开查看动量+核心卫星策略跟踪。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.scanner.scorer import score_all_assets
from core.scanner.history import ScanHistory
from core.scanner.renderer import (
    render_market_overview,
    render_opportunity_group,
    render_avoid_list,
    render_full_table,
)
from core.scanner.scorer import (
    OPPORTUNITY_TREND_BUY,
    OPPORTUNITY_TREND_WATCH,
    OPPORTUNITY_MEAN_REVERSION,
    OPPORTUNITY_AVOID,
)
from core.strategy.indicators import (
    calc_momentum_score,
    calc_ma_alignment,
    calc_current_drawdown,
    detect_market_regime,
    calc_regime_score,
)


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="机会扫描 - Market Pulse",
    page_icon=" ",
    layout="wide",
)


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


def _render_market_context(data: Dict[str, pd.DataFrame]):
    """市场概况 — 顶部上下文信息"""
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    # 市场体制
    spy_close = data.get("SPY", pd.DataFrame()).get("close", pd.Series())
    tlt_close = data.get("TLT", pd.DataFrame()).get("close", pd.Series())
    cl_close = data.get("CL", pd.DataFrame()).get("close", pd.Series())

    regime = {"regime": "中性", "description": "数据不足"}
    composite = 50
    vol_regime = "正常"

    if not spy_close.empty and not tlt_close.empty and not cl_close.empty:
        regime = detect_market_regime(spy_close, tlt_close, cl_close)
        regime_score = calc_regime_score(spy_close)
        composite = regime_score["composite"]
        vol_regime = regime_score["vol_regime"]

    # 体制颜色
    regime_colors = {
        "风险偏好": ("#2e7d32", "#e8f5e9"),
        "避险": ("#1565c0", "#e3f2fd"),
        "危机": ("#c62828", "#ffebee"),
        "滞胀担忧": ("#e65100", "#fff3e0"),
        "震荡": ("#757575", "#f5f5f5"),
        "中性": ("#757575", "#f5f5f5"),
    }
    color, bg = regime_colors.get(regime["regime"], ("#757575", "#f5f5f5"))

    # 评分颜色
    score_color = "#2e7d32" if composite >= 65 else "#c62828" if composite <= 35 else "#757575"

    # 体制卡片
    col_regime, col_score = st.columns([3, 1])
    with col_regime:
        st.markdown(
            f'<div style="padding:0.8rem 1rem;background:{bg};border-left:4px solid {color};border-radius:0 0.3rem 0.3rem 0;">'
            f'<span style="font-size:1.3rem;font-weight:bold;color:{color};">{regime["regime"]}</span>'
            f'<span style="color:#666;margin-left:0.5rem;">{regime["description"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_score:
        st.metric("综合评分", f"{composite:.0f}", delta=vol_regime, delta_color="off")

    # 关键资产快照
    key_assets = ["SPY", "QQQ", "000300", "TLT"]
    cols = st.columns(len(key_assets))
    for col, symbol in zip(cols, key_assets):
        if symbol not in data:
            continue
        close = data[symbol]["close"]
        name = all_symbols.get(symbol, symbol)
        mom = calc_momentum_score(close)
        dd = calc_current_drawdown(close)
        alignment = calc_ma_alignment(close)

        # 趋势颜色
        trend_color = "#2e7d32" if alignment == "多头排列" else "#c62828" if alignment == "空头排列" else "#757575"
        dd_color = "#c62828" if dd < -10 else "#e65100" if dd < -5 else "#757575"

        with col:
            st.markdown(
                f'<div style="text-align:center;padding:0.5rem;background:#fafafa;border-radius:0.3rem;">'
                f'<div style="font-size:0.8rem;color:#888;">{name}</div>'
                f'<div style="font-size:1.1rem;font-weight:600;color:{trend_color};">{alignment}</div>'
                f'<div style="font-size:0.8rem;color:{dd_color};">回撤 {dd:.1f}%</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def main():
    st.title("  全市场机会扫描")
    st.caption("扫描所有资产，发现趋势、均值回归、规避机会")

    dm = get_data_manager()

    with st.spinner("加载资产数据..."):
        data = load_all_assets(dm, days=365)

    if not data:
        st.error("暂无数据，请先在「市场全景」页面刷新数据")
        return

    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    # ============================================================
    # 市场概况（顶部上下文）
    # ============================================================
    _render_market_context(data)

    st.divider()

    # ============================================================
    # 全市场扫描
    # ============================================================
    scores = score_all_assets(data)

    # 持久化扫描结果（供 P4 复盘使用）
    spy_close = data.get("SPY", pd.DataFrame()).get("close", pd.Series())
    regime_name, composite = "中性", 50.0
    if not spy_close.empty:
        from core.strategy.indicators import calc_regime_score
        rs = calc_regime_score(spy_close)
        composite = rs.get("composite", 50)
        spy_tlt_cl = all(
            not data.get(s, pd.DataFrame()).get("close", pd.Series()).empty
            for s in ["SPY", "TLT", "CL"]
        )
        if spy_tlt_cl:
            regime_info = detect_market_regime(
                data["SPY"]["close"], data["TLT"]["close"], data["CL"]["close"]
            )
            regime_name = regime_info.get("regime", "中性")
    ScanHistory().save_snapshot(scores, regime_name, composite)

    # 市场情绪总览
    st.subheader("  市场情绪")
    render_market_overview(scores)

    st.divider()

    # 机会发现
    st.subheader("  机会发现")
    render_opportunity_group(scores, OPPORTUNITY_TREND_BUY, expanded=True)
    render_opportunity_group(scores, OPPORTUNITY_MEAN_REVERSION, expanded=True)
    render_opportunity_group(scores, OPPORTUNITY_TREND_WATCH, expanded=False)

    st.divider()

    # 规避清单
    st.subheader("  规避清单")
    render_avoid_list(scores)

    st.divider()

    # 完整评分表
    with st.expander("  完整评分表", expanded=False):
        render_full_table(scores)


if __name__ == "__main__":
    main()
