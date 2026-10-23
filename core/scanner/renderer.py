"""全市场扫描渲染组件 — 门槛 8% 口径

版式原则：
- 先给结论（过门槛几个、该看谁、不碰谁），再展开明细
- 绿=过门槛候选，蓝=门槛附近，红=不碰
- 波动率只做仓位提示，不做淘汰标签
"""

from typing import Dict, List

import streamlit as st
import pandas as pd

from app.components.tooltips import (
    MOMENTUM_SCORE, DRAWDOWN, VOLATILITY,
    RETURN_1M, RETURN_3M, tip_icon,
)
from core.scanner.scorer import (
    AssetScore,
    OPPORTUNITY_CANDIDATE,
    OPPORTUNITY_NEAR,
    OPPORTUNITY_AVOID,
    OPPORTUNITY_NEUTRAL,
    ABS_MOMENTUM_MIN_PCT,
    TOP_N,
    MAX_WEIGHT,
)

OPP_CONFIG = {
    OPPORTUNITY_CANDIDATE: {
        "label": "过门槛候选", "icon": " ", "color": "#2e7d32",
        "bg": "#e8f5e9", "border": "#4caf50",
    },
    OPPORTUNITY_NEAR: {
        "label": "门槛附近", "icon": " ", "color": "#1565c0",
        "bg": "#e3f2fd", "border": "#42a5f5",
    },
    OPPORTUNITY_AVOID: {
        "label": "不碰", "icon": " ", "color": "#c62828",
        "bg": "#ffebee", "border": "#ef5350",
    },
    OPPORTUNITY_NEUTRAL: {
        "label": "数据不足", "icon": "—", "color": "#757575",
        "bg": "#fafafa", "border": "#bdbdbd",
    },
}


# ============================================================
# 门槛规则说明
# ============================================================

