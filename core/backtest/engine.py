"""回测引擎 - 模拟策略在历史数据上的表现"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.strategy.indicators import calc_drawdown


@dataclass
class BacktestResult:
    """回测结果"""

    strategy_name: str
    equity_curve: pd.Series  # index=date, value=组合净值 (起始=1.0)
    drawdown_curve: pd.Series  # index=date, value=回撤 (负值)
    daily_returns: pd.Series  # index=date, value=日收益率
    total_return: float  # 总收益率 (%)
    annualized_return: float  # 年化收益率 (%)
    max_drawdown: float  # 最大回撤 (%)
    sharpe_ratio: float  # 夏普比率
    volatility: float  # 年化波动率 (%)
    win_rate: float  # 月度胜率 (%)
    num_rebalances: int  # 再平衡次数
    weight_history: pd.DataFrame  # 每次再平衡的权重记录


class BacktestEngine:
    """回测引擎 - 月度再平衡，按日计算组合收益"""

    def __init__(
        self,
        risk_free_rate: float = 0.03,
        commission_rate: float = 0.001,
        warmup_days: int = 252,
    ):
        """
        Args:
            risk_free_rate: 无风险利率，默认 3%
            commission_rate: 交易佣金率，默认 0.1%
            warmup_days: 指标预热天数，跳过前 N 天
        """
        self.risk_free_rate = risk_free_rate
        self.commission_rate = commission_rate
        self.warmup_days = warmup_days

    def run(
        self,
        strategy,
        all_data: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        hedge_monthly_cost: Optional[float] = None,
        hedge_otm_threshold: float = 0.05,
        hedge_leverage: float = 7.0,
    ) -> BacktestResult:
        """执行单个策略的回测

        Args:
            strategy: BaseStrategy 实例
            all_data: {symbol: DataFrame} 完整历史数据
            start_date: 回测开始日期 "YYYY-MM-DD"
            end_date: 回测结束日期 "YYYY-MM-DD"
            hedge_monthly_cost: 尾部对冲月成本比例（如 0.015 = 1.5%），None 表示无对冲
            hedge_otm_threshold: OTM put 虚值比例（如 0.05 = 5% OTM）
            hedge_leverage: 期权杠杆系数（虚值 put 的收益放大倍数）

        Returns:
            BacktestResult 回测结果
        """
        # 获取交易日序列（以 SPY 为基准）
        ref_symbol = "SPY" if "SPY" in all_data else list(all_data.keys())[0]
        ref_df = all_data[ref_symbol]
        trading_dates = ref_df[
            (ref_df["date"] >= start_date) & (ref_df["date"] <= end_date)
        ]["date"].reset_index(drop=True)

        if len(trading_dates) < 2:
            return self._empty_result(strategy.name)

        # 跳过预热期
        if len(trading_dates) > self.warmup_days:
            trading_dates = trading_dates.iloc[self.warmup_days:]
            trading_dates = trading_dates.reset_index(drop=True)

        # 获取再平衡日期（每月第一个交易日）
        rebalance_dates = self._get_rebalance_dates(trading_dates)

        if not rebalance_dates:
            return self._empty_result(strategy.name)

        # 初始化
        portfolio_value = 1.0
        current_weights = {}  # {symbol: weight}
        equity_records = []
        return_records = []
        weight_history = []
        hedge_costs = []
        hedge_payoffs = []

        # 构建各资产的日期索引，加速查找
        asset_indexed = {}
        for symbol, df in all_data.items():
            if not df.empty:
                asset_indexed[symbol] = df.set_index("date")["close"]

        # SPY 价格索引（用于计算月度收益 + 对冲收益）
        spy_series = asset_indexed.get("SPY")
        spy_period_start_price = None  # 上一再平衡日的 SPY 价格

        # 逐再平衡周期模拟
        for i, rebal_date in enumerate(rebalance_dates):
            # --- 尾部对冲逻辑 ---
            period_hedge_cost = 0.0
            period_hedge_payoff = 0.0

            if hedge_monthly_cost is not None and spy_series is not None:
                # 扣除对冲成本
                period_hedge_cost = hedge_monthly_cost
                portfolio_value *= (1 - period_hedge_cost)
                hedge_costs.append({"date": rebal_date, "cost": period_hedge_cost})

                # 计算上月 SPY 收益，判断是否有对冲赔付
                if spy_period_start_price is not None:
                    spy_available = spy_series[spy_series.index <= rebal_date]
                    if len(spy_available) > 0:
                        spy_current = spy_available.iloc[-1]
                        spy_monthly_return = (spy_current / spy_period_start_price) - 1

                        # SPY 月跌幅超过 OTM 阈值 → 期权产生收益
                        if spy_monthly_return < -hedge_otm_threshold:
                            excess_drop = abs(spy_monthly_return) - hedge_otm_threshold
                            period_hedge_payoff = excess_drop * hedge_leverage
                            portfolio_value *= (1 + period_hedge_payoff)
                            hedge_payoffs.append({
                                "date": rebal_date,
                                "spy_return": spy_monthly_return,
                                "payoff": period_hedge_payoff,
                            })

                # 记录本周期起始 SPY 价格
                spy_available = spy_series[spy_series.index <= rebal_date]
                if len(spy_available) > 0:
                    spy_period_start_price = spy_available.iloc[-1]

            # 策略计算目标权重
            data_window = {
                sym: df[df["date"] <= rebal_date]
                for sym, df in all_data.items()
                if not df.empty
            }
            try:
                new_weights = strategy.generate_weights(rebal_date, data_window)
            except Exception:
                new_weights = current_weights

            # 记录权重
            weight_record = {"date": rebal_date}
            weight_record.update(new_weights)
            weight_history.append(weight_record)

            # 计算换手成本
            all_symbols = set(list(current_weights.keys()) + list(new_weights.keys()))
            turnover = sum(
                abs(new_weights.get(s, 0) - current_weights.get(s, 0))
                for s in all_symbols
            )
            cost = turnover * self.commission_rate
            portfolio_value *= (1 - cost)

            current_weights = new_weights

            # 确定本周期结束日期
            if i + 1 < len(rebalance_dates):
                period_end = rebalance_dates[i + 1]
            else:
                period_end = trading_dates.iloc[-1]

            # 本周期内的交易日
            period_mask = (trading_dates >= rebal_date) & (trading_dates < period_end)
            period_dates = trading_dates[period_mask]

            # 按日计算组合收益
            prev_date = rebal_date
            for date in period_dates:
                daily_return = 0.0
                for symbol, weight in current_weights.items():
                    if weight == 0 or symbol not in asset_indexed:
                        continue
                    series = asset_indexed[symbol]
                    # 获取当日和前一日收盘价
                    available = series[series.index <= date]
                    if len(available) < 2:
                        continue
                    today_price = available.iloc[-1]
                    prev_available = series[series.index <= prev_date]
                    if len(prev_available) == 0:
                        continue
                    prev_price = prev_available.iloc[-1]
                    if prev_price > 0:
                        asset_return = (today_price / prev_price) - 1
                        daily_return += weight * asset_return

                portfolio_value *= (1 + daily_return)
                equity_records.append({"date": date, "value": portfolio_value})
                return_records.append({"date": date, "return": daily_return})
                prev_date = date

        if not equity_records:
            return self._empty_result(strategy.name)

        # 构建结果
        equity_df = pd.DataFrame(equity_records).set_index("date")
        equity_curve = equity_df["value"]
        daily_returns = pd.DataFrame(return_records).set_index("date")["return"]
        drawdown_curve = calc_drawdown(equity_curve)

        # 计算指标
        metrics = self._calc_metrics(equity_curve, daily_returns)

        return BacktestResult(
            strategy_name=strategy.name,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            daily_returns=daily_returns,
            num_rebalances=len(weight_history),
            weight_history=pd.DataFrame(weight_history),
            **metrics,
        )

    def run_comparison(
        self,
        strategies: list,
        all_data: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> List[BacktestResult]:
        """批量运行多个策略

        如果策略对象有 hedge_monthly_cost 属性，自动启用尾部对冲。
        """
        results = []
        for strategy in strategies:
            hedge_cost = getattr(strategy, "hedge_monthly_cost", None)
            hedge_otm = getattr(strategy, "hedge_otm_threshold", 0.05)
            hedge_lev = getattr(strategy, "hedge_leverage", 7.0)
            result = self.run(
                strategy, all_data, start_date, end_date,
                hedge_monthly_cost=hedge_cost,
                hedge_otm_threshold=hedge_otm,
                hedge_leverage=hedge_lev,
            )
            results.append(result)
        return results

    def _get_rebalance_dates(self, trading_dates: pd.Series) -> List[pd.Timestamp]:
        """获取再平衡日期（每月第一个交易日）"""
        dates = pd.to_datetime(trading_dates)
        # 按年月分组，取每月第一个交易日
        df = pd.DataFrame({"date": dates})
        df["year_month"] = df["date"].dt.to_period("M")
        rebalance = df.groupby("year_month")["date"].first().tolist()
        return rebalance

    def _calc_metrics(
        self, equity_curve: pd.Series, daily_returns: pd.Series
    ) -> dict:
        """计算回测指标"""
        trading_days = len(daily_returns)
        if trading_days == 0:
            return {
                "total_return": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "volatility": 0.0,
                "win_rate": 0.0,
            }

        # 总收益率
        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100

        # 年化收益率
        years = trading_days / 252
        if years > 0 and equity_curve.iloc[0] > 0:
            annualized_return = (
                (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / years) - 1
            ) * 100
        else:
            annualized_return = 0.0

        # 最大回撤
        peak = equity_curve.cummax()
        drawdown = (equity_curve - peak) / peak
        max_drawdown = drawdown.min() * 100

        # 年化波动率
        annual_vol = daily_returns.std() * np.sqrt(252) * 100

        # 夏普比率
        if annual_vol > 0:
            sharpe = (annualized_return / 100 - self.risk_free_rate) / (
                annual_vol / 100
            )
        else:
            sharpe = 0.0

        # 月度胜率
        monthly_returns = daily_returns.resample("ME").apply(
            lambda x: (1 + x).prod() - 1
        )
        if len(monthly_returns) > 0:
            win_rate = (monthly_returns > 0).sum() / len(monthly_returns) * 100
        else:
            win_rate = 0.0

        return {
            "total_return": round(total_return, 2),
            "annualized_return": round(annualized_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 2),
            "volatility": round(annual_vol, 2),
            "win_rate": round(win_rate, 1),
        }

    def _empty_result(self, strategy_name: str) -> BacktestResult:
        """返回空结果（数据不足时）"""
        empty_series = pd.Series(dtype=float)
        return BacktestResult(
            strategy_name=strategy_name,
            equity_curve=empty_series,
            drawdown_curve=empty_series,
            daily_returns=empty_series,
            total_return=0.0,
            annualized_return=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            volatility=0.0,
            win_rate=0.0,
            num_rebalances=0,
            weight_history=pd.DataFrame(),
        )
