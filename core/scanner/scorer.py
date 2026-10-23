"""全资产扫描 — 对齐生产策略「凯利·门槛8%」

核心口径（与 DualMomentumKellyFilterStrategy 一致）：
- 12 月涨幅 > 8% 才有资格买入，否则宁可现金/SHY
- 过门槛后按动量排序取 top5，单票凯利仓位顶 20%
- 波动率只影响仓位大小，不作为淘汰理由
- 均值回归不是本策略的机会轴，超卖只进「不碰」观察

分组：
- 过门槛候选：12M > 8%，可进入 top5
- 门槛附近：12M 在 0~8%，趋势不稳
- 不碰：12M ≤ 0 / 深回撤 / 空头排列
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
from core.strategy.kelly import kelly_weights, mu_from_momentum
from core.data.config import STRATEGY_UNIVERSE, CASH_PROXY

# 生产策略门槛（百分点），与 DualMomentumKellyFilterStrategy.abs_momentum_min_pct 一致
ABS_MOMENTUM_MIN_PCT = 8.0
TOP_N = 5
MAX_WEIGHT = 0.20
KELLY_K = 0.50

# 机会类型 — 字符串值保持兼容历史快照（trend_buy/trend_watch/avoid/neutral）
OPPORTUNITY_CANDIDATE = "trend_buy"       # 过门槛候选
OPPORTUNITY_NEAR = "trend_watch"          # 门槛附近
OPPORTUNITY_AVOID = "avoid"               # 不碰
OPPORTUNITY_NEUTRAL = "neutral"           # 中性（数据不足）

# 兼容旧导入名
OPPORTUNITY_TREND_BUY = OPPORTUNITY_CANDIDATE
OPPORTUNITY_TREND_WATCH = OPPORTUNITY_NEAR
OPPORTUNITY_MEAN_REVERSION = "mean_reversion"  # 旧值，只读历史用

# 旧值 → 新组（历史快照归一）
_OPP_NORMALIZE = {
    "mean_reversion": OPPORTUNITY_AVOID,
    "candidate": OPPORTUNITY_CANDIDATE,
    "near": OPPORTUNITY_NEAR,
}


@dataclass
class AssetScore:
    """单个资产的扫描结果（门槛 8% 口径）"""
    symbol: str
    name: str
    # 主字段
    ret_12m: Optional[float] = None        # 12 月涨幅 %
    passes_threshold: bool = False         # 12M > 8%
    momentum_rank: int = 0                 # 12M 动量排名（1=最强）
    kelly_weight: float = 0.0              # 估算凯利仓位 0~20%
    opportunity: str = OPPORTUNITY_NEUTRAL
    # 辅助指标
    momentum: Optional[float] = None       # 综合动量 0-100
    ma_alignment: str = ""
    macd_signal: str = ""
    rsi: Optional[float] = None
    rsi_status: str = ""
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    # 兼容旧字段（P4 复盘仍读）
    trend_score: float = 0.0
    reversion_score: float = 0.0
    risk_score: float = 0.0
    strategies: List[str] = field(default_factory=list)
    summary: str = ""


def score_all_assets(data: Dict[str, pd.DataFrame]) -> List[AssetScore]:
    """对所有资产按门槛 8% 口径扫描。"""
    all_symbols = STRATEGY_UNIVERSE
    scores = []

    for symbol, df in data.items():
        if df.empty or len(df) < 30:
            continue

        close = df["close"]
        name = all_symbols.get(symbol, symbol)
        score = AssetScore(symbol=symbol, name=name)

        # 12 月涨幅（生产策略主信号）
        if len(close) >= 252:
            score.ret_12m = round(float(close.iloc[-1] / close.iloc[-252] - 1.0) * 100, 2)
        elif len(close) >= 63:
            # 短历史降级用 3M 年化近似，标记为不可信
            score.ret_12m = round(float(close.iloc[-1] / close.iloc[-63] - 1.0) * 100, 2)

        score.passes_threshold = (
            score.ret_12m is not None and score.ret_12m > ABS_MOMENTUM_MIN_PCT
        )

        # 辅助指标
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

        # 兼容字段（P4 复盘仍在用）
        score.trend_score = _legacy_trend_score(score)
        score.reversion_score = _legacy_reversion_score(score)
        score.risk_score = _legacy_risk_score(score)

        scores.append(score)

    # 按 12M 动量排名
    ranked = sorted(
        scores,
        key=lambda s: (s.ret_12m if s.ret_12m is not None else -999),
        reverse=True,
    )
    for i, s in enumerate(ranked, 1):
        s.momentum_rank = i

    # 估算凯利仓位：过门槛的 top5 才有仓位，单票顶 20%
    _assign_kelly_weights(ranked)

    for s in scores:
        s.opportunity = _classify_opportunity(s)
        s.strategies = _map_strategies(s.opportunity)
        s.summary = _generate_summary(s)

    # 排序：过门槛 > 门槛附近 > 中性 > 不碰；组内按 12M 动量
    opp_order = {
        OPPORTUNITY_CANDIDATE: 0,
        OPPORTUNITY_NEAR: 1,
        OPPORTUNITY_NEUTRAL: 2,
        OPPORTUNITY_AVOID: 3,
    }
    scores.sort(key=lambda s: (
        opp_order.get(s.opportunity, 9),
        -(s.ret_12m if s.ret_12m is not None else -999),
    ))
    return scores


def _assign_kelly_weights(ranked: List[AssetScore]) -> None:
    """对过门槛 top5 用分数凯利估算仓位（与生产策略同公式）。"""
    candidates = [s for s in ranked if s.passes_threshold][:TOP_N]
    if not candidates:
        return

    mu_map = {}
    vol_map = {}
    for s in candidates:
        if s.ret_12m is None:
            continue
        mu_map[s.symbol] = mu_from_momentum(s.ret_12m)
        # 用波动率百分点转小数（与策略一致）
        vol = s.volatility
        if vol and vol > 0:
            vol_map[s.symbol] = vol / 100.0

    if not mu_map:
        return

    weights = kelly_weights(
        mu_map, vol_map,
        k=KELLY_K,
        max_weight=MAX_WEIGHT,
    )
    for s in candidates:
        s.kelly_weight = float(weights.get(s.symbol, 0.0))


def _classify_opportunity(s: AssetScore) -> str:
    """按门槛 8% 分组。均值回归不再是机会轴。"""
    if s.ret_12m is None:
        return OPPORTUNITY_NEUTRAL

    # 不碰：深回撤 + 空头，或 12M 明显为负
    if s.current_drawdown < -20 and s.ma_alignment == "空头排列":
        return OPPORTUNITY_AVOID
    if s.ret_12m <= -5:
        return OPPORTUNITY_AVOID

    # 过门槛候选
    if s.ret_12m > ABS_MOMENTUM_MIN_PCT:
        return OPPORTUNITY_CANDIDATE

    # 门槛附近：0 ~ 8%
    if s.ret_12m > 0:
        return OPPORTUNITY_NEAR

    return OPPORTUNITY_AVOID


def _map_strategies(opportunity: str) -> List[str]:
    """只标注仍在生产的策略。"""
    mapping = {
        OPPORTUNITY_CANDIDATE: ["凯利·门槛8%", "双动量×凯利"],
        OPPORTUNITY_NEAR: ["凯利·门槛8%"],
        OPPORTUNITY_AVOID: [],
        OPPORTUNITY_NEUTRAL: [],
    }
    return mapping.get(opportunity, [])


def _generate_summary(s: AssetScore) -> str:
    """一句话说明，口径对齐门槛 8%。"""
    if s.ret_12m is None:
        return "历史不足"

    if s.opportunity == OPPORTUNITY_CANDIDATE:
        parts = [f"12M {s.ret_12m:+.1f}% 过门槛"]
        if s.momentum_rank <= TOP_N:
            parts.append(f"动量第{s.momentum_rank}")
        if s.kelly_weight > 1e-4:
            parts.append(f"参考仓位 {s.kelly_weight:.0%}")
        return "，".join(parts)

    if s.opportunity == OPPORTUNITY_NEAR:
        gap = ABS_MOMENTUM_MIN_PCT - s.ret_12m
        return f"12M {s.ret_12m:+.1f}%，距门槛还差 {gap:.1f}pp，先不买"

    if s.opportunity == OPPORTUNITY_AVOID:
        parts = []
        if s.ret_12m <= -5:
            parts.append(f"12M {s.ret_12m:+.1f}% 走弱")
        if s.current_drawdown < -15:
            parts.append(f"回撤 {s.current_drawdown:.0f}%")
        if s.ma_alignment == "空头排列":
            parts.append("均线空头")
        if not parts:
            parts.append(f"12M {s.ret_12m:+.1f}% 未过门槛")
        return "，".join(parts)

    return "无明确信号"


def _legacy_trend_score(s: AssetScore) -> float:
    """兼容 P4：趋势分（动量+均线+MACD）。"""
    score = 0.0
    if s.momentum is not None:
        score += s.momentum * 0.4
    ma_points = {"多头排列": 25, "交织": 10, "空头排列": 0}
    score += ma_points.get(s.ma_alignment, 5)
    macd_points = {"金叉": 20, "无信号": 5, "死叉": 0}
    score += macd_points.get(s.macd_signal, 5)
    return min(100, max(0, round(score, 1)))


def _legacy_reversion_score(s: AssetScore) -> float:
    """兼容 P4：回归分（仅作观察，不再当机会）。"""
    if s.rsi is None:
        return 0.0
    rsi = s.rsi
    if rsi <= 20:
        return 95
    if rsi <= 30:
        return 80
    if rsi <= 40:
        return 55
    if rsi <= 50:
        return 35
    if rsi <= 60:
        return 20
    if rsi <= 70:
        return 10
    return 0


def _legacy_risk_score(s: AssetScore) -> float:
    """兼容 P4：风险分。"""
    score = 0.0
    dd = s.current_drawdown
    if dd < -20:
        score += 40
    elif dd < -15:
        score += 30
    elif dd < -10:
        score += 20
    elif dd < -5:
        score += 10
    if s.ma_alignment == "空头排列":
        score += 30
    elif s.ma_alignment == "交织":
        score += 10
    if s.volatility > 35:
        score += 30
    elif s.volatility > 25:
        score += 20
    elif s.volatility > 15:
        score += 10
    return min(100, max(0, round(score, 1)))
