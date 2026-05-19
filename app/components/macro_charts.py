"""宏观观测图表组件"""

from typing import Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core.data.config import MACRO_INDICATORS, MACRO_DISPLAY_ORDER, MACRO_COLORS


def create_macro_indicator_scatter(
    matrix: pd.DataFrame,
    current_month: pd.Timestamp,
    similar_months: pd.DataFrame,
    x_indicator: str = "cpi_yoy",
    y_indicator: str = "treasury_10y",
) -> go.Figure:
    """创建宏观指标二维散点图（用户选择 X/Y 轴指标）"""
    if matrix.empty or x_indicator not in matrix.columns or y_indicator not in matrix.columns:
        return go.Figure()

    fig = go.Figure()

    # 按年份着色
    for year in sorted(matrix.index.year.unique()):
        mask = matrix.index.year == year
        subset = matrix[mask].dropna(subset=[x_indicator, y_indicator])
        if subset.empty:
            continue
        fig.add_trace(go.Scatter(
            x=subset[x_indicator],
            y=subset[y_indicator],
            mode="markers",
            marker=dict(size=7, opacity=0.4),
            name=str(year),
            text=subset.index.strftime("%Y-%m"),
            hovertemplate=(
                "%{text}<br>"
                f"{MACRO_INDICATORS[x_indicator]['name']}: %{{x:.2f}}%<br>"
                f"{MACRO_INDICATORS[y_indicator]['name']}: %{{y:.2f}}%<extra></extra>"
            ),
        ))

    # 相似月份（橙色圆圈）
    if not similar_months.empty:
        similar_data = matrix[matrix.index.isin(similar_months["month"])].dropna(
            subset=[x_indicator, y_indicator]
        )
        if not similar_data.empty:
            fig.add_trace(go.Scatter(
                x=similar_data[x_indicator],
                y=similar_data[y_indicator],
                mode="markers",
                marker=dict(size=14, color="orange", symbol="circle-open",
                            line=dict(width=3)),
                name="相似月份",
                text=similar_data.index.strftime("%Y-%m"),
                hovertemplate="相似: %{text}<extra></extra>",
            ))

    # 当前月份（红色星）
    if current_month in matrix.index:
        cur = matrix.loc[[current_month]]
        fig.add_trace(go.Scatter(
            x=cur[x_indicator],
            y=cur[y_indicator],
            mode="markers+text",
            marker=dict(size=18, color="red", symbol="star"),
            text=["当前"],
            textposition="top center",
            name="当前",
        ))

    x_name = MACRO_INDICATORS.get(x_indicator, {}).get("name", x_indicator)
    y_name = MACRO_INDICATORS.get(y_indicator, {}).get("name", y_indicator)

    fig.update_layout(
        title=f"宏观环境散点: {x_name} vs {y_name}",
        xaxis_title=f"{x_name} (%)",
        yaxis_title=f"{y_name} (%)",
        height=520,
        template="plotly_white",
        hovermode="closest",
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.12,
            xanchor="center", x=0.5,
            font=dict(size=10),
        ),
        margin=dict(t=60, b=100),
    )

    return fig


# 历史重大事件（用于时间线背景标注）
# y_row: 标签行号（0/1 交替），避免密集事件重叠
_MAJOR_EVENTS = [
    # (start, end, label, color, y_row)
    ("2000-03", "2002-10", "互联网泡沫", "rgba(239,83,80,0.10)", 0),
    ("2007-10", "2009-03", "金融危机", "rgba(239,83,80,0.14)", 1),
    ("2010-04", "2012-06", "欧债危机", "rgba(255,152,0,0.10)", 0),
    ("2015-06", "2016-02", "熔断/油价", "rgba(255,152,0,0.08)", 1),
    ("2018-10", "2018-12", "贸易战", "rgba(255,152,0,0.10)", 0),
    ("2020-02", "2020-04", "新冠暴跌", "rgba(239,83,80,0.14)", 1),
    ("2022-01", "2022-10", "通胀加息", "rgba(156,39,176,0.10)", 0),
]


