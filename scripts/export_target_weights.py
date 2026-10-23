"""月度目标权重导出 — 双账户路径 1（信号统一，执行拆分）

生产策略：成长动量·个股（StockMomentumGrowthStrategy，2026-10 切换）。
与飞书月报共用 build_monthly_allocation，月报只输出这一套方案。

用法（每月第一个交易日收盘后）:
    python3 scripts/export_target_weights.py

输出到 data/target_weights/YYYY-MM-DD/:
    tiger.json   — 老虎/OpenAPI 可吃的美股腿
    domestic.csv — 境内券商手动/QMT 腿
    summary.md   — 人类可读的本月调仓单

执行约定：
    - 本策略全为美股个股，境内腿通常为空（保持货基）
    - 美股腿：当晚开盘后下单
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
# 本策略持仓全为美股个股 → 老虎；境内腿通常为空
DOMESTIC_SYMBOLS = list(CN_SYMBOLS.keys())
TIGER_PROXY = {"日经225": "EWJ", "KOSPI": "EWY"}


def build_monthly_allocation(end: Optional[str] = None) -> dict:
    """计算本月目标权重 + 近一年权重路径，供导出脚本和飞书月报共用。"""
    dm = DataManager()
    end = end or datetime.now().strftime("%Y-%m-%d")
    # 生产策略：成长动量·个股（2026-10 切换）。3M 动量 top12 满仓
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
        lines.append("# 本月境内腿无目标仓位（保持现金/货基）")
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
    md += ["", "## 老虎腿（今晚开盘后）", ""]
    for s, w in tiger_payload["weights"].items():
        md.append(f"- {s}: {w:.1%}")
    md += ["", "## 境内腿（次日开盘前）", ""]
    if domestic_w:
        for s, w in sorted(domestic_w.items(), key=lambda x: -x[1]):
            md.append(f"- {s} {name_map.get(s, s)}: {w:.1%}")
    else:
        md.append("- 无目标仓位，保持现金/货基")
    md += ["", "## 执行检查", "", "- [ ] 两账户各自单票 ≤ 20%", "- [ ] 未配满部分进短债/货基（SHY 或境内货基）", "- [ ] 记录成交价到 data/live_trades.csv"]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"已导出 → {out_dir}")
    print(f"老虎腿 {len(tiger_w)} 个标的，境内腿 {len(domestic_w)} 个标的")
    for s, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {s:8s} {w:6.1%}")
    return out_dir


def main() -> None:
    alloc = build_monthly_allocation()
    write_target_weights(alloc)


if __name__ == "__main__":
    main()
