#!/usr/bin/env python3
"""成长动量·个股 权重方案扫描（top8 固定，只动配权）

基线 = 生产：8 只等权满仓（每只 12.5%），单票顶 20%。
  W0 等权满仓          w=1/n
  W1 动量加权          w∝3M涨幅
  W2 排名衰减          w∝(n+1-rank)
  W3a 凯利 k=0.25      w=clip(k·μ̂/σ², 0, 20%)
  W3b 凯利 k=0.5
  W4 广度仓位          每只固定 12.5%，总仓=n/8，票少自动留现金
  W5 排名衰减+广度     W2 的相对权重 × W4 的总仓

加权后 clip 20% 不回补（与 inverse_vol_weights 同口径），差额进 SHY。
判定：Calmar 优先，CAGR 次之，MaxDD 恶化 >3pp 降权。

用法:
    python3 scripts/sweep_growth_weights.py
    python3 scripts/sweep_growth_weights.py --start 2019-01-01 --end 2025-12-31
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import BacktestEngine
from core.data.config import GROWTH_UNIVERSE
from core.data.manager import DataManager
from core.strategy.indicators import calc_volatility
from core.strategy.kelly import kelly_weights, mu_from_momentum
from core.strategy.strategies import StockMomentumGrowthStrategy, _slice_data


def _cap_map(raw: dict, cap: float = 0.20) -> dict:
    """clip 单票硬顶，不回补（差额留给引擎进 SHY）"""
    return {s: min(w, cap) for s, w in raw.items() if w > 1e-6}


class _WeightBase(StockMomentumGrowthStrategy):
    """权重变体共用：切片 → 正动量 → top8 → 配权 → 防御腿。不改生产类。"""

    def _select(self, current_date, all_data, lookback_days=252):
        """返回 (sliced, ranked)。ranked 为 top8 正动量 [(sym, 3M_ret)]，无则 ranked=[]"""
        sliced = _slice_data(all_data, current_date, max(lookback_days, 300))
        returns = {}
        for symbol in self.stock_universe:
            close = sliced.get(symbol)
            if close is None or len(close) < self.min_history:
                continue
            ret = close.iloc[-1] / close.iloc[-self.mom_lookback] - 1
            if ret > 0:
                returns[symbol] = ret
        ranked = sorted(returns.items(), key=lambda x: x[1], reverse=True)[: self.top_n]
        return sliced, ranked

    def _finish(self, weights: dict, sliced) -> dict:
        for s, w in self._defense_leg(sliced).items():
            weights[s] = weights.get(s, 0.0) + w
        return {s: w for s, w in weights.items() if w > 1e-6}


class _EqualFull(_WeightBase):
    """W0 基线 = 生产：等权满仓"""

    name = "W0 等权满仓"
    description = "top8 等权，1/n，clip 20%"


class _MomWeighted(_WeightBase):
    """W1 动量加权：w ∝ 3M 涨幅，归一到 1.0 后 clip"""

    name = "W1 动量加权"
    description = "w∝3M涨幅，归一后 clip 20%"

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced, ranked = self._select(current_date, all_data, lookback_days)
        if not ranked:
            return self._finish({}, sliced)
        total = sum(r for _, r in ranked)
        raw = {s: r / total for s, r in ranked}
        return self._finish(_cap_map(raw, self.per_name_cap), sliced)


class _RankDecay(_WeightBase):
    """W2 排名衰减：w ∝ (n+1-rank)，归一到 1.0 后 clip"""

    name = "W2 排名衰减"
    description = "w∝(n+1-rank)，归一后 clip 20%"

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced, ranked = self._select(current_date, all_data, lookback_days)
        if not ranked:
            return self._finish({}, sliced)
        n = len(ranked)
        raw = {s: (n - i) / (n * (n + 1) / 2) for i, (s, _) in enumerate(ranked)}
        return self._finish(_cap_map(raw, self.per_name_cap), sliced)


class _KellyVariant(_WeightBase):
    """W3 凯利：top8 正动量内按 μ̂/σ² 配权，k 与 clip 由子类定"""

    name = "W3 凯利"
    description = "kelly_weights(k) on top8"
    kelly_k: float = 0.25

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced, ranked = self._select(current_date, all_data, lookback_days)
        if not ranked:
            return self._finish({}, sliced)
        mu_map, vol_map = {}, {}
        for s, ret in ranked:
            close = sliced[s]
            mu_map[s] = mu_from_momentum(ret * 100.0)
            vol = calc_volatility(close, window=20)
            if vol and vol > 0:
                vol_map[s] = vol / 100.0
        raw = kelly_weights(
            mu_map, vol_map,
            k=self.kelly_k,
            max_weight=self.per_name_cap,
        )
        return self._finish(raw, sliced)


class _Kelly25(_KellyVariant):
    name = "W3a 凯利 k=0.25"
    description = "1/4 凯利，单票顶 20%"
    kelly_k = 0.25


class _Kelly50(_KellyVariant):
    name = "W3b 凯利 k=0.5"
    description = "1/2 凯利，单票顶 20%"
    kelly_k = 0.5


class _BreadthEqual(_WeightBase):
    """W4 广度仓位：每只固定 12.5%，总仓 = n/8，票少自动留现金"""

    name = "W4 广度仓位"
    description = "每只 12.5% 固定，总仓 n/8，票少留现金"

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced, ranked = self._select(current_date, all_data, lookback_days)
        if not ranked:
            return self._finish({}, sliced)
        per = min(self.stock_budget / self.top_n, self.per_name_cap)  # 1/8=12.5%
        raw = {s: per for s, _ in ranked}
        return self._finish(raw, sliced)


class _RankDecayBreadth(_WeightBase):
    """W5 排名衰减 + 广度：相对权重 ∝(n+1-rank)，总仓 = n/8"""

    name = "W5 衰减+广度"
    description = "排名衰减相对权重 × 总仓 n/8"

    def generate_weights(self, current_date, all_data, lookback_days=252):
        sliced, ranked = self._select(current_date, all_data, lookback_days)
        if not ranked:
            return self._finish({}, sliced)
        n = len(ranked)
        budget = self.stock_budget * n / self.top_n
        score_sum = n * (n + 1) / 2
        raw = {s: budget * (n - i) / score_sum for i, (s, _) in enumerate(ranked)}
        return self._finish(_cap_map(raw, self.per_name_cap), sliced)


def _stats(nav: pd.Series, label: str) -> dict:
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
        "label": label,
        "cagr": cagr,
        "total": total,
        "dd": dd,
        "sharpe": sharpe,
        "calmar": calmar,
    }


def _roll3(nav: pd.Series):
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 37:
        return np.nan, np.nan, np.nan
    roll = [
        (monthly.iloc[i + 36] / monthly.iloc[i]) ** (1 / 3) - 1
        for i in range(len(monthly) - 36)
    ]
    return (
        float(np.median(roll)),
        float(np.percentile(roll, 25)),
        float(np.mean([r >= 0.26 for r in roll])),
    )


def _yearly(nav: pd.Series) -> dict:
    out = {}
    for y in range(nav.index[0].year, nav.index[-1].year + 1):
        seg = nav[nav.index.year == y]
        if len(seg) > 1:
            out[y] = seg.iloc[-1] / seg.iloc[0] - 1
    return out


def load_data(end: str) -> dict:
    dm = DataManager()
    need = sorted(GROWTH_UNIVERSE)
    stale = dm.us_stocks_need_refresh("2018-01-01", end, symbols=need)
    if stale:
        print(f"补拉 {len(stale)} 只（其余已有覆盖）...")
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

    cases = [
        _EqualFull(),
        _MomWeighted(),
        _RankDecay(),
        _Kelly25(),
        _Kelly50(),
        _BreadthEqual(),
        _RankDecayBreadth(),
    ]

    records = []
    yearly_all = {}
    print(f"\n{'=' * 78}")
    print(f"成长动量权重扫描  {args.start} → {args.end}  佣金 0.1%  top8 固定")
    print(f"{'=' * 78}")
    for strat in cases:
        result = engine.run_comparison([strat], data, args.start, args.end)[0]
        if result.equity_curve.empty:
            print(f"{strat.name}: 无结果")
            continue
        nav = result.equity_curve
        row = _stats(nav, strat.name)
        med3, p25, hit26 = _roll3(nav)
        row["roll3y_med"] = med3
        row["roll3y_p25"] = p25
        row["hit26"] = hit26
        records.append(row)
        yearly_all[strat.name] = _yearly(nav)
        print(
            f"{strat.name:<14} "
            f"CAGR {row['cagr'] * 100:6.1f}%  DD {row['dd'] * 100:6.1f}%  "
            f"Sharpe {row['sharpe']:5.2f}  Calmar {row['calmar']:5.2f}  "
            f"3Ymed {med3 * 100:5.1f}%  ≥26% {hit26 * 100:4.1f}%"
        )

    if not records:
        print("无有效结果")
        return

    df = pd.DataFrame(records)
    out = Path(__file__).parent.parent / "data" / "sweep_growth_weights.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n已写入 {out}")

    print("\n=== 按 Calmar 排序 ===")
    print(f"{'方案':<14} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Calmar':>8} {'3Ymed':>8} {'≥26%':>8}")
    for r in sorted(records, key=lambda x: x["calmar"] if x["calmar"] == x["calmar"] else 0, reverse=True):
        print(
            f"{r['label']:<14} "
            f"{r['cagr'] * 100:7.1f}% {r['dd'] * 100:7.1f}% "
            f"{r['sharpe']:8.2f} {r['calmar']:8.2f} "
            f"{r['roll3y_med'] * 100:7.1f}% {r['hit26'] * 100:7.1f}%"
        )

    years = sorted({y for ym in yearly_all.values() for y in ym})
    print("\n=== 分年收益 ===")
    header = f"{'方案':<14}" + "".join(f"{y:>8}" for y in years)
    print(header)
    for name, ym in yearly_all.items():
        line = f"{name:<14}"
        for y in years:
            v = ym.get(y)
            line += f"{v * 100:7.1f}%" if v == v else f"{'—':>8}"
        print(line)

    print("\n判定口径：Calmar 优先（保本），CAGR 次之；MaxDD 恶化 >3pp 降权")
    print("生产 = W1 动量加权（2026-10 切换）；其余方案仅作对照")


if __name__ == "__main__":
    main()
