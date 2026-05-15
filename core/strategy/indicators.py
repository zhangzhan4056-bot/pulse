"""技术指标计算模块

从 OHLCV 原始数据计算动量、趋势、风险三类指标。
纯函数，只依赖 pandas 和 numpy。
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.data.config import US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS


# 资产角色定义
CORE_ASSETS = ["SPY", "TLT"]  # 核心资产：长期持有
# 卫星资产 = 全部资产 - 核心资产（从 config 动态获取）
SATELLITE_ASSETS = [s for s in list(US_SYMBOLS.keys()) + list(CN_SYMBOLS.keys()) + list(GLOBAL_SYMBOLS.keys()) if s not in CORE_ASSETS]


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


def calc_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """ADX 平均趋向指数（趋势强度指标）

    ADX > 25: 趋势明确
    ADX < 20: 无趋势（震荡）
    20-25: 过渡区

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        period: 计算周期，默认 14

    Returns:
        ADX 序列
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # 方向运动
    plus_dm = high - prev_high
    minus_dm = prev_low - low

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    # ATR
    atr = calc_atr(high, low, close, period)

    # 平滑的方向指标
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

    # DX 和 ADX
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100 * (di_diff / di_sum)
    adx = dx.rolling(window=period).mean()

    return adx


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


# ============================================================
# 市场环境判断（Regime Detection）
# ============================================================


def detect_market_regime(
    spy_close: pd.Series,
    tlt_close: pd.Series,
    cl_close: pd.Series,
) -> Dict[str, str]:
    """判断当前市场环境

    基于 SPY/TLT/CL 的相对动量和趋势，判断市场处于哪种状态。

    逻辑：
    - 风险偏好（Risk-On）：SPY 动量强于 TLT，股市上涨
    - 避险（Risk-Off）：TLT 动量强于 SPY，资金流向债市
    - 滞胀担忧：CL 大涨 + SPY 走弱，通胀压力
    - 危机：SPY 和 CL 同时大跌

    Returns:
        {
            "regime": "风险偏好" / "避险" / "滞胀担忧" / "危机" / "震荡",
            "description": 状态描述,
            "spy_score": SPY 动量评分,
            "tlt_score": TLT 动量评分,
            "cl_score": CL 动量评分,
        }
    """
    spy_score = calc_momentum_score(spy_close) or 50
    tlt_score = calc_momentum_score(tlt_close) or 50
    cl_score = calc_momentum_score(cl_close) or 50

    spy_dd = calc_current_drawdown(spy_close)

    # 判断环境
    if spy_dd < -15:
        regime = "危机"
        description = "美股大幅回撤，市场恐慌，建议降低仓位"
    elif spy_score > tlt_score + 15 and spy_score > 60:
        regime = "风险偏好"
        description = "资金流向股市，风险资产表现强劲，可增加卫星仓位"
    elif tlt_score > spy_score + 15 and tlt_score > 55:
        regime = "避险"
        description = "资金流向债市，市场避险情绪升温，建议防守为主"
    elif cl_score > 70 and spy_score < 50:
        regime = "滞胀担忧"
        description = "油价走强但股市疲软，通胀压力上升，关注货币政策"
    elif abs(spy_score - tlt_score) < 10 and spy_score > 40 and spy_score < 60:
        regime = "震荡"
        description = "市场方向不明，股债相关性低，建议观望"
    else:
        regime = "中性"
        description = "市场无明显偏向，维持常规配置"

    return {
        "regime": regime,
        "description": description,
        "spy_score": round(spy_score, 1),
        "tlt_score": round(tlt_score, 1),
        "cl_score": round(cl_score, 1),
    }


# ============================================================
# 核心卫星策略
# ============================================================


