"""全市场扫描渲染组件

设计原则：
- 每个模块有独立的视觉风格（颜色、边框、图标）
- 重点信息用卡片突出，详细数据用表格
- 颜色体系：绿色=机会，蓝色=关注，紫色=均值回归，红色=规避
"""

from typing import Dict, List

import streamlit as st
import pandas as pd

from core.data.config import US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.scanner.scorer import (
    AssetScore,
    OPPORTUNITY_TREND_BUY,
    OPPORTUNITY_TREND_WATCH,
    OPPORTUNITY_MEAN_REVERSION,
    OPPORTUNITY_AVOID,
    OPPORTUNITY_NEUTRAL,
)

OPP_CONFIG = {
    OPPORTUNITY_TREND_BUY: {
        "label": "趋势买入", "icon": " ", "color": "#2e7d32",
        "bg": "#e8f5e9", "border": "#4caf50",
    },
    OPPORTUNITY_TREND_WATCH: {
        "label": "趋势关注", "icon": " ", "color": "#1565c0",
        "bg": "#e3f2fd", "border": "#42a5f5",
    },
    OPPORTUNITY_MEAN_REVERSION: {
        "label": "均值回归", "icon": " ", "color": "#6a1b9a",
        "bg": "#f3e5f5", "border": "#ab47bc",
    },
    OPPORTUNITY_AVOID: {
        "label": "建议规避", "icon": " ", "color": "#c62828",
        "bg": "#ffebee", "border": "#ef5350",
    },
    OPPORTUNITY_NEUTRAL: {
        "label": "中性", "icon": "—", "color": "#757575",
        "bg": "#fafafa", "border": "#bdbdbd",
    },
}


# ============================================================
# 市场情绪总览
# ============================================================

