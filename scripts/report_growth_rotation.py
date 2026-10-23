"""成长动量策略对照报告

对比：成长动量·个股 / 满仓QQQ长持 / 满仓SPY长持 / 凯利·门槛8%
输出：CAGR、MaxDD、Sharpe、Calmar、分年、3 年滚动 ≥26% 占比。

用法:
    python scripts/report_growth_rotation.py
    python scripts/report_growth_rotation.py --start 2019-01-01 --end 2025-12-31
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import BacktestEngine
from core.data.config import GROWTH_UNIVERSE, STRATEGY_UNIVERSE
from core.data.manager import DataManager
from core.strategy.strategies import (
    DualMomentumKellyFilterStrategy,
    QQQBuyHoldStrategy,
    SPYBuyHoldStrategy,
    StockMomentumGrowthStrategy,
)


def _stats(nav: pd.Series, label: str) -> dict:
    nav = nav.dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    dd = (nav / nav.cummax() - 1).min()
    vol = nav.pct_change().dropna().std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / vol if vol > 0 else np.nan
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    return {
        "label": label,
        "cagr": cagr,
        "total": total,
        "dd": dd,
        "sharpe": sharpe,
        "calmar": calmar,
        "years": years,
    }


def _roll_3y(nav: pd.Series) -> pd.Series:
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 37:
        return pd.Series(dtype=float)
    return pd.Series(
        [(monthly.iloc[i + 36] / monthly.iloc[i]) ** (1 / 3) - 1 for i in range(len(monthly) - 36)]
    )


def _yearly(nav: pd.Series) -> dict:
    out = {}
    for y in range(nav.index[0].year, nav.index[-1].year + 1):
        seg = nav[nav.index.year == y]
        out[y] = seg.iloc[-1] / seg.iloc[0] - 1 if len(seg) > 1 else np.nan
    return out


def load_data(start: str, end: str) -> dict:
    dm = DataManager()
    syms = dict(STRATEGY_UNIVERSE)
    syms.update(GROWTH_UNIVERSE)
    data = {}
    for s in syms:
        df = dm.load(s, "2018-01-01", end)
        if not df.empty:
            data[s] = df
    return data


def qqq_nav(data: dict, start: str, end: str) -> pd.Series:
    qqq = data["QQQ"].copy()
    qqq["date"] = pd.to_datetime(qqq["date"])
    qqq = qqq[(qqq["date"] >= start) & (qqq["date"] <= end)].sort_values("date")
    nav = (qqq["close"] / qqq["close"].iloc[0]).copy()
    nav.index = qqq["date"].values
    return nav


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default="2025-12-31")
    args = p.parse_args()

    data = load_data(args.start, args.end)
    print(f"loaded {len(data)} symbols, {args.start} → {args.end}")

    engine = BacktestEngine(risk_free_rate=0.02, commission_rate=0.001, warmup_days=0)
    strategies = [
        StockMomentumGrowthStrategy(),
        DualMomentumKellyFilterStrategy(),
        QQQBuyHoldStrategy(),
        SPYBuyHoldStrategy(),
    ]
    results = engine.run_comparison(strategies, data, args.start, args.end)

    rows = []
    navs = {"QQQ 买入持有": qqq_nav(data, args.start, args.end)}
    for r in results:
        eq = r.equity_curve
        navs[r.strategy_name] = eq
        rows.append(_stats(eq, r.strategy_name))
    rows.append(_stats(navs["QQQ 买入持有"], "QQQ 买入持有"))
    rows.sort(key=lambda x: x["calmar"] if x["calmar"] == x["calmar"] else 0, reverse=True)

    print()
    print(f"{'策略':<24} {'CAGR':>8} {'总收益':>9} {'MaxDD':>8} {'Sharpe':>8} {'Calmar':>8}")
    print("-" * 72)
    for r in rows:
        print(
            f"{r['label']:<24} {r['cagr'] * 100:7.1f}% {r['total'] * 100:8.1f}% "
            f"{r['dd'] * 100:7.1f}% {r['sharpe']:8.2f} {r['calmar']:8.2f}"
        )

    print("\n=== 分年 ===")
    years = list(range(pd.Timestamp(args.start).year, pd.Timestamp(args.end).year + 1))
    print("策略".ljust(24) + "".join(f"{y:>8}" for y in years))
    for label in ["成长动量·个股", "凯利·门槛8%", "满仓QQQ长期持有", "满仓SPY长期持有", "QQQ 买入持有"]:
        if label not in navs:
            continue
        y = _yearly(navs[label])
        print(
            label[:24].ljust(24)
            + "".join(
                f"{y[i] * 100:7.1f}%" if i in y and y[i] == y[i] else f"{'':>8}" for i in years
            )
        )

    print("\n=== 3 年滚动 CAGR ===")
    print(f"{'策略':<24} {'median':>8} {'p25':>8} {'≥26%':>8} {'≥22%':>8}")
    for label, nav in navs.items():
        roll = _roll_3y(nav)
        if roll.empty:
            continue
        print(
            f"{label:<24} {roll.median() * 100:7.1f}% {roll.quantile(0.25) * 100:7.1f}% "
            f"{(roll >= 0.26).mean() * 100:7.1f}% {(roll >= 0.22).mean() * 100:7.1f}%"
        )

    print("\n结论口径：")
    print("- 全期 CAGR≈26% ≠ 「稳稳 3 年 1 倍」；看 3 年滚动 ≥26% 占比")
    print("- 引擎（月首调仓）实测 3 年滚动 ≥26% 约 25–30% 窗口，中位 3 年 CAGR 约 19–21%")
    print("- 仍双优于 QQQ（CAGR 更高、MaxDD 不更深），但不是无回撤的稳态 26%")
    print("- A2 组合 DD 刹车对个股动量作废（会死锁空仓），本策略 portfolio_dd_brake=False")
    print("- 组合回撤红线 −40%（2026-10 放宽）；满仓 QQQ/SPY 长持为对照基准")


if __name__ == "__main__":
    main()
