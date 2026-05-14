"""图表组件 - Plotly 图表封装"""

from typing import Optional, Dict

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_candlestick_chart(
    df: pd.DataFrame,
    symbol: str,
    title: Optional[str] = None,
) -> go.Figure:
    """创建 K 线图

    Args:
        df: DataFrame，必须包含 date, open, high, low, close, volume
        symbol: 资产代码
        title: 图表标题

    Returns:
        Plotly Figure
    """
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
    )

    # K线
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
        ),
        row=1,
        col=1,
    )

    # 成交量
    colors = [
        "#ef5350" if close >= open else "#26a69a"
        for close, open in zip(df["close"], df["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["volume"],
            marker_color=colors,
            name="成交量",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        title=title or f"{symbol} K线图",
        xaxis_rangeslider_visible=False,
        height=600,
        template="plotly_white",
        xaxis2_title="日期",
        yaxis_title="价格",
        yaxis2_title="成交量",
    )

    return fig


def create_line_chart(
    df: pd.DataFrame,
    symbol: str,
    title: Optional[str] = None,
    yaxis_title: str = "价格",
) -> go.Figure:
    """创建折线图

    Args:
        df: DataFrame，必须包含 date 和 close
        symbol: 资产代码
        title: 图表标题
        yaxis_title: Y轴标题

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["close"],
            mode="lines",
            name=symbol,
            line=dict(width=2),
        )
    )

    fig.update_layout(
        title=title or f"{symbol} 走势",
        xaxis_title="日期",
        yaxis_title=yaxis_title,
        height=400,
        template="plotly_white",
        hovermode="x unified",
    )

    return fig


def create_multi_line_chart(
    data: Dict[str, pd.DataFrame],
    title: str = "资产走势对比",
    normalize: bool = True,
) -> go.Figure:
    """创建多资产对比折线图

    Args:
        data: {symbol: DataFrame}，每个 DataFrame 必须包含 date 和 close
        title: 图表标题
        normalize: 是否标准化（以第一个数据点为基准）

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    for symbol, df in data.items():
        if df.empty:
            continue

        values = df["close"].copy()
        if normalize:
            # 标准化为百分比变化
            first_value = values.iloc[0]
            values = (values / first_value - 1) * 100

        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=values,
                mode="lines",
                name=symbol,
                line=dict(width=2),
            )
        )

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="涨跌幅 (%)" if normalize else "价格",
        height=500,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
    )

    return fig
