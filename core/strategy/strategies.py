"""策略类定义

11 种资产配置策略，统一 BaseStrategy 接口，用于回测引擎调用。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.strategy.indicators import (
    calc_momentum_score,
    calc_ma_alignment,
    calc_macd,
    calc_volatility,
    calc_rsi,
    calc_correlation_matrix,
    calc_cvar,
    detect_market_regime,
)
from core.strategy.kelly import kelly_weights, mu_from_momentum
from core.strategy.trend_follow import (
    TSMOM_LOOKBACK,
    DONCHIAN_ENTRY,
    DONCHIAN_EXIT,
    CHAN_K_SLOW,
    COOLDOWN_SLOW,
    calc_tsmom_signal,
    simulate_tsmom_chandelier_positions,
    simulate_donchian_positions,
    inverse_vol_weights,
)
from core.data.config import STRATEGY_UNIVERSE, CASH_PROXY


# 所有可配置资产（大类 + 国际 + 板块）
ALL_SYMBOLS = list(STRATEGY_UNIVERSE.keys())


# 策略分类定义
STRATEGY_CATEGORIES = {
    "趋势跟随": {
        "label": "  趋势跟随",
        "subtitle": "自己在涨就拿",
        "description": "核心假设：趋势一旦形成就会延续。各资产独立判断自己的方向，不和别的票比强弱。",
        "market_view": "单边市拿趋势；股票走熊时可切债/金/油；比动量轮动换手更低、更偏防御",
    },
    "趋势动量": {
        "label": "  趋势/动量",
        "subtitle": "涨的继续涨",
        "description": "核心假设：价格有惯性。过去表现好的资产未来还会好。",
        "market_view": "牛市吃肉，熊市挨打；震荡市容易被来回割",
    },
    "风险配置": {
        "label": " ️ 风险配置",
        "subtitle": "不预测，只分配",
        "description": "核心假设：不判断方向，通过数学方法优化风险分配。",
        "market_view": "回撤小但牛市跑不赢；靠资产间低相关+风险均衡吃饭",
    },
    "反脆弱": {
        "label": "  反脆弱",
        "subtitle": "从混乱中受益",
        "description": "核心假设：不预测黑天鹅，用期权构建凸性收益——小亏多次，大赚少次。",
        "market_view": "平时付保险费（标准 0.3%/月，激进 0.5%/月），暴跌时 BS 模型凸性收益放大",
    },
    "动态配置": {
        "label": "  动态配置",
        "subtitle": "因时而变",
        "description": "核心假设：不同市场环境需要不同策略，体制切换而非押注单一策略。",
        "market_view": "牛市用动量、危机用对冲、震荡用分散——理论上全环境都能活",
    },
}


class BaseStrategy(ABC):
    """策略基类 - 定义标准化接口"""

    name: str = ""
    description: str = ""
    category: str = ""  # 必须是 STRATEGY_CATEGORIES 的 key
    rebalance_freq: str = "M"

    @abstractmethod
    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        """计算目标持仓权重

        Args:
            current_date: 当前回测日期
            all_data: {symbol: DataFrame} 截至 current_date 的数据
            lookback_days: 回看窗口

        Returns:
            {symbol: weight}，weight 为 0.0-1.0，剩余为现金
        """
        pass

    def get_all_symbols(self) -> List[str]:
        """返回策略使用的所有资产代码"""
        return ALL_SYMBOLS


def _slice_data(
    all_data: Dict[str, pd.DataFrame],
    current_date: pd.Timestamp,
    lookback_days: int,
) -> Dict[str, pd.Series]:
    """截取截至 current_date 的收盘价序列"""
    result = {}
    for symbol, df in all_data.items():
        if df.empty:
            continue
        window = df[df["date"] <= current_date].tail(lookback_days)
        if len(window) > 0:
            result[symbol] = window["close"].reset_index(drop=True)
    return result


class TrendFollowingStrategy(BaseStrategy):
    """趋势跟踪策略

    均线多头排列时持有，空头或交织时清仓。
    MACD 金叉确认加强信号。
    """

    name = "趋势跟踪策略"
    description = "均线多头排列时持有，空头或交织时清仓，MACD 确认（技术面基线）"
    category = "趋势跟随"

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        qualifying = []
        half_weight = []

        for symbol, close in sliced.items():
            if len(close) < 200:
                continue

            # 条件 A：均线多头排列
            alignment = calc_ma_alignment(close)
            is_bullish = alignment == "多头排列"

            # 条件 B：MACD 柱状图 > 0
            dif, dea, hist = calc_macd(close)
            macd_positive = len(hist) > 0 and hist.iloc[-1] > 0

            if is_bullish and macd_positive:
                qualifying.append(symbol)
            elif is_bullish:
                half_weight.append(symbol)

        if not qualifying and not half_weight:
            return {}

        # 分配权重：全权重资产各 1 份，半权重资产各 0.5 份
        total_units = len(qualifying) + 0.5 * len(half_weight)
        if total_units == 0:
            return {}

        weights = {}
        unit_weight = 1.0 / total_units
        for s in qualifying:
            weights[s] = unit_weight
        for s in half_weight:
            weights[s] = unit_weight * 0.5

        return weights


def _trend_portfolio_weights(selected: List[str], per_name_cap: float = 0.20) -> Dict[str, float]:
    """趋势跟随组合层：等权 + 单票硬顶，不足 cap 的部分留现金（保本优先）"""
    if not selected:
        return {}
    w = 1.0 / len(selected)
    weights = {}
    for s in selected:
        ww = min(per_name_cap, w)
        if ww > 0:
            weights[s] = ww
    return weights


class TimeSeriesMomentumStrategy(BaseStrategy):
    """TSMOM 时间序列动量 — 各资产独立看自己的 12 月方向

    机构 CTA 的最简形式（AQR TSMOM）：
    - 信号：12 个月收益符号（正=持有，负=空仓），只看方向不看幅度
    - 配权：持仓内部反波动率，单票硬顶 20%，剩余现金
    - 频率：月频，无盘中止损（月内回撤自己扛）

    与双动量的本质区别：不横截面比强弱。SPY 跌而 GLD 涨时会切到 GLD；
    双动量则把 12M 变负的股票踢掉，但不会主动配 GLD 除非它相对最强。
    """

    name = "TSMOM 趋势跟随"
    description = "各资产独立 12 月收益符号择时 + 反波动率配权，月频，单票 20% 上限"
    category = "趋势跟随"
    rebalance_freq = "M"

    lookback_period: int = TSMOM_LOOKBACK
    per_name_cap: float = 0.20

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days=max(lookback_days, 300))

        qualifying = []
        closes = {}
        for symbol, close in sliced.items():
            if len(close) <= self.lookback_period:
                continue
            closes[symbol] = close
            if calc_tsmom_signal(close, self.lookback_period):
                qualifying.append(symbol)

        if not qualifying:
            return {}
        return inverse_vol_weights(closes, qualifying, per_name_cap=self.per_name_cap)


class CTABreakoutStrategy(BaseStrategy):
    """CTA 突破跟随 — Donchian 通道 + 吊灯止损

    海龟/管理期货的经典突破流：
    - 进场：收盘价突破前 55 日最高价（创新高才买，不抄底）
    - 离场：跌破前 20 日低点，或跌破吊灯线（最高价−3×ATR）
    - 持有段从价格历史无状态重放；周频让突破/止损及时生效

    换手通常很低（一进一出可拿数月），周频 ≠ 高成本。
    """

    name = "CTA 突破跟随"
    description = "55 日新高突破进场 + 20 日低点/吊灯离场，各资产独立，单票 20% 上限"
    category = "趋势跟随"
    rebalance_freq = "W"

    entry_lookback: int = DONCHIAN_ENTRY
    exit_lookback: int = DONCHIAN_EXIT
    per_name_cap: float = 0.20

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        ohlcv = _slice_ohlcv(all_data, current_date, lookback_days=max(lookback_days, 800))
        if not ohlcv:
            return {}

        candidates = []
        for symbol, df in ohlcv.items():
            if len(df) < self.entry_lookback + 30:
                continue
            close = df["close"]
            high = df["high"] if "high" in df.columns else close * 1.01
            low = df["low"] if "low" in df.columns else close * 0.99
            pos = simulate_donchian_positions(
                close, high, low,
                entry_lookback=self.entry_lookback,
                exit_lookback=self.exit_lookback,
            )
            if len(pos) and bool(pos["in_position"].iloc[-1]):
                candidates.append(symbol)

        return _trend_portfolio_weights(candidates, self.per_name_cap)


class MomentumChandelierStrategy(BaseStrategy):
    """动量吊灯跟随 — 12 月符号管方向，吊灯线管离场

    「符号」= 12 个月收益的正负号（方向）；「吊灯」= 最高价 − k×ATR 的移动止损线。
    - 符号为正 → 允许持有；符号翻负 → 离场
    - 持有期间跌破吊灯线或硬止损 −8% → 离场并冷却
    - 符号负责「要不要持有」，吊灯负责「何时落袋」

    比纯 TSMOM 多一层月末吊灯保护，比 CTA 突破少一层通道信号。
    用月频评估止损：12 月趋势不应被周内噪音打出去（周频实测显著更差）。
    """

    name = "动量吊灯跟随"
    description = "12 月收益符号为正才持有 + 月末吊灯线止损，符号管方向、吊灯管离场"
    category = "趋势跟随"
    rebalance_freq = "M"

    lookback_period: int = TSMOM_LOOKBACK
    per_name_cap: float = 0.20

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        ohlcv = _slice_ohlcv(all_data, current_date, lookback_days=max(lookback_days, 800))
        if not ohlcv:
            return {}

        candidates = []
        for symbol, df in ohlcv.items():
            if len(df) <= self.lookback_period + 10:
                continue
            close = df["close"]
            high = df["high"] if "high" in df.columns else close * 1.01
            low = df["low"] if "low" in df.columns else close * 0.99
            pos = simulate_tsmom_chandelier_positions(
                close, high, low,
                lookback=self.lookback_period,
                base_k=CHAN_K_SLOW,
                cooldown_days=COOLDOWN_SLOW,
            )
            if len(pos) and bool(pos["in_position"].iloc[-1]):
                candidates.append(symbol)

        return _trend_portfolio_weights(candidates, self.per_name_cap)


class RiskParityStrategy(BaseStrategy):
    """风险平价策略

    按波动率反比分配权重，低波高配。
    保本优先，低回撤。
    """

    name = "风险平价策略"
    description = "按波动率反比分配权重，低波高配，保本优先"
    category = "风险配置"
    min_volatility: float = 0.5  # 最低波动率阈值 (%)

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 计算各资产波动率
        vol_map = {}
        for symbol, close in sliced.items():
            vol = calc_volatility(close, window=20)
            if vol and vol >= self.min_volatility:
                vol_map[symbol] = vol

        if not vol_map:
            return {}

        # 波动率反比
        inv_vol = {s: 1.0 / v for s, v in vol_map.items()}
        total_inv_vol = sum(inv_vol.values())

        # 归一化，保留 5% 现金
        investable = 0.95
        weights = {s: (iv / total_inv_vol) * investable for s, iv in inv_vol.items()}

        return weights


class MinCorrelationStrategy(BaseStrategy):
    """低相关性组合策略

    基于资产间相关性矩阵，构建最大化分散化组合。
    等风险贡献(ERC)思想：相关性低的资产获得更高权重。
    """

    name = "低相关性组合策略"
    description = "基于相关性矩阵构建分散化组合，降低整体风险"
    category = "风险配置"

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        # 计算相关性矩阵需要完整 DataFrame
        window_data = {}
        for symbol, df in all_data.items():
            if df.empty:
                continue
            window = df[df["date"] <= current_date].tail(60)  # 60 日滚动相关性
            if len(window) >= 30:
                window_data[symbol] = window

        if len(window_data) < 2:
            return {}

        # 计算相关性矩阵
        corr_matrix = calc_correlation_matrix(window_data, column="close", window=60)

        if corr_matrix.empty or len(corr_matrix) < 2:
            return {}

        # 计算每个资产与其他资产的平均相关性
        symbols = corr_matrix.columns.tolist()
        avg_corr = {}
        for symbol in symbols:
            # 排除自身，计算平均相关性
            other_corr = corr_matrix[symbol].drop(symbol)
            avg_corr[symbol] = other_corr.mean()

        # 平均相关性越低，权重越高（分散化效果越好）
        # 将相关性转换为权重：weight_i = (1 - avg_corr_i) / sum(1 - avg_corr_j)
        inv_corr = {s: max(0.01, 1.0 - c) for s, c in avg_corr.items()}
        total_inv_corr = sum(inv_corr.values())

        if total_inv_corr == 0:
            return {}

        # 归一化，保留 5% 现金
        investable = 0.95
        weights = {
            s: (ic / total_inv_corr) * investable
            for s, ic in inv_corr.items()
        }

        return weights


class DualMomentumStrategy(BaseStrategy):
    """双动量策略

    两层过滤：
    1. 绝对动量：12 个月收益 > 0 才持有（熊市防空洞）
    2. 相对动量：通过绝对动量的资产中选最强的

    Gary Antonacci 经典策略，回撤远低于纯动量。
    """

    name = "双动量策略"
    description = "绝对动量过滤熊市 + 相对动量选最强资产，熊市自动切现金"
    category = "趋势动量"
    top_n: int = 3
    lookback_period: int = 252  # 12 个月

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 计算各资产收益率（绝对动量）
        returns = {}
        for symbol, close in sliced.items():
            if len(close) >= self.lookback_period:
                ret = (close.iloc[-1] / close.iloc[-self.lookback_period] - 1) * 100
                returns[symbol] = ret

        if not returns:
            return {}

        # 绝对动量过滤：只保留正收益的资产
        positive = {s: r for s, r in returns.items() if r > 0}

        if not positive:
            # 所有资产收益为负 → 全部持有现金
            return {}

        # 相对动量排序，选前 N 个
        ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
        selected = ranked[: self.top_n]

        # 等权分配
        weight = 1.0 / len(selected)
        return {s: weight for s, _ in selected}


class DualMomentum6MStrategy(DualMomentumStrategy):
    """双动量 6 个月版

    缩短回望期到 6 个月，对近期趋势更敏感。
    优势：更快响应趋势变化，减少滞后。
    劣势：信号更频繁，假信号更多。
    """

    name = "双动量6个月"
    description = "6 个月回望期，对近期趋势更敏感，信号更频繁"
    category = "趋势动量"
    lookback_period = 126  # 6 个月


class DualMomentum3MStrategy(DualMomentumStrategy):
    """双动量 3 个月版

    最短回望期，极度敏感。
    优势：最快响应趋势反转。
    劣势：噪音最多，可能在震荡市频繁进出。
    """

    name = "双动量3个月"
    description = "3 个月回望期，极度敏感，最快响应趋势反转"
    category = "趋势动量"
    lookback_period = 63  # 3 个月


class MomentumVolFilterStrategy(BaseStrategy):
    """动量+波动率过滤策略

    选股用动量，仓位用波动率管理：
    - 选动量评分最高的资产
    - 波动率越高，仓位越低（目标波动率 / 实际波动率）
    - 低波环境满仓吃肉，高波环境自动减仓

    解决纯动量策略波动大、回撤深的问题。
    """

    name = "动量+波动率过滤"
    description = "动量选股 + 波动率控仓，高波动自动降仓，稳步上涨"
    category = "趋势动量"
    top_n: int = 3
    target_vol: float = 12.0  # 目标年化波动率 (%)
    min_momentum: float = 50  # 最低动量评分

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 第一步：动量选股
        scores = {}
        for symbol, close in sliced.items():
            score = calc_momentum_score(close)
            if score is not None:
                scores[symbol] = score

        if not scores:
            return {}

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = [(s, sc) for s, sc in ranked if sc >= self.min_momentum][: self.top_n]

        if not selected:
            return {}

        # 第二步：波动率控仓
        # 计算选中资产的加权平均波动率
        vol_map = {}
        for symbol, _ in selected:
            close = sliced.get(symbol)
            if close is not None:
                vol = calc_volatility(close, window=20)
                if vol and vol > 0:
                    vol_map[symbol] = vol

        if not vol_map:
            # 无法计算波动率，等权分配
            weight = 1.0 / len(selected)
            return {s: weight for s, _ in selected}

        # 总仓位 = 目标波动率 / 实际波动率（上限 100%）
        avg_vol = sum(vol_map.values()) / len(vol_map)
        total_position = min(1.0, self.target_vol / avg_vol) if avg_vol > 0 else 0.5

        # 资产间按波动率反比分配（低波多配）
        inv_vol = {s: 1.0 / v for s, v in vol_map.items()}
        total_inv_vol = sum(inv_vol.values())

        weights = {
            s: (iv / total_inv_vol) * total_position
            for s, iv in inv_vol.items()
        }

        return weights


class DrawdownControlStrategy(BaseStrategy):
    """最大回撤控制策略

    在动量选股基础上叠加回撤风控层：
    - 回撤 < 5%：满仓
    - 回撤 5%-8%：半仓
    - 回撤 8%-10%：20% 仓位
    - 回撤 > 10%：清仓，等净值恢复

    直接对应风控红线：-10% 减半仓，-15% 清仓。
    可套在任何策略上面，这里以动量轮动为底仓。
    """

    name = "回撤控制策略"
    description = "动量选股 + 回撤风控，跌破阈值自动减仓，保本优先"
    category = "趋势动量"
    top_n: int = 3
    # 回撤阈值 → 仓位比例
    dd_thresholds = [
        (-5.0, 1.0),   # 回撤 < 5%：满仓
        (-8.0, 0.5),   # 回撤 < 8%：半仓
        (-10.0, 0.2),  # 回撤 < 10%：20% 仓位
        (-999, 0.0),   # 回撤 > 10%：清仓
    ]

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 第一步：动量选股（与 MomentumRotationStrategy 相同）
        scores = {}
        for symbol, close in sliced.items():
            score = calc_momentum_score(close)
            if score is not None:
                scores[symbol] = score

        if not scores:
            return {}

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = [(s, sc) for s, sc in ranked if sc > 50][: self.top_n]

        if not selected:
            return {}

        # 等权底仓
        base_weight = 1.0 / len(selected)
        base_weights = {s: base_weight for s, _ in selected}

        # 第二步：计算组合当前回撤（用 SPY 作为组合代理）
        spy_close = sliced.get("SPY")
        if spy_close is not None and len(spy_close) > 1:
            current_dd = (spy_close.iloc[-1] / spy_close.max() - 1) * 100
        else:
            current_dd = 0.0

        # 第三步：根据回撤确定仓位比例
        position_scale = 1.0
        for threshold, scale in self.dd_thresholds:
            if current_dd >= threshold:
                position_scale = scale
                break

        # 仓位为 0 时直接空仓，不要返回 0% 权重
        if position_scale <= 0:
            return {}

        # 应用仓位缩放，过滤掉缩放后为 0 的资产
        weights = {s: w * position_scale for s, w in base_weights.items() if w * position_scale > 0}

        return weights


def _slice_ohlcv(
    all_data: Dict[str, pd.DataFrame],
    current_date: pd.Timestamp,
    lookback_days: int = 2000,
) -> Dict[str, pd.DataFrame]:
    """截取截至 current_date 的 OHLCV，保留完整回看窗口（长回看指标需要足够历史）"""
    result = {}
    for symbol, df in all_data.items():
        if df.empty:
            continue
        window = df[df["date"] <= current_date].tail(lookback_days)
        if len(window) > 0:
            result[symbol] = window.reset_index(drop=True)
    return result


class DualMomentumKellyStrategy(DualMomentumStrategy):
    """双动量 × 分数凯利配比

    信号与双动量完全一致（12M 绝对动量过滤 + 相对动量选强）。
    分仓从等权 33% 改为 1/2 凯利：w = clip(0.50·μ̂/σ², 0, 20%)。
    μ̂ 用 12M 动量温和压缩，σ̂ 用 60 日已实现波动率。
    """

    name = "双动量×凯利"
    description = "双动量信号不变，分仓改为 1/2 凯利 μ̂/σ² + 单票 20% 硬顶"
    top_n: int = 5
    kelly_k: float = 0.50
    max_weight: float = 0.20
    vol_window: int = 60
    # 组合回撤自动化（A2）：月频 -15% 减半 / -20% 清仓。
    # 不要用 -10%/-15% 做自动刹车（收益 -25pp、回撤只少 2pp），也不要用「DD 收窄」当恢复条件（清仓后死锁）
    portfolio_dd_brake: bool = True
    dd_halve: float = -0.15
    dd_flatten: float = -0.20
    dd_restore: Optional[float] = None
    dd_brake_intraday: bool = False

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        returns = {}
        for symbol, close in sliced.items():
            if len(close) >= self.lookback_period:
                ret = (close.iloc[-1] / close.iloc[-self.lookback_period] - 1) * 100
                returns[symbol] = ret

        if not returns:
            return {}

        positive = {s: r for s, r in returns.items() if r > 0}
        if not positive:
            return {}

        ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
        selected = ranked[: self.top_n]

        mu_map = {}
        vol_map = {}
        for symbol, ret in selected:
            close = sliced[symbol]
            mu_map[symbol] = mu_from_momentum(ret)
            vol = calc_volatility(close, window=self.vol_window)
            if vol and vol > 0:
                vol_map[symbol] = vol / 100.0  # 转小数

        return kelly_weights(
            mu_map, vol_map,
            k=self.kelly_k,
            max_weight=self.max_weight,
        )


class MomentumVolKellyStrategy(MomentumVolFilterStrategy):
    """动量选股 × 凯利波动率配比

    选股同「动量+波动率过滤」（动量分 ≥ 阈值选 top N）。
    分仓从 target_vol/实际vol 改为分数凯利 μ̂/σ²，同时保留总仓位波动率上限。
    """

    name = "动量波动率×凯利"
    description = "动量选股不变，分仓改为 1/2 凯利 μ̂/σ²，波动率上限兜底"
    top_n: int = 5
    kelly_k: float = 0.50
    max_weight: float = 0.20
    vol_window: int = 60

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        scores = {}
        for symbol, close in sliced.items():
            score = calc_momentum_score(close)
            if score is not None:
                scores[symbol] = score

        if not scores:
            return {}

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected = [(s, sc) for s, sc in ranked if sc >= self.min_momentum][: self.top_n]
        if not selected:
            return {}

        mu_map = {}
        vol_map = {}
        for symbol, score in selected:
            close = sliced.get(symbol)
            if close is None or len(close) < 63:
                continue
            # 动量分 0-100 → 超出 50 的部分当优势
            ret_proxy = (score - 50.0) * 0.4  # 80 分 → +12% 预期
            mu_map[symbol] = mu_from_momentum(ret_proxy)
            vol = calc_volatility(close, window=self.vol_window)
            if vol and vol > 0:
                vol_map[symbol] = vol / 100.0

        weights = kelly_weights(
            mu_map, vol_map,
            k=self.kelly_k,
            max_weight=self.max_weight,
        )

        # 波动率上限：组合目标 12% 年化（沿用原策略风控）
        if weights and vol_map:
            port_vol = sum(weights[s] * vol_map[s] for s in weights if s in vol_map)
            if port_vol > self.target_vol / 100.0 > 0:
                scale = (self.target_vol / 100.0) / port_vol
                weights = {s: w * scale for s, w in weights.items()}

        return weights


class _AntifragileBase:
    """反脆弱策略共用的双动量权重逻辑"""

    top_n: int = 3

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)
        returns_12m = {}
        for symbol, close in sliced.items():
            if len(close) >= 252:
                ret = (close.iloc[-1] / close.iloc[-252] - 1) * 100
                returns_12m[symbol] = ret
        if not returns_12m:
            return {}
        positive = {s: r for s, r in returns_12m.items() if r > 0}
        if not positive:
            return {}
        ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
        selected = ranked[: self.top_n]
        weight = 1.0 / len(selected)
        return {s: weight for s, _ in selected}


class AntifragileStrategy(_AntifragileBase, BaseStrategy):
    """反脆弱策略（塔勒布杠铃 + 尾部对冲）

    结构：
    - 主仓 (100%): 双动量策略选股，负责日常收益
    - 尾部对冲: 买 SPY 深度虚值看跌期权（模拟），每月付固定成本
    - 对冲收益: SPY 月末跌幅超过阈值时，BS 公式产生凸性收益

    对冲参数通过类属性设置，由回测引擎读取：
    - hedge_monthly_cost: 月成本比例（0.3%，年化 ~3.6%）
    - hedge_otm_threshold: 虚值比例（5%，SPY 月均波动 ~4%）
    - hedge_leverage: 向后兼容参数，BS 公式不依赖此值

    赔付采用简化 Black-Scholes 公式，用月末收盘价计算 SPY 月跌幅，
    近似真实 5% OTM 月 put 的凸性收益结构。
    """

    name = "反脆弱策略"
    description = "双动量主仓 + SPY 看跌期权尾部对冲，暴跌时凸性收益，稳步上涨"
    category = "反脆弱"
    top_n: int = 3

    # 尾部对冲参数（引擎通过 getattr 读取）
    # 赔付: 简化 Black-Scholes 公式计算 5% OTM 月 put 收益
    # 成本 0.3%/月（年化 ~3.6%），大跌时凸性收益可覆盖多年成本
    hedge_monthly_cost: float = 0.003   # 每月 0.3% 权利金（备用，BS 模式下不使用）
    hedge_otm_threshold: float = 0.05   # 5% OTM
    hedge_leverage: float = 15.0        # 向后兼容参数，BS 公式不依赖此值
    hedge_vol: float = 0.20             # BS 公式年化波动率
    use_bs_cost: bool = True            # 用 BS 公式动态计算权利金成本


class AntifragileAggressiveStrategy(_AntifragileBase, BaseStrategy):
    """反脆弱激进版（优化参数：低 OTM + 中等成本）

    与标准反脆弱的区别：
    - OTM 阈值从 5% 降到 3%（更频繁触发，SPY 月跌 3% 比 5% 常见得多）
    - 权利金从 0.3%/月 提高到 0.5%/月（年化 6% vs 3.6%）
    - 参数优化结果：0.5%/3% 组合在回测中全面优于标准版

    回测对比（2019-2026）：
    - 标准版 (0.3%/5%): 年化 +14.94%, 回撤 -31.13%, 夏普 0.75
    - 激进版 (0.5%/3%): 年化 +16.75%, 回撤 -28.07%, 夏普 0.86
    """

    name = "反脆弱激进版"
    description = "3% OTM + 0.5%/月权利金，更频繁触发对冲，暴跌收益更猛"
    category = "反脆弱"
    top_n: int = 3

    # 优化后的对冲参数（经参数扫描验证）
    hedge_monthly_cost: float = 0.005   # 每月 0.5% 权利金（备用，BS 模式下不使用）
    hedge_otm_threshold: float = 0.03   # 3% OTM（更频繁触发）
    hedge_leverage: float = 15.0        # 向后兼容参数
    hedge_vol: float = 0.20             # BS 公式年化波动率
    use_bs_cost: bool = True            # 用 BS 公式动态计算权利金成本


class AntifragileWeeklyStrategy(_AntifragileBase, BaseStrategy):
    """反脆弱周频版（每周结算尾部对冲）

    与标准反脆弱的区别：
    - 结算频率：每周（每周最后交易日）而非每月
    - OTM 阈值：3%（周跌幅通常 < 月跌幅，降低阈值提高触发频率）
    - T=1/52（周 put 期权），权利金按周扣减
    - 年化成本与标准版相当（~3.6%），但更频繁触发赔付

    参数设计思路：
    - 周度 SPY 标准差 ~1.5%，3% OTM ≈ 2σ
    - 周 put 比月 put 便宜（时间短），但需要更频繁购买
    - 引擎自动将月成本换算为周成本：0.3% / 4.33 ≈ 0.069%/周
    """

    name = "反脆弱周频版"
    description = "每周结算 SPY 看跌期权，3% OTM，T=1/52，更频繁触发对冲"
    category = "反脆弱"
    rebalance_freq: str = "W"

    hedge_monthly_cost: float = 0.003   # 月成本 0.3%（备用，BS 模式下不使用）
    hedge_otm_threshold: float = 0.03   # 3% OTM
    hedge_leverage: float = 15.0
    hedge_vol: float = 0.20             # BS 公式年化波动率
    use_bs_cost: bool = True            # 用 BS 公式动态计算权利金成本


class AntifragileDailyStrategy(_AntifragileBase, BaseStrategy):
    """反脆弱日频版（每日结算尾部对冲）

    与标准反脆弱的区别：
    - 结算频率：每日而非每月
    - OTM 阈值：2%（日跌幅通常很小，阈值需更低）
    - T=1/252（日 put 期权），权利金按日扣减
    - vol=30%（日频期权隐含波动率高于月频，反映短周期波动率期限结构）
    - 使用 BS 公允价值计算权利金（而非固定比例），反映日频期权真实成本

    参数设计思路：
    - 日度 SPY 标准差 ~1%，2% OTM ≈ 2σ
    - 日频期权真实 IV 远高于月频（短周期 vol 期限结构上翘）
    - 固定成本模型在 T→0 时严重低估日频期权成本（差 26 倍）
    - BS 公允价值模式自动计算 OTM put 权利金，消除成本低估
    - 2% OTM 在 BS 公允价值下接近盈亏平衡，更符合真实市场
    """

    name = "反脆弱日频版"
    description = "每日结算 SPY 看跌期权，2% OTM，T=1/252，vol=30%，BS公允价值定价"
    category = "反脆弱"
    rebalance_freq: str = "D"

    hedge_monthly_cost: float = 0.006   # 备用固定成本（BS 模式下不使用）
    hedge_otm_threshold: float = 0.02   # 2% OTM（BS公允价值下接近盈亏平衡）
    hedge_leverage: float = 15.0
    hedge_vol: float = 0.30             # 日频期权 IV 高于月频
    use_bs_cost: bool = True            # 用 BS 公式动态计算权利金成本


class TailRiskParityStrategy(BaseStrategy):
    """尾部风险平价策略

    用 CVaR（条件风险价值）+ 动量构建风险调整后收益评分：
    1. 计算各资产 CVaR（尾部 5% 的平均损失）
    2. 计算动量评分（预期收益代理）
    3. 按「动量 / CVaR」分配权重（高收益+低尾部风险 = 高权重）
    4. 波动率异常高时自动降仓

    与风险平价的区别：风险平价用波动率（对称风险），这里用 CVaR（尾部风险）。
    与纯 CVaR 的区别：纯 CVaR 不考虑收益，会导致低风险低收益资产获得过高权重。
    加入动量因子后，策略倾向于选择「尾部风险低 + 收益不错」的资产。
    """

    name = "尾部风险平价"
    description = "CVaR+动量联合优化，选择尾部风险低且收益好的资产"
    category = "风险配置"
    alpha: float = 0.05
    vol_scale_threshold: float = 1.5
    min_momentum: float = 40  # 最低动量阈值

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 绝对动量过滤：SPY 12 个月收益为负时大幅降仓
        position_scale = 1.0
        spy_close = sliced.get("SPY")
        if spy_close is not None and len(spy_close) >= 252:
            spy_ret = spy_close.iloc[-1] / spy_close.iloc[-252] - 1
            if spy_ret <= 0:
                position_scale = 0.3  # 熊市只保留 30% 仓位
        elif spy_close is not None and len(spy_close) >= 60:
            pass  # 数据不足，维持满仓

        # 波动率风控
        if spy_close is not None and len(spy_close) >= 60:
            returns = spy_close.pct_change().dropna()
            current_vol = returns.tail(20).std()
            median_vol = returns.rolling(60).std().median()
            if median_vol > 0 and current_vol > median_vol * self.vol_scale_threshold:
                position_scale *= max(0.3, median_vol * self.vol_scale_threshold / current_vol)

        # 计算各资产的风险调整评分
        scores = {}
        for symbol, close in sliced.items():
            if len(close) < 60:
                continue
            cvar = calc_cvar(close, window=min(len(close), 252), alpha=self.alpha)
            if cvar <= 0:
                continue
            momentum = calc_momentum_score(close)
            if momentum is None or momentum < self.min_momentum:
                continue
            scores[symbol] = momentum / cvar

        if not scores:
            return {}

        total_score = sum(scores.values())
        investable = 0.95 * position_scale
        weights = {s: (sc / total_score) * investable for s, sc in scores.items()}

        return weights


class DrawdownConstraintStrategy(BaseStrategy):
    """回撤约束优化策略

    在动量选股+波动率配仓基础上叠加回撤硬约束：
    1. 用动量评分选股
    2. 用波动率反比分配基础权重
    3. 资产级：剔除历史最大回撤超过 -40% 的资产
    4. 组合级：SPY 回撤超阈值时减仓（对齐风控红线 -10%/-15%/-20%）

    与回撤控制策略的区别：回撤控制只做仓位缩放，
    回撤约束在资产选择层面就开始控制（剔除高回撤资产）。
    """

    name = "回撤约束优化"
    description = "动量选股+波动率配仓+回撤硬约束，资产级和组合级双重风控"
    category = "风险配置"
    top_n: int = 4
    min_momentum: float = 50
    asset_max_dd: float = -40.0  # 单资产最大回撤阈值 (%)
    portfolio_dd_thresholds = [
        (-8.0, 1.0),    # 回撤 < 8%：满仓
        (-12.0, 0.6),   # 回撤 < 12%：6 成仓
        (-16.0, 0.3),   # 回撤 < 16%：3 成仓
        (-999, 0.0),    # 回撤 > 16%：清仓
    ]

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 第一步：动量选股
        scores = {}
        for symbol, close in sliced.items():
            score = calc_momentum_score(close)
            if score is not None and score >= self.min_momentum:
                scores[symbol] = score

        if not scores:
            return {}

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        candidates = ranked[: self.top_n * 2]

        # 第二步：回撤约束过滤（资产级）
        filtered = []
        for symbol, score in candidates:
            close = sliced.get(symbol)
            if close is None or len(close) < 60:
                continue
            max_dd = _calc_max_dd(close)
            if max_dd >= self.asset_max_dd:
                filtered.append((symbol, score))

        if not filtered:
            filtered = ranked[:2]

        filtered = filtered[: self.top_n]

        # 第三步：波动率反比分配基础权重
        vol_map = {}
        for symbol, _ in filtered:
            close = sliced.get(symbol)
            if close is not None:
                vol = calc_volatility(close, window=20)
                if vol and vol > 0:
                    vol_map[symbol] = vol

        if not vol_map:
            weight = 1.0 / len(filtered)
            return {s: weight for s, _ in filtered}

        inv_vol = {s: 1.0 / v for s, v in vol_map.items()}
        total_inv = sum(inv_vol.values())

        # 第四步：组合回撤约束（用 SPY 作为组合代理）
        spy_close = sliced.get("SPY")
        position_scale = 1.0
        if spy_close is not None and len(spy_close) > 1:
            current_dd = (spy_close.iloc[-1] / spy_close.max() - 1) * 100
            for threshold, scale in self.portfolio_dd_thresholds:
                if current_dd >= threshold:
                    position_scale = scale
                    break

        if position_scale <= 0:
            return {}

        investable = 0.95 * position_scale
        weights = {s: (iv / total_inv) * investable for s, iv in inv_vol.items()}

        return weights


# ============================================================
# 体制切换策略
# ============================================================

# 体制→策略映射（固定，理论驱动）
_REGIME_STRATEGY_MAP = {
    "风险偏好": "双动量3个月",       # 趋势向上时动量收割
    "避险": "风险平价",             # 波动率反比配置，自动降仓
    "危机": "反脆弱激进版",          # 尾部对冲暴跌时凸性收益
    "滞胀担忧": "低相关性组合",      # 分散化扛滞胀，不单押方向
    "震荡": "低相关性组合",          # 最大分散化降低来回割
    "中性": "双动量",               # 绝对动量过滤
}


class RegimeSwitchingStrategy(BaseStrategy):
    """体制切换策略

    根据市场体制（风险偏好/避险/危机/滞胀/震荡/中性）动态选择最优子策略。
    理论驱动的固定映射，非数据拟合。

    防抖动机制：
    - 连续 5 个交易日判定同一体制才触发切换
    - 切换后至少持有 1 个再平衡周期

    月度再平衡。
    """

    name = "体制切换策略"
    description = "根据市场体制动态选择最优策略，防抖动连续确认"
    category = "动态配置"
    rebalance_freq = "M"

    # 连续确认阈值（交易日数）
    CONSECUTIVE_THRESHOLD = 5
    # 最小持有期（再平衡次数）
    MIN_HOLDING_PERIODS = 1

    def __init__(self):
        # 子策略实例缓存（延迟初始化）
        self._sub_strategies: Dict[str, BaseStrategy] = {}
        # 切换状态
        self._current_regime: str = "中性"
        self._consecutive_count: int = 0
        self._holding_periods: int = 0
        self._sub_strategy: BaseStrategy = None

    def _get_sub_strategy(self, name: str) -> BaseStrategy:
        """延迟初始化子策略实例"""
        if name not in self._sub_strategies:
            # 按名称查找策略类
            strategy_map = {
                "双动量": DualMomentumStrategy,
                "双动量3个月": DualMomentum3MStrategy,
                "双动量6个月": DualMomentum6MStrategy,
                "风险平价": RiskParityStrategy,
                "反脆弱激进版": AntifragileAggressiveStrategy,
                "低相关性组合": MinCorrelationStrategy,
            }
            cls = strategy_map.get(name)
            if cls:
                self._sub_strategies[name] = cls()
            else:
                # 回退到双动量
                self._sub_strategies[name] = DualMomentumStrategy()
        return self._sub_strategies[name]

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        # 获取 SPY/TLT/CL 数据用于体制检测
        spy_df = all_data.get("SPY")
        tlt_df = all_data.get("TLT")
        cl_df = all_data.get("CL")

        if spy_df is None or spy_df.empty:
            # 无数据时使用默认策略
            if self._sub_strategy is None:
                self._sub_strategy = self._get_sub_strategy("双动量")
            return self._sub_strategy.generate_weights(current_date, all_data, lookback_days)

        # 截取到当前日期的数据
        spy_close = spy_df[spy_df["date"] <= current_date]["close"]
        tlt_close = tlt_df[tlt_df["date"] <= current_date]["close"] if tlt_df is not None and not tlt_df.empty else pd.Series()
        cl_close = cl_df[cl_df["date"] <= current_date]["close"] if cl_df is not None and not cl_df.empty else pd.Series()

        # 检测体制
        detected_regime = "中性"
        if len(spy_close) >= 60 and not tlt_close.empty and not cl_close.empty:
            regime_info = detect_market_regime(spy_close, tlt_close, cl_close)
            detected_regime = regime_info.get("regime", "中性")

        # 防抖动逻辑
        if detected_regime == self._current_regime:
            self._consecutive_count += 1
        else:
            self._consecutive_count = 1

        # 每次再平衡增加持有期
        self._holding_periods += 1

        # 判断是否满足切换条件
        can_switch = (
            self._consecutive_count >= self.CONSECUTIVE_THRESHOLD
            and self._holding_periods >= self.MIN_HOLDING_PERIODS
            and detected_regime != self._current_regime
        )

        if can_switch or self._sub_strategy is None:
            # 首次初始化或满足切换条件
            target_name = _REGIME_STRATEGY_MAP.get(detected_regime, "双动量")
            self._sub_strategy = self._get_sub_strategy(target_name)
            self._current_regime = detected_regime
            self._holding_periods = 0

        # 委托子策略生成权重
        return self._sub_strategy.generate_weights(current_date, all_data, lookback_days)


def _calc_max_dd(close: pd.Series) -> float:
    """计算序列的最大回撤（负值）"""
    peak = close.cummax()
    dd = (close - peak) / peak
    return round(dd.min() * 100, 2)
