"""板块异常检测 — 监控科技板块和持仓基金的风险信号

检测规则：
1. 动量骤降：5 日动量从正转负，降幅超阈值
2. 波动率飙升：20 日波动率 > 1.5x 60 日中位数
3. 相对走弱：板块收益显著落后 SPY
4. 板块排名跳水：在 10 个板块中排名下降 3+ 位
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from core.strategy.indicators import (
    calc_momentum_score,
    calc_volatility,
    calc_returns,
)
from core.data.config import US_SECTORS_SYMBOLS, WATCHED_HOLDINGS


@dataclass
class SectorAlert:
    """单个板块异常告警"""
    sector: str         # "XLK" or "011369"
    sector_name: str    # "科技ETF" or "中际旭创混合"
    alert_type: str     # "momentum_drop" / "vol_spike" / "relative_weakness" / "rank_drop"
    severity: str       # "warning" / "critical"
    message: str        # 人话描述
    details: Dict = field(default_factory=dict)


# 阈值配置
THRESHOLDS = {
    "default": {
        "momentum_drop": 30,      # 动量降幅阈值
        "vol_spike_ratio": 1.5,   # 波动率飙升倍数
        "relative_weakness": 5.0, # 相对 SPY 落后百分点
        "rank_drop": 3,           # 排名下降位数
    },
    "sensitive": {
        "momentum_drop": 20,
        "vol_spike_ratio": 1.3,
        "relative_weakness": 3.0,
        "rank_drop": 2,
    },
}


def _get_threshold(symbol: str, metric: str) -> float:
    """获取阈值（关注持仓用敏感阈值）"""
    holding = WATCHED_HOLDINGS.get(symbol, {})
    level = holding.get("threshold", "default")
    return THRESHOLDS[level][metric]


def _calc_short_momentum(close: pd.Series, window: int = 5) -> Optional[float]:
    """计算短期动量（简化版，只用短期收益映射到 0-100）"""
    if len(close) < window + 1:
        return None
    ret = (close.iloc[-1] / close.iloc[-window] - 1) * 100
    # 映射 [-10%, +10%] -> [0, 100]
    return max(0, min(100, (ret + 10) * 5))


def detect_sector_alerts(
    sector_data: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    prev_rankings: Optional[Dict[str, int]] = None,
) -> List[SectorAlert]:
    """检测板块异常

    Args:
        sector_data: {symbol: DataFrame} 所有板块/基金数据
        spy_df: SPY 数据（基准）
        prev_rankings: 前一天的动量排名 {symbol: rank}

    Returns:
        SectorAlert 列表
    """
    alerts = []

    if spy_df.empty or len(spy_df) < 30:
        return alerts

    spy_close = spy_df["close"]
    spy_ret_5d = (spy_close.iloc[-1] / spy_close.iloc[-5] - 1) * 100 if len(spy_close) >= 5 else 0

    # 计算当前排名
    current_scores = {}
    for symbol, df in sector_data.items():
        if df.empty or len(df) < 30:
            continue
        close = df["close"]
        score = calc_momentum_score(close)
        if score is not None:
            current_scores[symbol] = score

    # 按动量排名
    sorted_symbols = sorted(current_scores.keys(), key=lambda s: current_scores[s], reverse=True)
    current_rankings = {s: i + 1 for i, s in enumerate(sorted_symbols)}

    # 逐个板块检测
    for symbol, df in sector_data.items():
        if df.empty or len(df) < 30:
            continue

        close = df["close"]
        name = WATCHED_HOLDINGS.get(symbol, {}).get("name", "")
        if not name:
            name = US_SECTORS_SYMBOLS.get(symbol, symbol)

        is_watched = symbol in WATCHED_HOLDINGS

        # ---- 规则 1: 动量骤降 ----
        if len(close) >= 10:
            mom_now = calc_momentum_score(close)
            # 5 天前的动量
            mom_prev = calc_momentum_score(close.iloc[:-5])
            if mom_now is not None and mom_prev is not None:
                drop = mom_prev - mom_now
                threshold = _get_threshold(symbol, "momentum_drop")
                if mom_prev > 50 and mom_now < 50 and drop > threshold:
                    severity = "critical" if is_watched else "warning"
                    alerts.append(SectorAlert(
                        sector=symbol,
                        sector_name=name,
                        alert_type="momentum_drop",
                        severity=severity,
                        message=f"{name} 动量骤降：{mom_prev:.0f} → {mom_now:.0f}（降{drop:.0f}分）",
                        details={"prev": mom_prev, "now": mom_now, "drop": drop},
                    ))

        # ---- 规则 2: 波动率飙升 ----
        if len(close) >= 60:
            vol_now = calc_volatility(close, window=20)
            # 60 天滚动波动率的中位数
            daily_ret = close.pct_change()
            rolling_vol = daily_ret.rolling(20).std() * (252 ** 0.5) * 100
            vol_median = rolling_vol.iloc[-60:].median()
            if vol_now > 0 and vol_median > 0:
                ratio = vol_now / vol_median
                threshold = _get_threshold(symbol, "vol_spike_ratio")
                if ratio > threshold:
                    severity = "critical" if ratio > 2.0 else "warning"
                    alerts.append(SectorAlert(
                        sector=symbol,
                        sector_name=name,
                        alert_type="vol_spike",
                        severity=severity,
                        message=f"{name} 波动率飙升：{vol_now:.1f}%（中位数 {vol_median:.1f}%，{ratio:.1f}x）",
                        details={"vol_now": vol_now, "vol_median": vol_median, "ratio": ratio},
                    ))

        # ---- 规则 3: 相对走弱 ----
        if len(close) >= 10:
            ret_5d = (close.iloc[-1] / close.iloc[-5] - 1) * 100
            weakness = spy_ret_5d - ret_5d
            threshold = _get_threshold(symbol, "relative_weakness")
            if weakness > threshold:
                severity = "critical" if is_watched and weakness > threshold * 1.5 else "warning"
                alerts.append(SectorAlert(
                    sector=symbol,
                    sector_name=name,
                    alert_type="relative_weakness",
                    severity=severity,
                    message=f"{name} 相对走弱：5日收益 {ret_5d:+.1f}%，SPY {spy_ret_5d:+.1f}%，落后 {weakness:.1f}pp",
                    details={"ret_5d": ret_5d, "spy_ret_5d": spy_ret_5d, "weakness": weakness},
                ))

        # ---- 规则 4: 排名跳水（仅板块 ETF）----
        if symbol in current_rankings and prev_rankings and symbol in prev_rankings:
            curr_rank = current_rankings[symbol]
            prev_rank = prev_rankings[symbol]
            drop = curr_rank - prev_rank
            threshold = _get_threshold(symbol, "rank_drop")
            if drop >= threshold:
                alerts.append(SectorAlert(
                    sector=symbol,
                    sector_name=name,
                    alert_type="rank_drop",
                    severity="warning",
                    message=f"{name} 板块排名跳水：第{prev_rank}名 → 第{curr_rank}名（下降{drop}位）",
                    details={"prev_rank": prev_rank, "curr_rank": curr_rank, "drop": drop},
                ))

    # 按严重程度排序
    severity_order = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 2))

    return alerts


def get_sector_rankings(sector_data: Dict[str, pd.DataFrame]) -> Dict[str, int]:
    """计算板块动量排名（用于存储供次日比较）"""
    scores = {}
    for symbol, df in sector_data.items():
        if df.empty or len(df) < 30:
            continue
        if symbol in US_SECTORS_SYMBOLS:  # 只排板块 ETF
            score = calc_momentum_score(df["close"])
            if score is not None:
                scores[symbol] = score

    sorted_symbols = sorted(scores.keys(), key=lambda s: scores[s], reverse=True)
    return {s: i + 1 for i, s in enumerate(sorted_symbols)}
