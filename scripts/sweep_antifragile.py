"""反脆弱策略参数扫描

扫描周频/日频变体的 cost × OTM 参数网格，
输出 CSV 供离线分析。

Usage:
    python scripts/sweep_antifragile.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from core.data import DataManager
from core.backtest import BacktestEngine
from core.strategy.strategies import (
    BaseStrategy,
    _AntifragileBase,
    AntifragileStrategy,
    AntifragileAggressiveStrategy,
    STRATEGY_CATEGORIES,
)


# ── 参数网格 ──

WEEKLY_GRID = {
    "cost": [0.002, 0.003, 0.004, 0.005],  # 月成本
    "otm":  [0.02, 0.03, 0.04],
}

DAILY_GRID = {
    "cost": [0.006],  # 备用值（BS 模式下不使用）
    "otm":  [0.01, 0.015, 0.02, 0.03],
    "vol":  0.30,  # 日频期权 IV 高于月频
}

BASELINES = [
    ("月频标准版", AntifragileStrategy),
    ("月频激进版", AntifragileAggressiveStrategy),
]

START_DATE = "2015-01-01"
END_DATE = "2025-12-31"


def make_sweep_strategy(name, freq, cost, otm, vol=0.20, use_bs_cost=True):
    """动态创建扫描策略类"""

    class SweepStrategy(_AntifragileBase, BaseStrategy):
        pass

    SweepStrategy.name = name
    SweepStrategy.category = "反脆弱"
    SweepStrategy.rebalance_freq = freq
    SweepStrategy.hedge_monthly_cost = cost
    SweepStrategy.hedge_otm_threshold = otm
    SweepStrategy.hedge_leverage = 15.0
    SweepStrategy.hedge_vol = vol
    SweepStrategy.use_bs_cost = use_bs_cost
    return SweepStrategy()


def run_sweep():
    print("加载数据...")
    dm = DataManager()
    all_data = {}
    for sym in ["SPY", "QQQ", "TLT", "GLD"]:
        df = dm.storage.load(sym)
        if not df.empty:
            all_data[sym] = df

    if "SPY" not in all_data:
        print("错误: 无 SPY 数据，请先在 P1 页面获取数据")
        return

    engine = BacktestEngine()
    records = []

    # 基线
    print(f"\n{'='*60}")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print(f"{'='*60}")

    print("\n--- 基线（月频）---")
    for label, cls in BASELINES:
        strategy = cls()
        result = engine.run_comparison([strategy], all_data, START_DATE, END_DATE)[0]
        records.append({
            "name": label,
            "freq": "M",
            "monthly_cost": strategy.hedge_monthly_cost,
            "otm": strategy.hedge_otm_threshold,
            "annualized_return": result.annualized_return,
            "max_drawdown": result.max_drawdown,
            "sharpe_ratio": result.sharpe_ratio,
            "volatility": result.volatility,
            "win_rate": result.win_rate,
            "num_rebalances": result.num_rebalances,
        })
        print(f"  {label}: 年化={result.annualized_return:+.2f}%, "
              f"回撤={result.max_drawdown:.2f}%, 夏普={result.sharpe_ratio:.2f}")

    # 周频扫描
    print(f"\n--- 周频扫描 ({len(WEEKLY_GRID['cost'])} × {len(WEEKLY_GRID['otm'])} = "
          f"{len(WEEKLY_GRID['cost']) * len(WEEKLY_GRID['otm'])} 组合) ---")
    for cost in WEEKLY_GRID["cost"]:
        for otm in WEEKLY_GRID["otm"]:
            name = f"周频 c{cost*100:.1f}% o{otm*100:.0f}%"
            strategy = make_sweep_strategy(name, "W", cost, otm)
            result = engine.run_comparison([strategy], all_data, START_DATE, END_DATE)[0]
            records.append({
                "name": name,
                "freq": "W",
                "monthly_cost": cost,
                "otm": otm,
                "annualized_return": result.annualized_return,
                "max_drawdown": result.max_drawdown,
                "sharpe_ratio": result.sharpe_ratio,
                "volatility": result.volatility,
                "win_rate": result.win_rate,
                "num_rebalances": result.num_rebalances,
            })
            print(f"  {name}: 年化={result.annualized_return:+.2f}%, "
                  f"回撤={result.max_drawdown:.2f}%, 夏普={result.sharpe_ratio:.2f}")

    # 日频扫描
    print(f"\n--- 日频扫描 ({len(DAILY_GRID['cost'])} × {len(DAILY_GRID['otm'])} = "
          f"{len(DAILY_GRID['cost']) * len(DAILY_GRID['otm'])} 组合) ---")
    daily_vol = DAILY_GRID.get("vol", 0.20)
    for cost in DAILY_GRID["cost"]:
        for otm in DAILY_GRID["otm"]:
            name = f"日频 o{otm*100:.1f}% BS"
            strategy = make_sweep_strategy(name, "D", cost, otm, vol=daily_vol, use_bs_cost=True)
            result = engine.run_comparison([strategy], all_data, START_DATE, END_DATE)[0]
            records.append({
                "name": name,
                "freq": "D",
                "monthly_cost": cost,
                "otm": otm,
                "annualized_return": result.annualized_return,
                "max_drawdown": result.max_drawdown,
                "sharpe_ratio": result.sharpe_ratio,
                "volatility": result.volatility,
                "win_rate": result.win_rate,
                "num_rebalances": result.num_rebalances,
            })
            print(f"  {name}: 年化={result.annualized_return:+.2f}%, "
                  f"回撤={result.max_drawdown:.2f}%, 夏普={result.sharpe_ratio:.2f}")

    # 保存结果
    df = pd.DataFrame(records)
    output_path = Path(__file__).parent / "sweep_results.csv"
    df.to_csv(output_path, index=False)
    print(f"\n结果已保存到: {output_path}")

    # 按夏普排序输出 Top 10
    print(f"\n{'='*60}")
    print("Top 10（按夏普比率排序）:")
    print(f"{'='*60}")
    top10 = df.sort_values("sharpe_ratio", ascending=False).head(10)
    print(top10[["name", "annualized_return", "max_drawdown", "sharpe_ratio"]].to_string(index=False))


if __name__ == "__main__":
    run_sweep()