def render_threshold_explainer():
    """把「门槛是什么」摊开写清楚 — 定义 + 三档规则。"""
    thr = ABS_MOMENTUM_MIN_PCT
    st.markdown("####   门槛是什么")

    st.markdown(
        f'<div style="padding:0.9rem 1.1rem;background:#f8f9fa;border:1px solid #e0e0e0;'
        f'border-radius:0.5rem;margin-bottom:0.8rem;">'
        f'<div style="font-size:1.05rem;font-weight:700;color:#222;margin-bottom:0.45rem;">'
        f'买入门槛 = <span style="color:#2e7d32;font-size:1.25rem;">过去 12 个月涨幅 &gt; {thr:.0f}%</span></div>'
        f'<div style="color:#555;font-size:0.92rem;line-height:1.55;">'
        f'只涨一点点就买的假趋势很容易吐回去。门槛 {thr:.0f}% 过滤这些噪音——'
        f'<b>不到 {thr:.0f}% 宁可空着拿现金/SHY</b>。'
        f'过门槛后按动量排序取前 {TOP_N} 个，单票凯利仓位顶 {int(MAX_WEIGHT * 100)}%。'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # 三档规则
    col1, col2, col3 = st.columns(3)
    rules = [
        (col1, "不碰", "12M ≤ 0%", "或 12M < −5% / 深回撤+空头", "#c62828", "#ffebee", "#ef5350"),
        (col2, "门槛附近", f"0% < 12M ≤ {thr:.0f}%", "趋势不稳，先不买", "#1565c0", "#e3f2fd", "#42a5f5"),
        (col3, "过门槛候选", f"12M > {thr:.0f}%", f"动量前 {TOP_N} 可买，参考仓位 0~{int(MAX_WEIGHT*100)}%", "#2e7d32", "#e8f5e9", "#4caf50"),
    ]
    for col, title, cond, note, color, bg, border in rules:
        with col:
            st.markdown(
                f'<div style="padding:0.75rem 0.9rem;background:{bg};border-radius:0.45rem;'
                f'border-top:3px solid {border};height:100%;">'
                f'<div style="font-weight:700;color:{color};font-size:1rem;">{title}</div>'
                f'<div style="font-size:1.15rem;font-weight:700;color:#222;margin:0.25rem 0;">{cond}</div>'
                f'<div style="font-size:0.82rem;color:#666;">{note}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.caption("波动率只影响仓位大小，不淘汰标的；均值回归不是本策略的机会轴。")


# ============================================================
# 结论总览（页面最上）
# ============================================================

def render_conclusion(scores: List[AssetScore]):
    """一句话结论 + 三桶计数 + top 候选。"""
    if not scores:
        st.warning("暂无数据")
        return

    candidates = [s for s in scores if s.opportunity == OPPORTUNITY_CANDIDATE]
    near = [s for s in scores if s.opportunity == OPPORTUNITY_NEAR]
    avoid = [s for s in scores if s.opportunity == OPPORTUNITY_AVOID]
    top = sorted(candidates, key=lambda s: s.momentum_rank)[:TOP_N]

    # 结论横幅
    if top:
        names = "、".join(f"{s.name} {s.kelly_weight:.0%}" for s in top)
        st.success(
            f"**生产策略口径（12M >{ABS_MOMENTUM_MIN_PCT:.0f}%）**："
            f"{len(candidates)} 个过门槛，优先看 **{names}**。"
            f"没过门槛的宁可现金/SHY。"
        )
    elif near:
        st.info(
            f"**生产策略口径（12M >{ABS_MOMENTUM_MIN_PCT:.0f}%）**："
            f"0 个过门槛，{len(near)} 个在门槛附近，**先不买，拿现金/SHY**。"
        )
    else:
        st.warning(
            f"**生产策略口径（12M >{ABS_MOMENTUM_MIN_PCT:.0f}%）**："
            f"市场整体偏弱，{len(avoid)} 个不碰，**空仓防守**。"
        )

    # 三桶计数卡
    c1, c2, c3 = st.columns(3)
    buckets = [
        (c1, OPPORTUNITY_CANDIDATE, candidates),
        (c2, OPPORTUNITY_NEAR, near),
        (c3, OPPORTUNITY_AVOID, avoid),
    ]
    for col, opp_type, group in buckets:
        cfg = OPP_CONFIG[opp_type]
        with col:
            st.markdown(
                f'<div style="text-align:center;padding:0.7rem;background:{cfg["bg"]};'
                f'border-radius:0.5rem;border-bottom:3px solid {cfg["border"]};">'
                f'<div style="font-size:1.5rem;">{cfg["icon"]}</div>'
                f'<div style="font-size:1.8rem;font-weight:bold;color:{cfg["color"]};">{len(group)}</div>'
                f'<div style="font-size:0.85rem;color:{cfg["color"]};">{cfg["label"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ============================================================
# 机会分组
# ============================================================

def render_opportunity_group(
    scores: List[AssetScore],
    opportunity_type: str,
    expanded: bool = True,
):
    """渲染一组资产 — 卡片 + 明细表。"""
    cfg = OPP_CONFIG.get(opportunity_type, OPP_CONFIG[OPPORTUNITY_NEUTRAL])
    group = [s for s in scores if s.opportunity == opportunity_type]
    if not group:
        return

    # 模块头部
    st.markdown(
        f'<div style="padding:0.55rem 1rem;background:{cfg["bg"]};border-left:4px solid {cfg["border"]};'
        f'border-radius:0 0.3rem 0.3rem 0;margin-bottom:0.5rem;">'
        f'<span style="font-size:1.15rem;">{cfg["icon"]}</span> '
        f'<span style="font-size:1.05rem;font-weight:600;color:{cfg["color"]};">{cfg["label"]}</span>'
        f'<span style="color:#999;margin-left:0.5rem;">{len(group)} 个资产</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for s in group:
        _render_asset_card(s, cfg)

    strategies = set()
    for s in group:
        strategies.update(s.strategies)
    if strategies:
        st.caption(f"  对应策略：{'、'.join(strategies)}")

    with st.expander("详细数据", expanded=False):
        rows = [_score_row(s) for s in group]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _score_row(s: AssetScore) -> dict:
    cfg = OPP_CONFIG.get(s.opportunity, OPP_CONFIG[OPPORTUNITY_NEUTRAL])
    return {
        "分组": cfg["label"],
        "资产": s.name,
        "代码": s.symbol,
        "12M%": s.ret_12m,
        "过门槛": "✓" if s.passes_threshold else "",
        "动量排名": s.momentum_rank,
        "参考仓位": f"{s.kelly_weight:.0%}" if s.kelly_weight > 1e-4 else "—",
        "回撤%": s.current_drawdown,
        "波动率%": s.volatility,
        "均线": s.ma_alignment,
        "1月%": s.ret_1m,
        "3月%": s.ret_3m,
    }


def _render_asset_card(s: AssetScore, cfg: dict):
    """单个资产卡片 — 12M/门槛/仓位优先。"""
    badges = []
    if s.ret_12m is not None:
        color = "#2e7d32" if s.passes_threshold else "#c62828" if s.ret_12m <= 0 else "#e65100"
        badges.append(
            f'<span style="color:{color};font-weight:600;">12M {s.ret_12m:+.1f}%</span>'
        )
    if s.passes_threshold and s.momentum_rank <= TOP_N:
        badges.append(f'<span style="color:#2e7d32;">动量 #{s.momentum_rank}</span>')
    if s.kelly_weight > 1e-4:
        badges.append(f'<span style="color:#1565c0;">仓位 {s.kelly_weight:.0%}</span>')
    if s.current_drawdown < -5:
        badges.append(f'<span style="color:#c62828;">回撤 {s.current_drawdown:.1f}%{tip_icon(DRAWDOWN)}</span>')

    badge_html = "　".join(badges)

    # 辅助指标（次要）
    aux = []
    if s.ma_alignment:
        aux.append(s.ma_alignment)
    if s.rsi is not None:
        aux.append(f"RSI {s.rsi:.0f}")
    if s.volatility:
        aux.append(f"波动 {s.volatility:.0f}%{tip_icon(VOLATILITY)}")
    aux_text = " · ".join(aux)

    st.markdown(
        f'<div style="padding:0.55rem 0.85rem;margin-bottom:0.35rem;border-radius:0.35rem;'
        f'border:1px solid #e0e0e0;background:white;">'
        f'<div style="display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;">'
        f'<span style="font-weight:600;color:{cfg["color"]};font-size:1rem;">{s.name}</span>'
        f'<span style="color:#999;font-size:0.85rem;">{s.symbol}</span>'
        f'<span style="margin-left:auto;">{badge_html}</span>'
        f'</div>'
        f'<div style="font-size:0.82rem;color:#666;margin-top:0.25rem;">{aux_text}</div>'
        f'<div style="font-size:0.9rem;color:{cfg["color"]};margin-top:0.2rem;font-weight:500;">{s.summary}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# 不碰清单
# ============================================================

def render_avoid_list(scores: List[AssetScore]):
    """不碰清单 — 红色警告。"""
    group = [s for s in scores if s.opportunity == OPPORTUNITY_AVOID]
    if not group:
        st.success("当前没有明确不碰的资产")
        return

    cfg = OPP_CONFIG[OPPORTUNITY_AVOID]
    st.markdown(
        f'<div style="padding:0.75rem 1rem;background:{cfg["bg"]};border:2px solid {cfg["border"]};'
        f'border-radius:0.5rem;margin-bottom:0.5rem;">'
        f'<span style="font-size:1.15rem;">{cfg["icon"]}</span> '
        f'<span style="font-size:1.05rem;font-weight:700;color:{cfg["color"]};">'
        f'以下 {len(group)} 个资产不碰</span>'
        f'<span style="color:#666;margin-left:0.5rem;">未过门槛 / 走弱 / 深回撤</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for s in group:
        reasons = []
        if s.ret_12m is not None and s.ret_12m <= -5:
            reasons.append(f"12M {s.ret_12m:+.1f}%")
        if s.current_drawdown < -15:
            reasons.append("深度回撤")
        elif s.current_drawdown < -10:
            reasons.append("回撤较大")
        if s.ma_alignment == "空头排列":
            reasons.append("空头排列")
        tags = " ".join(reasons)

        st.markdown(
            f'<div style="padding:0.45rem 0.8rem;margin-bottom:0.3rem;border-radius:0.3rem;'
            f'border:1px solid {cfg["border"]};background:{cfg["bg"]};">'
            f'<span style="font-weight:600;color:{cfg["color"]};">{s.name}</span>'
            f'<span style="color:#999;font-size:0.85rem;margin-left:0.3rem;">{s.symbol}</span>'
            f'<span style="margin-left:0.5rem;color:#c62828;font-size:0.85rem;">{tags}</span>'
            f'<div style="font-size:0.88rem;color:{cfg["color"]};margin-top:0.15rem;">{s.summary}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ============================================================
# 完整评分表
# ============================================================

def render_full_table(scores: List[AssetScore]):
    """完整明细表（结论之后的附录）。"""
    rows = [_score_row(s) for s in scores]
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    with st.expander("  指标说明", expanded=False):
        st.markdown(
            f"- **12M%**: 过去 12 个月涨幅。>{ABS_MOMENTUM_MIN_PCT:.0f}% 才有资格买入（生产策略门槛）\n"
            f"- **过门槛**: 12M 涨幅是否超过 {ABS_MOMENTUM_MIN_PCT:.0f}%\n"
            f"- **动量排名**: 12M 涨幅在全宇宙中的排名，前 {TOP_N} 名可进组合\n"
            f"- **参考仓位**: 1/2 凯利估算，单票顶 20%；波动率只影响仓位，不淘汰标的\n"
            f"- **回撤%**: 当前价距历史高点的回撤{tip_icon(DRAWDOWN)}\n"
            f"- **波动率%**: 年化波动率{tip_icon(VOLATILITY)}\n"
            f"- **1月%/3月%**: 近 1 月/3 月收益{tip_icon(RETURN_1M)}"
        )