def create_macro_timeline(
    ranked: pd.DataFrame,
    current_month: pd.Timestamp,
    similar_months: pd.DataFrame,
    spy_monthly: pd.Series = None,
    qqq_monthly: pd.Series = None,
) -> go.Figure:
    """创建宏观指标百分位走势图（带历史事件标注 + SPY/QQQ 副轴）"""
    fig = go.Figure()

    # 标签交替放在两行，避免重叠
    _Y_ROW = {0: 1.05, 1: 1.14}

    # ── 1. 历史事件背景色带 + 标签 ──
    for start, end, label, color, y_row in _MAJOR_EVENTS:
        fig.add_shape(
            type="rect",
            x0=pd.Timestamp(start), x1=pd.Timestamp(end),
            y0=0, y1=1, yref="paper",
            fillcolor=color,
            line_width=0,
            layer="below",
        )
        mid = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
        fig.add_annotation(
            x=mid, y=_Y_ROW[y_row], yref="paper",
            text=label, showarrow=False,
            font=dict(size=12, color="#555", family="Microsoft YaHei"),
            yanchor="bottom",
            xanchor="center",
        )

    # ── 2. 百分位走势线 ──
    for indicator in MACRO_DISPLAY_ORDER:
        if indicator not in ranked.columns:
            continue
        color = MACRO_COLORS.get(indicator, "#999")
        name = MACRO_INDICATORS.get(indicator, {}).get("name", indicator)
        fig.add_trace(go.Scatter(
            x=ranked.index,
            y=ranked[indicator],
            mode="lines",
            name=name,
            line=dict(color=color, width=2),
            hovertemplate=f"{name}: %{{y:.0f}}%<extra></extra>",
        ))

    # ── 2b. SPY/QQQ 价格（副轴） ──
    if spy_monthly is not None and not spy_monthly.empty:
        fig.add_trace(go.Scatter(
            x=spy_monthly.index,
            y=spy_monthly.values,
            mode="lines",
            name="SPY",
            line=dict(color="#1565c0", width=1.5, dash="dot"),
            yaxis="y2",
            hovertemplate="SPY $%{y:,.0f}<extra></extra>",
        ))
    if qqq_monthly is not None and not qqq_monthly.empty:
        fig.add_trace(go.Scatter(
            x=qqq_monthly.index,
            y=qqq_monthly.values,
            mode="lines",
            name="QQQ",
            line=dict(color="#00897b", width=1.5, dash="dot"),
            yaxis="y2",
            hovertemplate="QQQ $%{y:,.0f}<extra></extra>",
        ))

    # ── 3. 相似月份标注 ──
    if not similar_months.empty:
        for _, row in similar_months.iterrows():
            fig.add_shape(
                type="line",
                x0=row["month"], x1=row["month"],
                y0=0, y1=1, yref="paper",
                line=dict(dash="dash", color="#ff9800", width=1.5),
                opacity=0.7,
            )
        first_sim = similar_months.iloc[0]["month"]
        fig.add_annotation(
            x=first_sim, y=1.01, yref="paper",
            text=f"▲ 相似月 {first_sim.strftime('%Y-%m')}",
            showarrow=False,
            font=dict(size=12, color="#e65100"),
            yanchor="bottom",
        )

    # ── 4. 当前月份 ──
    fig.add_shape(
        type="line",
        x0=current_month, x1=current_month,
        y0=0, y1=1, yref="paper",
        line=dict(color="#d32f2f", width=2.5),
    )
    fig.add_annotation(
        x=current_month, y=1.01, yref="paper",
        text=f"● 当前 {current_month.strftime('%Y-%m')}",
        showarrow=False,
        font=dict(color="#d32f2f", size=13, family="Microsoft YaHei"),
        yanchor="bottom",
    )

    # ── 5. 50% 参考线 ──
    fig.add_hline(
        y=50, line_dash="dash", line_color="#bbb", opacity=0.4,
        annotation_text="中位数", annotation_position="bottom right",
        annotation_font_size=11, annotation_font_color="#999",
    )

    # ── 6. 高低警戒区间 ──
    fig.add_hrect(
        y0=0, y1=20,
        fillcolor="rgba(33,150,243,0.05)", line_width=0, layer="below",
    )
    fig.add_hrect(
        y0=80, y1=100,
        fillcolor="rgba(239,83,80,0.05)", line_width=0, layer="below",
    )
    fig.add_annotation(
        x=1.01, y=10, xref="paper", yref="y",
        text="低位区", showarrow=False,
        font=dict(size=11, color="#2196f3"),
        textangle=-90, xanchor="left",
    )
    fig.add_annotation(
        x=1.01, y=90, xref="paper", yref="y",
        text="高位区", showarrow=False,
        font=dict(size=11, color="#ef5350"),
        textangle=-90, xanchor="left",
    )

    # ── 布局 ──
    fig.update_layout(
        title=dict(
            text="宏观指标百分位走势 · 历史事件对照",
            font=dict(size=16),
        ),
        xaxis_title="",
        yaxis=dict(
            title=dict(text="百分位 (%)", font=dict(size=12)),
            range=[0, 100],
        ),
        yaxis2=dict(
            title=dict(text="价格 ($)", font=dict(size=12)),
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        height=325,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.15,
            xanchor="center", x=0.5,
            font=dict(size=12),
        ),
        margin=dict(t=90, b=70, r=60),
    )

    return fig


def render_macro_value_cards(analysis: Dict) -> None:
    """渲染当前宏观指标值卡片（含百分位排名）"""
    import streamlit as st

    current_values = analysis.get("current_values")
    current_ranks = analysis.get("current_ranks")
    if current_values is None:
        return

    cols = st.columns(len(MACRO_DISPLAY_ORDER))
    for col, indicator in zip(cols, MACRO_DISPLAY_ORDER):
        config = MACRO_INDICATORS.get(indicator, {})
        value = current_values.get(indicator)
        rank = current_ranks.get(indicator) if current_ranks is not None else None

        if value is None or pd.isna(value):
            with col:
                st.metric(label=config.get("name", indicator), value="N/A")
            continue

        # 与上月对比
        matrix = analysis.get("matrix")
        delta = None
        if matrix is not None and indicator in matrix.columns:
            idx = matrix.index.get_loc(analysis["current_month"])
            if idx > 0:
                prev_value = matrix.iloc[idx - 1].get(indicator)
                if prev_value is not None and not pd.isna(prev_value):
                    delta = round(value - prev_value, 2)

        # 百分位描述
        rank_text = ""
        if rank is not None and not pd.isna(rank):
            if rank >= 80:
                rank_text = "↑ 高位"
            elif rank >= 60:
                rank_text = "↗ 偏高"
            elif rank >= 40:
                rank_text = "→ 中位"
            elif rank >= 20:
                rank_text = "↙ 偏低"
            else:
                rank_text = "↓ 低位"

        with col:
            st.metric(
                label=config.get("name", indicator),
                value=f"{value:.2f}{config.get('unit', '')}",
                delta=f"{delta:+.2f}" if delta is not None else None,
            )
            if rank is not None and not pd.isna(rank):
                st.caption(f"P{rank:.0f} {rank_text}")
