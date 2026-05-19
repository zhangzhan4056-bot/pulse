"""策略类定义

11 种资产配置策略，统一 BaseStrategy 接口，用于回测引擎调用。
"""

from abc import ABC, abstractmethod
from typing import Dict, List

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
)
from core.data.config import US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS


# 所有可配置资产
ALL_SYMBOLS = list(US_SYMBOLS.keys()) + list(CN_SYMBOLS.keys()) + list(GLOBAL_SYMBOLS.keys())


# 策略分类定义
STRATEGY_CATEGORIES = {
    "趋势动量": {
        "label": "  趋势/动量",
        "subtitle": "涨的继续涨",
        "description": "核心假设：价格有惯性。过去表现好的资产未来还会好。",
        "market_view": "牛市吃肉，熊市挨打；震荡市容易被来回割",
    },
    "均值回归": {
        "label": "⏪ 均值回归",
        "subtitle": "跌多了会反弹",
        "description": "核心假设：价格围绕价值波动，偏离过大后会回归。",
        "market_view": "与趋势策略天然对冲；震荡市赚钱，单边市容易抄在半山腰",
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
    description = "均线多头排列时持有，空头或交织时清仓，MACD 确认"
    category = "趋势动量"

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


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略

    RSI 超卖时买入，超买时卖出。
    与动量/趋势策略天然对冲。
    """

    name = "均值回归策略"
    description = "RSI 超卖抄底，超买离场，与动量策略对冲"
    category = "均值回归"

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 计算各资产 RSI
        rsi_map = {}
        for symbol, close in sliced.items():
            if len(close) < 20:
                continue
            rsi_series = calc_rsi(close, period=14)
            if not rsi_series.empty:
                rsi_value = rsi_series.iloc[-1]
                if not np.isnan(rsi_value):
                    rsi_map[symbol] = rsi_value

        if not rsi_map:
            return {}

        # RSI 越低权重越高（超卖反弹机会）
        # RSI < 30: 高权重, 30-50: 正常权重, 50-70: 低权重, >70: 零权重
        weights = {}
        for symbol, rsi in rsi_map.items():
            if rsi >= 70:
                # 超买，不持有
                continue
            elif rsi >= 50:
                # 偏高，低权重
                weights[symbol] = 0.3
            elif rsi >= 30:
                # 正常，标准权重
                weights[symbol] = 0.7
            else:
                # 超卖，高权重
                weights[symbol] = 1.0

        if not weights:
            return {}

        # 归一化，保留 5% 现金
        total = sum(weights.values())
        investable = 0.95
        weights = {s: (w / total) * investable for s, w in weights.items()}

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

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        sliced = _slice_data(all_data, current_date, lookback_days)

        # 计算各资产 12 个月收益率（绝对动量）
        returns_12m = {}
        for symbol, close in sliced.items():
            if len(close) >= 252:
                ret = (close.iloc[-1] / close.iloc[-252] - 1) * 100
                returns_12m[symbol] = ret

        if not returns_12m:
            return {}

        # 绝对动量过滤：只保留正收益的资产
        positive = {s: r for s, r in returns_12m.items() if r > 0}

        if not positive:
            # 所有资产收益为负 → 全部持有现金
            return {}

        # 相对动量排序，选前 N 个
        ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
        selected = ranked[: self.top_n]

        # 等权分配
        weight = 1.0 / len(selected)
        return {s: weight for s, _ in selected}


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


class AntifragileStrategy(BaseStrategy):
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
    hedge_monthly_cost: float = 0.003   # 每月 0.3% 权利金
    hedge_otm_threshold: float = 0.05   # 5% OTM
    hedge_leverage: float = 15.0        # 向后兼容参数，BS 公式不依赖此值

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        # 主仓：复用双动量逻辑
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


class AntifragileAggressiveStrategy(BaseStrategy):
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
    hedge_monthly_cost: float = 0.005   # 每月 0.5% 权利金（年化 6%）
    hedge_otm_threshold: float = 0.03   # 3% OTM（更频繁触发）
    hedge_leverage: float = 15.0        # 向后兼容参数

    def generate_weights(
        self,
        current_date: pd.Timestamp,
        all_data: Dict[str, pd.DataFrame],
        lookback_days: int = 252,
    ) -> Dict[str, float]:
        # 主仓：复用双动量逻辑
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


def _calc_max_dd(close: pd.Series) -> float:
    """计算序列的最大回撤（负值）"""
    peak = close.cummax()
    dd = (close - peak) / peak
    return round(dd.min() * 100, 2)
