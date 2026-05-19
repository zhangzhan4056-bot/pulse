"""全资产多维评分

对每个资产从三个维度打分：
1. 趋势机会 — 适合趋势跟踪/动量类策略
2. 均值回归机会 — 适合均值回归策略
3. 风险评估 — 需要规避或减仓

评分逻辑对应 P3 的 11 种策略：
- 趋势分数高 → 趋势跟踪、双动量、动量+波动率过滤
- 均值回归分数高 → 均值回归
- 风险分数高 → 风险平价、最小相关性（需要回避）
- 回撤控制、尾部风险平价、回撤约束 → 看回撤和波动率
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from core.strategy.indicators import (
    calc_momentum_score,
    calc_ma_alignment,
    calc_macd,
    calc_macd_signal,
    calc_rsi,
    calc_rsi_status,
    calc_current_drawdown,
    calc_max_drawdown,
    calc_volatility,
    calc_returns,
)
from core.data.config import US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS


# 机会类型
OPPORTUNITY_TREND_BUY = "trend_buy"       # 趋势买入
OPPORTUNITY_TREND_WATCH = "trend_watch"    # 趋势关注
OPPORTUNITY_MEAN_REVERSION = "mean_reversion"  # 均值回归
OPPORTUNITY_AVOID = "avoid"               # 规避
OPPORTUNITY_NEUTRAL = "neutral"           # 中性


@dataclass
class AssetScore:
    """单个资产的多维评分"""
    symbol: str
    name: str
    # 三个维度的分数 (0-100)
    trend_score: float = 0.0
    reversion_score: float = 0.0
    risk_score: float = 0.0
    # 综合机会类型
    opportunity: str = OPPORTUNITY_NEUTRAL
    # 关键指标快照
    momentum: Optional[float] = None       # 综合动量 0-100
    ma_alignment: str = ""                 # 均线排列
    macd_signal: str = ""                  # MACD 信号
    rsi: Optional[float] = None            # RSI 值
    rsi_status: str = ""                   # RSI 状态
    current_drawdown: float = 0.0          # 当前回撤 %
    max_drawdown: float = 0.0              # 最大回撤 %
    volatility: float = 0.0               # 年化波动率 %
    ret_1m: Optional[float] = None         # 1 月收益 %
    ret_3m: Optional[float] = None         # 3 月收益 %
    # 对应策略标签
    strategies: List[str] = field(default_factory=list)
    # 一句话说明
    summary: str = ""


def score_all_assets(data: Dict[str, pd.DataFrame]) -> List[AssetScore]:
    """对所有资产进行多维评分

    Args:
        data: {symbol: DataFrame} 资产数据

    Returns:
        AssetScore 列表，按综合机会排序
    """
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    scores = []

    for symbol, df in data.items():
        if df.empty or len(df) < 30:
            continue

        close = df["close"]
        name = all_symbols.get(symbol, symbol)

        score = AssetScore(symbol=symbol, name=name)

        # ---- 基础指标 ----
        score.momentum = calc_momentum_score(close)
        score.ma_alignment = calc_ma_alignment(close)
        dif, dea, hist = calc_macd(close)
        score.macd_signal = calc_macd_signal(dif, dea)
        rsi_series = calc_rsi(close)
        score.rsi = round(rsi_series.iloc[-1], 1) if not rsi_series.empty else None
        score.rsi_status = calc_rsi_status(score.rsi) if score.rsi else ""
        score.current_drawdown = calc_current_drawdown(close)
        score.max_drawdown = calc_max_drawdown(close)
        score.volatility = calc_volatility(close)

        returns = calc_returns(close, [21, 63])
        ret_dict = returns.to_dict("records")[0]
        score.ret_1m = ret_dict.get("1M")
        score.ret_3m = ret_dict.get("3M")

        # ---- 趋势评分 (0-100) ----
        score.trend_score = _calc_trend_score(score)

        # ---- 均值回归评分 (0-100) ----
        score.reversion_score = _calc_reversion_score(score)

        # ---- 风险评分 (0-100，越高越危险) ----
        score.risk_score = _calc_risk_score(score)

        # ---- 综合机会判定 ----
        score.opportunity = _classify_opportunity(score)
        score.strategies = _map_strategies(score.opportunity)
        score.summary = _generate_summary(score)

        scores.append(score)

    # 排序：trend_buy > mean_reversion > trend_watch > neutral > avoid
    opp_order = {
        OPPORTUNITY_TREND_BUY: 0,
        OPPORTUNITY_MEAN_REVERSION: 1,
        OPPORTUNITY_TREND_WATCH: 2,
        OPPORTUNITY_NEUTRAL: 3,
        OPPORTUNITY_AVOID: 4,
    }
    scores.sort(key=lambda s: (opp_order.get(s.opportunity, 9), -s.trend_score))

    return scores


def _calc_trend_score(s: AssetScore) -> float:
    """趋势评分：动量 + 均线 + MACD + 波动率调整"""
    score = 0.0

    # 动量 (0-40 分)
    if s.momentum is not None:
        score += s.momentum * 0.4

    # 均线排列 (0-25 分)
    ma_points = {"多头排列": 25, "交织": 10, "空头排列": 0}
    score += ma_points.get(s.ma_alignment, 5)

    # MACD (0-20 分)
    macd_points = {"金叉": 20, "无信号": 5, "死叉": 0}
    score += macd_points.get(s.macd_signal, 5)

    # 波动率调整：高波动扣分 (0-15 分)
    if s.volatility > 30:
        score += 0
    elif s.volatility > 20:
        score += 5
    elif s.volatility > 10:
        score += 10
    else:
        score += 15

    return min(100, max(0, round(score, 1)))


def _calc_reversion_score(s: AssetScore) -> float:
    """均值回归评分：主要看 RSI"""
    if s.rsi is None:
        return 0.0

    rsi = s.rsi
    if rsi <= 20:
        return 95  # 极度超卖
    elif rsi <= 30:
        return 80  # 超卖
    elif rsi <= 40:
        return 55  # 偏低
    elif rsi <= 50:
        return 35  # 中性偏低
    elif rsi <= 60:
        return 20  # 中性
    elif rsi <= 70:
        return 10  # 偏高
    else:
        return 0   # 超买，不适合抄底


def _calc_risk_score(s: AssetScore) -> float:
    """风险评分：回撤 + 空头排列 + 高波动"""
    score = 0.0

    # 当前回撤 (0-40 分)
    dd = s.current_drawdown
    if dd < -20:
        score += 40
    elif dd < -15:
        score += 30
    elif dd < -10:
        score += 20
    elif dd < -5:
        score += 10

    # 空头排列 (0-30 分)
    if s.ma_alignment == "空头排列":
        score += 30
    elif s.ma_alignment == "交织":
        score += 10

    # 高波动 (0-30 分)
    if s.volatility > 35:
        score += 30
    elif s.volatility > 25:
        score += 20
    elif s.volatility > 15:
        score += 10

    return min(100, max(0, round(score, 1)))


def _classify_opportunity(s: AssetScore) -> str:
    """综合判定机会类型"""
    # 风险优先：高风险直接标规避
    if s.risk_score >= 60:
        return OPPORTUNITY_AVOID

    # 趋势买入：强动量 + 多头排列 + MACD 确认
    if s.trend_score >= 70 and s.ma_alignment == "多头排列":
        return OPPORTUNITY_TREND_BUY

    # 趋势关注：动量不错但没完全确认
    if s.trend_score >= 50:
        return OPPORTUNITY_TREND_WATCH

    # 均值回归：超卖
    if s.reversion_score >= 70:
        return OPPORTUNITY_MEAN_REVERSION

    return OPPORTUNITY_NEUTRAL


def _map_strategies(opportunity: str) -> List[str]:
    """映射到 P3 策略"""
    mapping = {
        OPPORTUNITY_TREND_BUY: [
            "趋势跟踪", "双动量", "动量+波动率过滤",
        ],
        OPPORTUNITY_TREND_WATCH: [
            "双动量", "回撤控制", "尾部风险平价",
        ],
        OPPORTUNITY_MEAN_REVERSION: [
            "均值回归",
        ],
        OPPORTUNITY_AVOID: [
            "风险平价", "最小相关性",
        ],
        OPPORTUNITY_NEUTRAL: [],
    }
    return mapping.get(opportunity, [])


def _generate_summary(s: AssetScore) -> str:
    """一句话说明"""
    parts = []

    if s.opportunity == OPPORTUNITY_TREND_BUY:
        parts.append(f"趋势强劲（动量{s.momentum:.0f}）")
        if s.ma_alignment == "多头排列":
            parts.append("均线多头")
        if s.macd_signal == "金叉":
            parts.append("MACD 金叉确认")
    elif s.opportunity == OPPORTUNITY_TREND_WATCH:
        parts.append(f"动量偏强（{s.momentum:.0f}）")
        if s.ma_alignment != "多头排列":
            parts.append(f"均线{s.ma_alignment}")
    elif s.opportunity == OPPORTUNITY_MEAN_REVERSION:
        parts.append(f"超卖区域（RSI {s.rsi:.0f}）")
        if s.current_drawdown < -10:
            parts.append(f"回撤{s.current_drawdown:.0f}%")
    elif s.opportunity == OPPORTUNITY_AVOID:
        if s.current_drawdown < -15:
            parts.append(f"深度回撤{s.current_drawdown:.0f}%")
        if s.ma_alignment == "空头排列":
            parts.append("均线空头")
        if s.volatility > 25:
            parts.append(f"高波动{s.volatility:.0f}%")
    else:
        parts.append("无明确信号")

    return "，".join(parts)
