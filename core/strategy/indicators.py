"""技术指标计算模块

从 OHLCV 原始数据计算动量、趋势、风险三类指标。
纯函数，只依赖 pandas 和 numpy。
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ============================================================
# 动量指标
# ============================================================


def calc_returns(close: pd.Series, periods: List[int]) -> pd.DataFrame:
    """计算多周期收益率

    Args:
        close: 收盘价序列
        periods: 周期列表，如 [21, 63, 126, 252]（约 1/3/6/12 个月）

    Returns:
        DataFrame，列名为 "1M", "3M" 等，值为收益率
    """
    result = {}
    for p in periods:
        label = f"{p // 21}M" if p >= 21 else f"{p}D"
        if len(close) > p:
            ret = (close.iloc[-1] / close.iloc[-p - 1] - 1) * 100
            result[label] = round(ret, 2)
        else:
            result[label] = None
    return pd.DataFrame([result])


def calc_momentum_score(close: pd.Series) -> Optional[float]:
    """综合动量评分

    加权公式：1M×0.2 + 3M×0.3 + 6M×0.3 + 12M×0.2
    匹配 2 月-3 年持仓周期。

    Returns:
        0-100 分，None 表示数据完全不足
    """
    periods = [21, 63, 126, 252]
    weights = [0.2, 0.3, 0.3, 0.2]

    # 计算可用的收益率和对应权重
    available_returns = []
    available_weights = []
    for p, w in zip(periods, weights):
        if len(close) > p:
            ret = (close.iloc[-1] / close.iloc[-p - 1] - 1) * 100
            available_returns.append(ret)
            available_weights.append(w)

    # 至少需要 1 个周期
    if not available_returns:
        return None

    # 重新归一化权重
    total_weight = sum(available_weights)
    normalized_weights = [w / total_weight for w in available_weights]

    # 加权收益率
    weighted_return = sum(r * w for r, w in zip(available_returns, normalized_weights))

    # 映射到 0-100 分
    # 经验值：-20% 到 +20% 的收益率映射到 0-100
    score = (weighted_return + 20) / 40 * 100
    return max(0, min(100, round(score, 1)))


# ============================================================
# 趋势指标
# ============================================================


def calc_sma(close: pd.Series, window: int) -> pd.Series:
    """简单移动平均"""
    return close.rolling(window=window).mean()


def calc_ema(close: pd.Series, window: int) -> pd.Series:
    """指数移动平均"""
    return close.ewm(span=window, adjust=False).mean()


def calc_ma_alignment(close: pd.Series) -> str:
    """均线排列状态

    判断 MA20, MA50, MA200 的相对位置。

    Returns:
        "多头排列" / "空头排列" / "交织"
    """
    if len(close) < 200:
        return "数据不足"

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]

    if ma20 > ma50 > ma200:
        return "多头排列"
    elif ma20 < ma50 < ma200:
        return "空头排列"
    else:
        return "交织"


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 相对强弱指数

    Args:
        close: 收盘价序列
        period: 计算周期，默认 14

    Returns:
        RSI 序列
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_rsi_status(rsi_value: float) -> str:
    """RSI 状态判断

    Returns:
        "超买" / "超卖" / "中性"
    """
    if rsi_value >= 70:
        return "超买"
    elif rsi_value <= 30:
        return "超卖"
    return "中性"


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 指标

    Args:
        close: 收盘价序列
        fast: 快线周期，默认 12
        slow: 慢线周期，默认 26
        signal: 信号线周期，默认 9

    Returns:
        (dif, dea, histogram) 三元组
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    histogram = (dif - dea) * 2

    return dif, dea, histogram


def calc_macd_signal(dif: pd.Series, dea: pd.Series) -> str:
    """MACD 信号判断

    Returns:
        "金叉" / "死叉" / "无信号"
    """
    if len(dif) < 2:
        return "无信号"

    # 当前和前一周期的差值
    curr_diff = dif.iloc[-1] - dea.iloc[-1]
    prev_diff = dif.iloc[-2] - dea.iloc[-2]

    if prev_diff <= 0 and curr_diff > 0:
        return "金叉"
    elif prev_diff >= 0 and curr_diff < 0:
        return "死叉"
    return "无信号"


# ============================================================
# 风险指标
# ============================================================


def calc_drawdown(close: pd.Series) -> pd.Series:
    """计算回撤序列

    回撤 = (当前价格 - 历史最高) / 历史最高

    Returns:
        回撤序列（负值，如 -0.05 表示回撤 5%）
    """
    peak = close.cummax()
    drawdown = (close - peak) / peak
    return drawdown


def calc_max_drawdown(close: pd.Series) -> float:
    """最大回撤

    Returns:
        最大回撤（负值，如 -0.15 表示最大回撤 15%）
    """
    dd = calc_drawdown(close)
    return round(dd.min() * 100, 2)


def calc_current_drawdown(close: pd.Series) -> float:
    """当前回撤

    Returns:
        当前回撤（负值，如 -3.2 表示回撤 3.2%）
    """
    dd = calc_drawdown(close)
    return round(dd.iloc[-1] * 100, 2)


def calc_volatility(close: pd.Series, window: int = 20) -> float:
    """年化波动率

    Args:
        close: 收盘价序列
        window: 计算窗口，默认 20 个交易日

    Returns:
        年化波动率（百分比）
    """
    returns = close.pct_change().dropna()
    if len(returns) < window:
        return 0.0
    vol = returns.rolling(window=window).std().iloc[-1]
    annual_vol = vol * np.sqrt(252) * 100
    return round(annual_vol, 2)


def calc_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """ATR 平均真实波幅

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        period: 计算周期，默认 14

    Returns:
        ATR 序列
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


# ============================================================
# 相关性
# ============================================================


def calc_correlation_matrix(
    data: Dict[str, pd.DataFrame],
    column: str = "close",
    window: Optional[int] = None,
) -> pd.DataFrame:
    """计算资产相关性矩阵

    Args:
        data: {symbol: DataFrame}，每个 DataFrame 包含 date 和指定列
        column: 用于计算相关的列名
        window: 滚动窗口（None 表示使用全部数据）

    Returns:
        相关性矩阵
    """
    # 提取各资产的收盘价
    prices = {}
    for symbol, df in data.items():
        if not df.empty and column in df.columns:
            prices[symbol] = df.set_index("date")[column]

    if not prices:
        return pd.DataFrame()

    price_df = pd.DataFrame(prices)

    if window:
        corr = price_df.tail(window).corr()
    else:
        corr = price_df.corr()

    return corr.round(2)
