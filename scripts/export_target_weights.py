"""月度目标权重导出 — 双账户路径 1（信号统一，执行拆分）

生产策略：成长动量·个股（StockMomentumGrowthStrategy，2026-10 切换）。
与飞书月报共用 build_monthly_allocation，月报只输出这一套方案。

用法（每月第一个交易日收盘后）:
    python3 scripts/export_target_weights.py

输出到 data/target_weights/YYYY-MM-DD/:
    tiger.json   — 老虎/OpenAPI 可吃的美股持仓
    domestic.csv — 境内券商手动/QMT 持仓
    summary.md   — 人类可读的本月调仓单

执行约定：
    - 本策略全为美股个股，境内账户通常为空（保持货基）
    - 美股部分：当晚开盘后下单
    - 单票 20% 顶；回撤预算 −40%，无自动刹车（A2 对个股动量会死锁空仓）
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.manager import DataManager
from core.data.config import GROWTH_UNIVERSE, CASH_PROXY, CN_SYMBOLS
from core.strategy.strategies import StockMomentumGrowthStrategy
from core.backtest.engine import BacktestEngine

# 双账户拆分（路径 1）
# 本策略持仓全为美股个股 → 老虎；境内账户通常为空
DOMESTIC_SYMBOLS = list(CN_SYMBOLS.keys())
TIGER_PROXY = {"日经225": "EWJ", "KOSPI": "EWY"}


def build_monthly_allocation(end: Optional[str] = None) -> dict:
    """计算本月目标权重 + 近一年权重路径，供导出脚本和飞书月报共用。"""
    dm = DataManager()
    end = end or datetime.now().strftime("%Y-%m-%d")
    # 生产策略：成长动量·个股（2026-10 切 top8 动量加权）。3M 动量 top8，w∝涨幅
    strategy = StockMomentumGrowthStrategy()

    # 加载成长动量宇宙（55 股 + SPY/QQQ/SHY）；缺数才补拉
    all_data = {}
    missing = []
    for symbol in GROWTH_UNIVERSE:
        df = dm.load(symbol, "2017-06-01", end)
        if not df.empty:
            all_data[symbol] = df
        else:
            missing.append(symbol)
    if missing:
        try:
            dm.fetch_us_stocks(outputsize=250, sleep_sec=8.0, symbols=missing)
            for symbol in missing:
                df = dm.load(symbol, "2017-06-01", end)
                if not df.empty:
                    all_data[symbol] = df
        except Exception as e:
            print(f"补拉个股数据失败（沿用本地已有数据）: {e}")

    # 无 A2：个股动量天然回撤 −30%+，自动刹车会死锁空仓；红线 −40% 人工扛
    result = BacktestEngine(commission_rate=0.001).run(
        strategy,
        all_data,
        start_date="2019-01-01",
        end_date=end,
        portfolio_dd_brake=False,
        rebalance_freq="M",
    )
    if result.equity_curve.empty:
        raise RuntimeError("无权重输出（数据不足）")

    # 用「今天」的信号，而不是上月再平衡日的权重
    as_of = result.equity_curve.index[-1]
    raw = strategy.generate_weights(as_of, all_data)
    raw = {s: float(w) for s, w in (raw or {}).items() if float(w) > 1e-6}
    weights = raw
    brake_action = "none"

    # 闲钱进短债
    total_w = sum(weights.values())
    if total_w < 1.0 - 1e-9:
        weights[CASH_PROXY] = weights.get(CASH_PROXY, 0.0) + (1.0 - total_w)
    weights = {s: w for s, w in weights.items() if w > 1e-6}

    tiger_w = {}
    domestic_w = {}
    for sym, w in weights.items():
        if sym in DOMESTIC_SYMBOLS:
            domestic_w[sym] = w
        elif sym in TIGER_PROXY:
            tiger_w[TIGER_PROXY[sym]] = w
        else:
            tiger_w[sym] = w

    eq = result.equity_curve
    current_dd = float(eq.iloc[-1] / eq.cummax().iloc[-1] - 1.0)

    # 分年对照：策略 vs 满仓 QQQ / SPY（买入持有）
    # 起点对齐策略净值首日，避免「基准从 2019、策略预热后才有」的不公平对照
    import pandas as pd

    eq_start = pd.to_datetime(eq.index[0])

    def _bench_yearly(sym: str) -> dict:
        df = all_data.get(sym)
        if df is None or len(df) < 2:
            return {}
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d[(d["date"] >= eq_start) & (d["date"] <= as_of)].sort_values("date")
        if len(d) < 2:
            return {}
        out = {}
        for y in sorted(set(d["date"].dt.year)):
            seg = d[d["date"].dt.year == y]
            if len(seg) < 2:
                continue
            prev = d[d["date"].dt.year < y]
            start = float(prev["close"].iloc[-1]) if len(prev) else float(seg["close"].iloc[0])
            out[y] = float(seg["close"].iloc[-1] / start - 1.0)
        return out

    def _bench_total(sym: str) -> dict:
        df = all_data.get(sym)
        if df is None or len(df) < 2:
            return {}
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d[(d["date"] >= eq_start) & (d["date"] <= as_of)].sort_values("date")
        if len(d) < 2:
            return {}
        nav = d["close"] / float(d["close"].iloc[0])
        years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
        total = float(nav.iloc[-1] - 1.0)
        cagr = (1.0 + total) ** (1 / years) - 1 if years > 0 else float("nan")
        dd = float((nav / nav.cummax() - 1.0).min())
        return {"total": total, "cagr": cagr, "max_dd": dd}

    qqq_y, spy_y = _bench_yearly("QQQ"), _bench_yearly("SPY")
    strategy_y = {}
    for y in sorted(set(eq.index.year)):
        seg = eq[eq.index.year == y]
        if len(seg) < 2:
            continue
        prev = eq[eq.index.year < y]
        start = float(prev.iloc[-1]) if len(prev) else float(eq.iloc[0])
        strategy_y[y] = float(seg.iloc[-1] / start - 1.0)

    bench_yearly = []
    for y in sorted(set(strategy_y) | set(qqq_y) | set(spy_y)):
        s = strategy_y.get(y)
        q = qqq_y.get(y)
        p = spy_y.get(y)
        bench_yearly.append({
            "year": y,
            "strategy": s,
            "qqq": q,
            "spy": p,
            "ex_qqq": (s - q) if s is not None and q is not None else None,
            "ex_spy": (s - p) if s is not None and p is not None else None,
        })

    # 相关标的的 3M 动量（给月报写「为什么进/出」用）
    mom_map = {}
    for sym in list(weights.keys()) + list(strategy.stock_universe.keys()):
        df = all_data.get(sym)
        if df is None or len(df) < strategy.mom_lookback + 3:
            continue
        close = df["close"]
        mom_map[sym] = float(close.iloc[-1] / close.iloc[-strategy.mom_lookback] - 1.0)

    return {
        "as_of": as_of,
        "strategy": strategy,
        "weights": weights,
        "tiger_w": tiger_w,
        "domestic_w": domestic_w,
        "brake_action": brake_action,
        "current_dd": current_dd,
        "nav": float(eq.iloc[-1]),
        "equity_curve": eq,
        "weight_history": result.weight_history,
        "name_map": GROWTH_UNIVERSE,
        "bench_yearly": bench_yearly,
        "bench_total": {
            "QQQ": _bench_total("QQQ"),
            "SPY": _bench_total("SPY"),
        },
        "mom_map": mom_map,
    }


def write_target_weights(alloc: dict) -> Path:
    """把调仓单落到 data/target_weights/YYYY-MM-DD/。"""
    as_of = alloc["as_of"]
    strategy = alloc["strategy"]
    weights = alloc["weights"]
    tiger_w = alloc["tiger_w"]
    domestic_w = alloc["domestic_w"]
    brake_action = alloc["brake_action"]
    current_dd = alloc["current_dd"]
    name_map = alloc["name_map"]

    out_dir = Path(__file__).parent.parent / "data" / "target_weights" / as_of.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    tiger_payload = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "strategy": strategy.name,
        "portfolio_dd_brake": {
            "enabled": False,
            "note": "回撤预算 −40%，无自动刹车（A2 对个股动量会死锁空仓）",
            "current_dd": round(current_dd, 4),
            "action": brake_action,
        },
        "max_weight": strategy.per_name_cap,
        "weights": {s: round(w, 4) for s, w in sorted(tiger_w.items(), key=lambda x: -x[1])},
        "cash_proxy": CASH_PROXY,
        "note": "权重按账户总资金缩放；SHY=闲钱现金代理",
    }
    (out_dir / "tiger.json").write_text(
        json.dumps(tiger_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = ["symbol,name,weight"]
    for s, w in sorted(domestic_w.items(), key=lambda x: -x[1]):
        lines.append(f"{s},{name_map.get(s, s)},{w:.4f}")
    if not domestic_w:
        lines.append("# 本月境内账户无目标仓位（保持现金/货基）")
    (out_dir / "domestic.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    md = [
        f"# 月度调仓单 {as_of.strftime('%Y-%m-%d')}",
        "",
        f"- 策略：{strategy.name}（3M 动量 top{strategy.top_n}，单票顶 {strategy.per_name_cap:.0%}）",
        f"- 风控：无自动刹车；回撤预算 **−40%**（人工扛，不因回撤砍仓）",
        f"- 模型净值：{alloc['nav']:.3f}（起点 1.0）",
        f"- 模型当前回撤：{current_dd * 100:.1f}%",
        f"- 本次风控动作：**{brake_action}**",
    ]
    if brake_action != "none":
        md.append("- ⚠️ 已按当前回撤减仓，不要用上月满仓单覆盖")
    md += ["", "## 老虎账户（今晚开盘后）", ""]
    for s, w in tiger_payload["weights"].items():
        md.append(f"- {s}: {w:.1%}")
    md += ["", "## 境内账户（次日开盘前）", ""]
    if domestic_w:
        for s, w in sorted(domestic_w.items(), key=lambda x: -x[1]):
            md.append(f"- {s} {name_map.get(s, s)}: {w:.1%}")
    else:
        md.append("- 无目标仓位，保持现金/货基")
    md += ["", "## 执行检查", "", "- [ ] 两账户各自单票 ≤ 20%", "- [ ] 未配满部分进短债/货基（SHY 或境内货基）", "- [ ] 记录成交价到 data/live_trades.csv"]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"已导出 → {out_dir}")
    print(f"老虎账户 {len(tiger_w)} 个标的，境内账户 {len(domestic_w)} 个标的")
    for s, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {s:8s} {w:6.1%}")
    return out_dir


def main() -> None:
    alloc = build_monthly_allocation()
    write_target_weights(alloc)


if __name__ == "__main__":
    main()
