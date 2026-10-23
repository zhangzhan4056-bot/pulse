"""成长动量三轴参数扫描

单因子对照基线（top_n=8 / mom=63 / freq=M / 静态 55 股，2026-10 生产已切 top8）：
  A  top_n:    8 / 12 / 18
  B1 回看窗:   21 / 63 / 126
  B2 调仓频率: M / Q
  C  宇宙:     静态 55 / 流动性换血 top55

输出 data/sweep_growth.csv + 终端表。判定：Calmar 优先，CAGR 次之。

用法:
    python3 scripts/sweep_growth_momentum.py
    python3 scripts/sweep_growth_momentum.py --start 2019-01-01 --end 2025-12-31
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import BacktestEngine
from core.data.config import GROWTH_UNIVERSE, US_LARGE_CAP_UNIVERSE
from core.data.manager import DataManager
from core.strategy.strategies import StockMomentumGrowthStrategy


class _LiquidityVariant(StockMomentumGrowthStrategy):
    """流动性换血变体（仅 sweep 用，生产已否掉，不注册 P3）"""

    name = "成长动量·流动性换血"
    description = "100 母池按 252D 成交额取 top55，再 3M 动量 top12"

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced = {
            s: df[df["date"] <= current_date].tail(252)
            for s, df in all_data.items()
            if s in US_LARGE_CAP_UNIVERSE
        }
        liquid = []
        for s, df in sliced.items():
            if len(df) < 252:
                continue
            dollar = (df["close"] * df["volume"]).median()
            liquid.append((s, dollar))
        liquid.sort(key=lambda x: x[1], reverse=True)
        pool = [s for s, _ in liquid[:55]]
        self.stock_universe = {s: US_LARGE_CAP_UNIVERSE[s] for s in pool}
        return super().generate_weights(current_date, all_data, lookback_days)


def make_growth_variant(name, top_n=12, mom=63, freq="M", universe=None):
    """动态建变体类（仿 sweep_antifragile）"""

    class V(StockMomentumGrowthStrategy):
        pass

    V.name = name
    V.top_n = top_n
    V.mom_lookback = mom
    V.min_history = mom + 3
    V.rebalance_freq = freq
    if universe is not None:
        V.stock_universe = universe
        V.full_universe = {
            **universe,
            "SPY": "标普500 ETF",
            "QQQ": "纳斯达克100 ETF",
            "SHY": "1-3年短债 ETF",
        }
    return V()


def _stats(nav: pd.Series, label: str, axis: str) -> dict:
    nav = nav.dropna()
    if len(nav) < 2:
        return {}
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    dd = (nav / nav.cummax() - 1).min()
    vol = nav.pct_change().dropna().std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / vol if vol > 0 else np.nan
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    return {
        "axis": axis,
        "label": label,
        "cagr": cagr,
        "total": total,
        "dd": dd,
        "sharpe": sharpe,
        "calmar": calmar,
    }


def _roll_3y_median(nav: pd.Series) -> float:
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 37:
        return np.nan
    roll = [
        (monthly.iloc[i + 36] / monthly.iloc[i]) ** (1 / 3) - 1
        for i in range(len(monthly) - 36)
    ]
    return float(np.median(roll))


def load_data(end: str) -> dict:
    dm = DataManager()
    need = sorted(set(GROWTH_UNIVERSE) | set(US_LARGE_CAP_UNIVERSE))
    stale = dm.us_stocks_need_refresh("2018-01-01", end, symbols=need)
    if stale:
        print(f"补拉 {len(stale)} 只个股（其余已有覆盖）...")
        dm.fetch_us_stocks(outputsize=2000, symbols=stale)
    data = {}
    for s in need:
        df = dm.load(s, "2018-01-01", end)
        if not df.empty:
            data[s] = df
    print(f"loaded {len(data)} symbols")
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default="2025-12-31")
    args = p.parse_args()

    data = load_data(args.end)
    engine = BacktestEngine(risk_free_rate=0.02, commission_rate=0.001, warmup_days=0)

    base = StockMomentumGrowthStrategy()
    cases = [
        ("基线", base),
        # A top_n
        ("A top_n=8", make_growth_variant("top8", top_n=8)),
        ("A top_n=18", make_growth_variant("top18", top_n=18)),
        # B1 回看窗
        ("B1 mom=21", make_growth_variant("mom21", mom=21)),
        ("B1 mom=126", make_growth_variant("mom126", mom=126)),
        # B2 调仓频率
        ("B2 freq=Q", make_growth_variant("freqQ", freq="Q")),
        # C 宇宙换血
        ("C 流动性换血", _LiquidityVariant()),
    ]

    records = []
    navs = {}
    print(f"\n{'=' * 72}")
    print(f"成长动量三轴扫描  {args.start} → {args.end}  佣金 0.1%")
    print(f"{'=' * 72}")
    for axis_label, strat in cases:
        result = engine.run_comparison([strat], data, args.start, args.end)[0]
        if result.equity_curve.empty:
            print(f"{axis_label}: 无结果（数据不足）")
            continue
        nav = result.equity_curve
        navs[strat.name] = nav
        row = _stats(nav, strat.name, axis_label)
        row["roll3y"] = _roll_3y_median(nav)
        records.append(row)
        print(
            f"{axis_label:<16} {strat.name:<16} "
            f"CAGR {row['cagr'] * 100:6.1f}%  DD {row['dd'] * 100:6.1f}%  "
            f"Sharpe {row['sharpe']:5.2f}  Calmar {row['calmar']:5.2f}  "
            f"3Ymed {row['roll3y'] * 100:5.1f}%"
        )

    if not records:
        print("无有效结果")
        return

    df = pd.DataFrame(records)
    out = Path(__file__).parent.parent / "data" / "sweep_growth.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n已写入 {out}")

    print("\n=== 按 Calmar 排序 ===")
    print(f"{'轴':<8} {'变体':<16} {'CAGR':>8} {'MaxDD':>8} {'Calmar':>8} {'3Ymed':>8}")
    for r in sorted(records, key=lambda x: x["calmar"] if x["calmar"] == x["calmar"] else 0, reverse=True):
        print(
            f"{r['axis']:<8} {r['label']:<16} "
            f"{r['cagr'] * 100:7.1f}% {r['dd'] * 100:7.1f}% "
            f"{r['calmar']:8.2f} {r['roll3y'] * 100:7.1f}%"
        )

    print("\n判定口径：Calmar 优先（保本），CAGR 次之；MaxDD 恶化 >3pp 降权")
    print("生产策略 = 55 股 top8 3M 月频（2026-10 从 top12 切换）")


if __name__ == "__main__":
    main()