def render_market_overview(scores: List[AssetScore]):
    """市场情绪总览 — 四个带颜色的指标卡"""
    total = len(scores)
    if total == 0:
        st.warning("暂无数据")
        return

    counts = {}
    for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                     OPPORTUNITY_MEAN_REVERSION, OPPORTUNITY_AVOID]:
        counts[opp_type] = sum(1 for s in scores if s.opportunity == opp_type)

    c1, c2, c3, c4 = st.columns(4)

    for col, opp_type in zip([c1, c2, c3, c4], [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                                                  OPPORTUNITY_MEAN_REVERSION, OPPORTUNITY_AVOID]):
        cfg = OPP_CONFIG[opp_type]
        n = counts[opp_type]
        with col:
            st.markdown(
                f'<div style="text-align:center;padding:0.8rem;background:{cfg["bg"]};'
                f'border-radius:0.5rem;border-bottom:3px solid {cfg["border"]};">'
                f'<div style="font-size:1.8rem;">{cfg["icon"]}</div>'
                f'<div style="font-size:2rem;font-weight:bold;color:{cfg["color"]};">{n}</div>'
                f'<div style="font-size:0.85rem;color:{cfg["color"]};">{cfg["label"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # 一句话总结
    buy_n = counts[OPPORTUNITY_TREND_BUY]
    avoid_n = counts[OPPORTUNITY_AVOID]
    if buy_n > 0 and avoid_n == 0:
        st.success(f"市场偏多，{buy_n} 个资产出现趋势机会，无高风险资产")
    elif avoid_n > 0 and buy_n == 0:
        st.error(f"市场偏空，{avoid_n} 个资产需规避，无趋势机会")
    elif buy_n > 0 and avoid_n > 0:
        st.warning(f"分化明显：{buy_n} 个趋势机会 vs {avoid_n} 个需规避，注意择时")
    else:
        st.info("市场中性，暂无明确方向信号")


# ============================================================
# 机会分组
# ============================================================

def render_opportunity_group(
    scores: List[AssetScore],
    opportunity_type: str,
    expanded: bool = True,
):
    """渲染一组机会资产 — 卡片 + 表格"""
    cfg = OPP_CONFIG.get(opportunity_type, OPP_CONFIG[OPPORTUNITY_NEUTRAL])
    group = [s for s in scores if s.opportunity == opportunity_type]

    if not group:
        return

    # ---- 模块头部 ----
    st.markdown(
        f'<div style="padding:0.6rem 1rem;background:{cfg["bg"]};border-left:4px solid {cfg["border"]};'
        f'border-radius:0 0.3rem 0.3rem 0;margin-bottom:0.5rem;">'
        f'<span style="font-size:1.2rem;">{cfg["icon"]}</span> '
        f'<span style="font-size:1.1rem;font-weight:600;color:{cfg["color"]};">{cfg["label"]}</span>'
        f'<span style="color:#999;margin-left:0.5rem;">{len(group)} 个资产</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- 机会卡片（每个资产一张） ----
    for s in group:
        _render_asset_card(s, cfg)

    # ---- 策略提示 ----
    strategies = set()
    for s in group:
        strategies.update(s.strategies)
    if strategies:
        st.caption(f"  对应策略：{'、'.join(strategies)}")

    # ---- 详细数据表（折叠） ----
    with st.expander("详细数据", expanded=False):
        rows = []
        for s in group:
            rows.append({
                "资产": s.name, "代码": s.symbol,
                "趋势分": s.trend_score, "回归分": s.reversion_score,
                "风险分": s.risk_score, "动量": s.momentum,
                "RSI": s.rsi, "均线": s.ma_alignment,
                "MACD": s.macd_signal, "回撤%": s.current_drawdown,
                "波动率%": s.volatility,
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_asset_card(s: AssetScore, cfg: dict):
    """单个资产的机会卡片"""
    # 关键指标
    indicators = []
    if s.momentum is not None:
        indicators.append(f"动量 {s.momentum:.0f}")
    if s.ma_alignment:
        indicators.append(s.ma_alignment)
    if s.rsi is not None:
        indicators.append(f"RSI {s.rsi:.0f}")
    if s.macd_signal and s.macd_signal != "无信号":
        indicators.append(s.macd_signal)

    indicator_text = " · ".join(indicators)

    # 回撤/波动率（风险相关）
    risk_parts = []
    if s.current_drawdown < -5:
        risk_parts.append(f"回撤 {s.current_drawdown:.1f}%")
    if s.volatility > 20:
        risk_parts.append(f"波动率 {s.volatility:.1f}%")
    risk_text = ""
    if risk_parts:
        risk_text = f'<span style="color:#c62828;font-size:0.8rem;margin-left:0.5rem;">{" · ".join(risk_parts)}</span>'

    st.markdown(
        f'<div style="padding:0.5rem 0.8rem;margin-bottom:0.3rem;border-radius:0.3rem;'
        f'border:1px solid #e0e0e0;background:white;">'
        f'<span style="font-weight:600;color:{cfg["color"]};font-size:1rem;">{s.name}</span>'
        f'<span style="color:#999;font-size:0.85rem;margin-left:0.3rem;">{s.symbol}</span>'
        f'{risk_text}'
        f'<div style="font-size:0.85rem;color:#555;margin-top:0.3rem;">{indicator_text}</div>'
        f'<div style="font-size:0.9rem;color:{cfg["color"]};margin-top:0.2rem;font-weight:500;">{s.summary}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# 规避清单
# ============================================================

def render_avoid_list(scores: List[AssetScore]):
    """渲染规避清单 — 红色警告风格"""
    group = [s for s in scores if s.opportunity == OPPORTUNITY_AVOID]

    if not group:
        st.success("✅ 当前无需规避的资产")
        return

    cfg = OPP_CONFIG[OPPORTUNITY_AVOID]

    # 红色警告头部
    st.markdown(
        f'<div style="padding:0.8rem 1rem;background:{cfg["bg"]};border:2px solid {cfg["border"]};'
        f'border-radius:0.5rem;margin-bottom:0.5rem;">'
        f'<span style="font-size:1.2rem;">{cfg["icon"]}</span> '
        f'<span style="font-size:1.1rem;font-weight:700;color:{cfg["color"]};">'
        f'以下 {len(group)} 个资产建议规避</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for s in group:
        # 风险原因标签
        reasons = []
        if s.current_drawdown < -15:
            reasons.append("  深度回撤")
        elif s.current_drawdown < -10:
            reasons.append("  回撤较大")
        if s.ma_alignment == "空头排列":
            reasons.append("  空头排列")
        if s.volatility > 25:
            reasons.append("⚡ 高波动")
        if s.risk_score >= 80:
            reasons.append("  高风险")

        tags = " ".join(reasons)

        st.markdown(
            f'<div style="padding:0.5rem 0.8rem;margin-bottom:0.3rem;border-radius:0.3rem;'
            f'border:1px solid {cfg["border"]};background:{cfg["bg"]};">'
            f'<span style="font-weight:600;color:{cfg["color"]};">{s.name}</span>'
            f'<span style="color:#999;font-size:0.85rem;margin-left:0.3rem;">{s.symbol}</span>'
            f'<span style="margin-left:0.5rem;">{tags}</span>'
            f'<div style="font-size:0.9rem;color:{cfg["color"]};margin-top:0.2rem;">{s.summary}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ============================================================
# 完整评分表
# ============================================================

def render_full_table(scores: List[AssetScore]):
    """渲染完整评分表"""
    rows = []
    for s in scores:
        cfg = OPP_CONFIG.get(s.opportunity, OPP_CONFIG[OPPORTUNITY_NEUTRAL])
        rows.append({
            "机会": cfg["label"],
            "资产": s.name,
            "代码": s.symbol,
            "趋势分": s.trend_score,
            "回归分": s.reversion_score,
            "风险分": s.risk_score,
            "动量": s.momentum,
            "RSI": s.rsi,
            "均线": s.ma_alignment,
            "1月%": s.ret_1m,
            "3月%": s.ret_3m,
            "回撤%": s.current_drawdown,
            "波动率%": s.volatility,
            "信号": s.summary,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
