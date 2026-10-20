"""月度目标权重导出 — 双账户路径 1（信号统一，执行拆分）

用法（每月第一个交易日收盘后）:
    python3 scripts/export_target_weights.py

输出到 data/target_weights/YYYY-MM-DD/:
    tiger.json   — 老虎/OpenAPI 可吃的美股腿
    domestic.csv — 境内券商手动/QMT 腿
    summary.md   — 人类可读的本月调仓单

执行约定：
    - A 股腿：次日开盘前下单
    - 美股腿：当晚开盘后下单
    - 两账户按各自资金独立等比缩放权重；单票 20% 顶各账户内生效
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.manager import DataManager
from core.data.config import STRATEGY_UNIVERSE, CASH_PROXY
from core.strategy.strategies import DualMomentumKellyStrategy
from core.backtest.engine import BacktestEngine

# 双账户拆分（路径 1）
TIGER_SYMBOLS = [s for s in STRATEGY_UNIVERSE if s not in ("399006", "000688", "000300", "日经225", "KOSPI")]
DOMESTIC_SYMBOLS = ["000300", "399006", "000688"]
# 日韩无境内干净标的：在老虎用区域 ETF 替代
TIGER_PROXY = {"日经225": "EWJ", "KOSPI": "EWY"}


def main() -> None:
    dm = DataManager()
    end = datetime.now().strftime("%Y-%m-%d")
    strategy = DualMomentumKellyStrategy()

    # 回放完整历史，让组合回撤刹车（-15%/-20% 月频）用真实净值路径定档
    all_data = {}
    for symbol in STRATEGY_UNIVERSE:
        df = dm.load(symbol, "2017-06-01", end)
        if not df.empty:
            all_data[symbol] = df

    result = BacktestEngine(commission_rate=0.001).run(
        strategy,
        all_data,
        start_date="2019-01-01",
        end_date=end,
        portfolio_dd_brake=True,
        dd_halve=strategy.dd_halve,
        dd_flatten=strategy.dd_flatten,
        dd_restore=strategy.dd_restore,
        dd_brake_intraday=strategy.dd_brake_intraday,
        rebalance_freq="M",
    )
    if result.equity_curve.empty:
        print("无权重输出（数据不足）")
        return

    # 用「今天」的信号，而不是上月再平衡日的权重（月频刹车最多滞后一个月）
    as_of = result.equity_curve.index[-1]
    raw = strategy.generate_weights(as_of, all_data)
    raw = {s: float(w) for s, w in (raw or {}).items() if float(w) > 1e-6}

    # 用模型真实净值算当前 DD，按 A2 定档（月频规则，但出单日强制对齐）
    eq = result.equity_curve
    current_dd = float(eq.iloc[-1] / eq.cummax().iloc[-1] - 1.0)
    if current_dd <= strategy.dd_flatten:
        weights = {}
        brake_action = "flatten"
    elif current_dd <= strategy.dd_halve:
        weights = {
            s: (0.0 if s == CASH_PROXY else w * 0.5) for s, w in raw.items()
        }
        weights = {s: w for s, w in weights.items() if w > 1e-6}
        brake_action = "halve"
    else:
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
        elif sym in TIGER_SYMBOLS or sym == CASH_PROXY:
            tiger_w[sym] = w

    out_dir = Path(__file__).parent.parent / "data" / "target_weights" / as_of.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    tiger_payload = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "strategy": strategy.name,
        "portfolio_dd_brake": {
            "enabled": True,
            "dd_halve": strategy.dd_halve,
            "dd_flatten": strategy.dd_flatten,
            "freq": "M",
            "current_dd": round(current_dd, 4),
            "action": brake_action,
        },
        "max_weight": strategy.max_weight,
        "weights": {s: round(w, 4) for s, w in sorted(tiger_w.items(), key=lambda x: -x[1])},
        "cash_proxy": CASH_PROXY,
        "note": "权重按账户总资金缩放；SHY=闲钱现金代理",
    }
    (out_dir / "tiger.json").write_text(
        json.dumps(tiger_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = ["symbol,name,weight"]
    name_map = STRATEGY_UNIVERSE
    for s, w in sorted(domestic_w.items(), key=lambda x: -x[1]):
        lines.append(f"{s},{name_map.get(s, s)},{w:.4f}")
    if not domestic_w:
        lines.append("# 本月境内腿无目标仓位（保持现金/货基）")
    (out_dir / "domestic.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    md = [
        f"# 月度调仓单 {as_of.strftime('%Y-%m-%d')}",
        "",
        f"- 策略：{strategy.name}（k={strategy.kelly_k}, 单票顶 {strategy.max_weight:.0%}）",
        f"- 风控：回撤 {strategy.dd_halve:.0%} 减半 / {strategy.dd_flatten:.0%} 清仓（月频）",
        f"- 模型净值：{result.equity_curve.iloc[-1]:.3f}（起点 1.0）",
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


if __name__ == "__main__":
    main()
