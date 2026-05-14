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


def create_indicator_chart(
    df: pd.DataFrame,
    symbol: str,
    title: Optional[str] = None,
) -> go.Figure:
    """带技术指标的 K 线图（主图 + RSI 副图 + MACD 副图）

    Args:
        df: DataFrame，必须包含 date, open, high, low, close
        symbol: 资产代码
        title: 图表标题

    Returns:
        Plotly Figure
    """
    from core.strategy.indicators import calc_sma, calc_rsi, calc_macd

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.5, 0.25, 0.25],
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

    # 均线
    for window, color, name in [(20, "#ff9800", "MA20"), (50, "#2196f3", "MA50"), (200, "#9c27b0", "MA200")]:
        if len(df) >= window:
            ma = calc_sma(df["close"], window)
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=ma,
                    mode="lines",
                    name=name,
                    line=dict(color=color, width=1),
                ),
                row=1,
                col=1,
            )

    # RSI
    rsi = calc_rsi(df["close"])
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=rsi,
            mode="lines",
            name="RSI",
            line=dict(color="#607d8b", width=1.5),
        ),
        row=2,
        col=1,
    )
    # RSI 超买超卖线
    fig.add_hline(y=70, line_dash="dash", line_color="#ef5350", row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="#26a69a", row=2, col=1)

    # MACD
    dif, dea, histogram = calc_macd(df["close"])
    colors = ["#ef5350" if v >= 0 else "#26a69a" for v in histogram]
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=histogram,
            name="MACD柱",
            marker_color=colors,
        ),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=dif,
            mode="lines",
            name="DIF",
            line=dict(color="#2196f3", width=1.5),
        ),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=dea,
            mode="lines",
            name="DEA",
            line=dict(color="#ff9800", width=1.5),
        ),
        row=3,
        col=1,
    )

    fig.update_layout(
        title=title or f"{symbol} 技术分析",
        height=700,
        template="plotly_white",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
    )

    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="RSI", row=2, col=1, range=[0, 100])
    fig.update_yaxes(title_text="MACD", row=3, col=1)

    return fig


def create_correlation_heatmap(
    corr_matrix: pd.DataFrame,
    title: str = "资产相关性矩阵",
) -> go.Figure:
    """创建相关性热力图

    Args:
        corr_matrix: 相关性矩阵
        title: 图表标题

    Returns:
        Plotly Figure
    """
    fig = go.Figure(
        data=go.Heatmap(
            z=corr_matrix.values,
            x=corr_matrix.columns.tolist(),
            y=corr_matrix.index.tolist(),
            colorscale="RdBu_r",
            zmin=-1,
            zmax=1,
            text=corr_matrix.values.round(2).tolist(),
            texttemplate="%{text}",
            textfont=dict(size=12),
            hovertemplate="%{x} vs %{y}: %{z:.2f}<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        height=400,
        template="plotly_white",
    )

    return fig


def create_drawdown_chart(
    data: Dict[str, pd.Series],
    title: str = "回撤对比",
) -> go.Figure:
    """创建多资产回撤对比图

    Args:
        data: {symbol: 回撤序列}，回撤为负值（如 -0.05 表示 5%）
        title: 图表标题

    Returns:
        Plotly Figure
    """
    from core.strategy.indicators import calc_drawdown

    fig = go.Figure()

    for symbol, series in data.items():
        if isinstance(series, pd.DataFrame):
            # 如果传入的是 DataFrame，提取 close 列计算回撤
            dd = calc_drawdown(series["close"])
            dates = series["date"]
        else:
            dd = series
            dates = dd.index if hasattr(dd, "index") else range(len(dd))

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=dd * 100,
                mode="lines",
                name=symbol,
                line=dict(width=1.5),
            )
        )

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="回撤 (%)",
        height=400,
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