def calc_core_satellite_allocation(
    regime: str,
    momentum_scores: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    """计算核心卫星配置建议

    根据市场环境和动量评分，给出具体配置比例。

    Args:
        regime: 市场环境（"风险偏好"/"避险"/"危机" 等）
        momentum_scores: {symbol: 动量评分}

    Returns:
        {
            "core": {"SPY": 40, "TLT": 30},      # 核心仓位
            "satellite": {"QQQ": 20, "CL": 10},   # 卫星仓位
            "cash": 0,                              # 现金比例
            "total": 100,
        }
    """
    # 基础配置
    if regime == "危机":
        # 危机模式：大幅减仓，保留现金
        core_pct = 40
        satellite_pct = 10
        cash_pct = 50
    elif regime == "避险":
        # 避险模式：核心偏债，卫星极少
        core_pct = 60
        satellite_pct = 10
        cash_pct = 30
    elif regime == "风险偏好":
        # 风险偏好：核心正常，卫星满配
        core_pct = 60
        satellite_pct = 35
        cash_pct = 5
    elif regime == "滞胀担忧":
        # 滞胀：核心偏债，卫星配商品
        core_pct = 50
        satellite_pct = 20
        cash_pct = 30
    else:
        # 中性/震荡：常规配置
        core_pct = 60
        satellite_pct = 25
        cash_pct = 15

    # 核心资产分配（SPY:TLT 比例根据环境调整）
    if regime in ["避险", "危机"]:
        # 避险时核心偏债
        spy_pct = core_pct * 0.3
        tlt_pct = core_pct * 0.7
    elif regime == "风险偏好":
        # 风险偏好时核心偏股
        spy_pct = core_pct * 0.65
        tlt_pct = core_pct * 0.35
    else:
        # 常规 50:50
        spy_pct = core_pct * 0.5
        tlt_pct = core_pct * 0.5

    # 卫星资产分配（按动量排名，取前 2-3 个）
    satellite_scores = {
        s: momentum_scores.get(s, 0) for s in SATELLITE_ASSETS
    }
    # 按动量排序
    ranked = sorted(satellite_scores.items(), key=lambda x: x[1], reverse=True)

    # 只配置动量 > 50 的卫星资产
    active_satellites = [(s, score) for s, score in ranked if score > 50]

    satellite_alloc = {}
    if active_satellites and satellite_pct > 0:
        # 按动量加权分配
        total_score = sum(score for _, score in active_satellites)
        for symbol, score in active_satellites:
            weight = score / total_score
            alloc = round(satellite_pct * weight, 1)
            satellite_alloc[symbol] = alloc

    return {
        "core": {
            "SPY": round(spy_pct, 1),
            "TLT": round(tlt_pct, 1),
        },
        "satellite": satellite_alloc,
        "cash": cash_pct,
        "total": 100,
    }


def generate_rotation_signals(
    momentum_scores: Dict[str, float],
    prev_scores: Optional[Dict[str, float]] = None,
) -> List[Dict[str, str]]:
    """生成卫星轮动信号

    当卫星资产的动量排名发生变化，或穿越阈值时触发信号。

    Args:
        momentum_scores: 当前动量评分
        prev_scores: 上期动量评分（用于检测变化）

    Returns:
        信号列表，每项包含：
        {
            "action": "买入" / "卖出" / "关注",
            "symbol": 资产代码,
            "reason": 原因,
            "strength": "强" / "中" / "弱",
        }
    """
    signals = []

    # 获取卫星资产的动量
    satellite_scores = {
        s: momentum_scores.get(s, 0) for s in SATELLITE_ASSETS
    }

    # 按动量排序
    ranked = sorted(satellite_scores.items(), key=lambda x: x[1], reverse=True)

    # 检查穿越阈值的信号
    for symbol, score in ranked:
        if score >= 70:
            signals.append({
                "action": "买入",
                "symbol": symbol,
                "reason": f"动量强劲（{score:.0f}分），趋势向好",
                "strength": "强" if score >= 80 else "中",
            })
        elif score >= 60:
            signals.append({
                "action": "关注",
                "symbol": symbol,
                "reason": f"动量偏强（{score:.0f}分），可小仓位试探",
                "strength": "中",
            })
        elif score <= 30:
            signals.append({
                "action": "卖出",
                "symbol": symbol,
                "reason": f"动量疲弱（{score:.0f}分），趋势向下",
                "strength": "强" if score <= 20 else "中",
            })
        elif score <= 40:
            signals.append({
                "action": "关注",
                "symbol": symbol,
                "reason": f"动量偏弱（{score:.0f}分），注意风险",
                "strength": "弱",
            })

    # 如果有上期数据，检测排名变化
    if prev_scores:
        prev_ranked = sorted(
            prev_scores.items(), key=lambda x: x[1], reverse=True
        )
        curr_ranked = ranked

        prev_top = [s for s, _ in prev_ranked[:2]]
        curr_top = [s for s, _ in curr_ranked[:2]]

        # 检测新的领头资产
        for symbol in curr_top:
            if symbol not in prev_top:
                curr_score = momentum_scores.get(symbol, 0)
                if curr_score > 60:
                    signals.append({
                        "action": "轮动",
                        "symbol": symbol,
                        "reason": f"动量排名上升，进入卫星仓位候选",
                        "strength": "中",
                    })

    # 按强度排序
    strength_order = {"强": 0, "中": 1, "弱": 2}
    signals.sort(key=lambda x: strength_order.get(x["strength"], 99))

    return signals


# ============================================================
# CVaR / 尾部风险
# ============================================================


def calc_cvar(close: pd.Series, window: int = 252, alpha: float = 0.05) -> float:
    """计算条件风险价值 (CVaR / Expected Shortfall)

    CVaR = 尾部 alpha 分位数以下损失的期望值。
    比最大回撤更稳健，比波动率更关注极端损失。

    Args:
        close: 收盘价序列
        window: 计算窗口，默认 252 个交易日
        alpha: 尾部概率，默认 5%（最差 5% 的平均损失）

    Returns:
        年化 CVaR（正值，如 15.0 表示尾部 5% 的平均损失为 15%）
    """
    returns = close.pct_change().dropna()
    if len(returns) < max(window * 0.8, 20):  # 允许 20% 数据缺失
        return 0.0
    recent = returns.tail(window)
    # VaR: alpha 分位数
    var = recent.quantile(alpha)
    # CVaR: 尾部低于 VaR 的收益的平均值
    tail = recent[recent <= var]
    if len(tail) == 0:
        return 0.0
    cvar = -tail.mean() * np.sqrt(252) * 100
    return round(cvar, 2)


def calc_expected_return(close: pd.Series, window: int = 252) -> float:
    """计算年化预期收益率（历史均值外推）

    Args:
        close: 收盘价序列
        window: 计算窗口

    Returns:
        年化预期收益率（百分比）
    """
    returns = close.pct_change().dropna()
    if len(returns) < window:
        return 0.0
    mean_daily = returns.tail(window).mean()
    annual = mean_daily * 252 * 100
    return round(annual, 2)


def calc_regime_score(spy_close: pd.Series) -> Dict[str, float]:
    """计算 GEM 宏观状态综合评分

    基于三个维度：
    1. 趋势：SPY 价格 vs 200 日均线
    2. 动量：12 个月收益率
    3. 波动率：当前波动率 vs 历史中位数

    Returns:
        {
            "trend": 趋势信号 (1=看涨, -1=看跌, 0=中性),
            "momentum": 12 个月收益率 (%),
            "vol_regime": 波动率状态 ("低波" / "正常" / "高波"),
            "composite": 综合评分 (0-100),
        }
    """
    if len(spy_close) < 200:
        return {"trend": 0, "momentum": 0.0, "vol_regime": "正常", "composite": 50.0}

    # 趋势：价格 vs 200 日均线
    ma200 = spy_close.rolling(200).mean().iloc[-1]
    price = spy_close.iloc[-1]
    trend = 1 if price > ma200 else -1

    # 动量：12 个月收益率
    if len(spy_close) >= 252:
        momentum = (spy_close.iloc[-1] / spy_close.iloc[-252] - 1) * 100
    else:
        momentum = 0.0

    # 波动率状态
    returns = spy_close.pct_change().dropna()
    if len(returns) >= 60:
        current_vol = returns.tail(20).std() * np.sqrt(252) * 100
        hist_vol = returns.rolling(60).std() * np.sqrt(252) * 100
        median_vol = hist_vol.median()
        if current_vol > median_vol * 1.5:
            vol_regime = "高波"
        elif current_vol < median_vol * 0.7:
            vol_regime = "低波"
        else:
            vol_regime = "正常"
    else:
        vol_regime = "正常"
        current_vol = 0

    # 综合评分
    score = 50  # 基准
    score += trend * 15  # 趋势 ±15
    # 动量映射
    if momentum > 20:
        score += 20
    elif momentum > 10:
        score += 10
    elif momentum > 0:
        score += 5
    elif momentum > -10:
        score -= 10
    else:
        score -= 20
    # 波动率
    if vol_regime == "高波":
        score -= 15
    elif vol_regime == "低波":
        score += 5

    return {
        "trend": trend,
        "momentum": round(momentum, 2),
        "vol_regime": vol_regime,
        "composite": max(0, min(100, round(score, 1))),
    }
